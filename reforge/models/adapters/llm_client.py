"""LLM client — OpenAI-compatible with retry/backoff and observability hooks."""
from __future__ import annotations

import base64
import mimetypes
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)

from reforge.config import config
from reforge.observability.logging.logger import get_logger

# ---------------------------------------------------------------------------
# Module-level hook — fires on every LLM lifecycle event.
# Set via set_hook() to wire into runtime observability without touching nodes.
# ---------------------------------------------------------------------------

_hook: Callable[[str, dict[str, Any]], None] | None = None


def set_hook(fn: Callable[[str, dict[str, Any]], None] | None) -> None:
    """Register a global hook called with (event_type, payload) on each LLM event.

    Event types: llm_call_start, llm_call_complete, llm_call_retry, llm_call_error
    Thread-safe for reads; set once at startup before concurrent calls.
    """
    global _hook
    _hook = fn


def _emit(event_type: str, payload: dict[str, Any]) -> None:
    if _hook is not None:
        try:
            _hook(event_type, payload)
        except Exception:
            pass  # hooks must never break the call path


# Errors that warrant a retry — transient network/capacity issues.
_RETRYABLE = (RateLimitError, APITimeoutError, APIConnectionError)


class LLMClient:
    """Thin wrapper around OpenAI-compatible SDK with retry and structured logging.

    Supports DeepSeek / Qwen / OpenAI via configurable base_url.
    Retries transient errors (rate limits, timeouts, 5xx) with exponential backoff.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        """Build an OpenAI-compatible client.

        Defaults to the text-only `LLM_*` config (backwards-compatible —
        callers that pass nothing get exactly the prior behaviour). Pass
        overrides to point at a different provider/model, e.g. the vision
        codegen endpoint. See `for_vision_codegen()`.
        """
        resolved_base = base_url if base_url is not None else config.llm_base_url
        resolved_key = api_key if api_key is not None else config.llm_api_key
        resolved_model = model if model is not None else config.llm_model
        self._client = OpenAI(
            base_url=resolved_base,
            api_key=resolved_key,
        )
        self._model = resolved_model
        self._logger = get_logger()
        if not resolved_key:
            self._logger.warning("LLM API key is not configured — API calls will fail")

    @classmethod
    def for_vision_codegen(cls) -> "LLMClient":
        """Build a client wired to the vision-capable codegen endpoint.

        The vision codegen path is used when the user asks to reproduce a
        target image: codegen needs to see the pixels, not a lossy text
        transcription. Config falls through to LLM_* when the dedicated
        CODEGEN_VISION_* envs are unset — see Config docstring.
        """
        return cls(
            base_url=config.codegen_vision_base_url,
            api_key=config.codegen_vision_api_key,
            model=config.codegen_vision_model,
        )

    def chat(self, system_prompt: str, user_message: str) -> str:
        """Send a text-only chat request; returns stripped response text.

        Retries up to config.max_retry times on transient errors with
        exponential backoff (1 s, 2 s, 4 s, …).  Raises RuntimeError when
        all attempts are exhausted, or re-raises immediately on 4xx errors.
        """
        return self._dispatch(
            system_prompt=system_prompt,
            user_content=user_message,
            prompt_chars=len(system_prompt) + len(user_message),
        )

    def chat_multimodal(
        self,
        system_prompt: str,
        user_message: str,
        image_paths: Iterable[Path | str],
    ) -> str:
        """Send a chat request with one or more inline images.

        Used for visual reproduction tasks where the codegen LLM needs to
        see the target image directly. Each image is read once, base64
        encoded, and attached as an OpenAI-style `image_url` content block.
        Same retry/hook/logging behaviour as `chat()`.
        """
        content: list[dict[str, Any]] = [{"type": "text", "text": user_message}]
        attached = 0
        for raw in image_paths:
            path = Path(raw)
            if not path.is_file():
                continue
            content.append(
                {"type": "image_url", "image_url": {"url": _encode_image_data_url(path)}}
            )
            attached += 1
        if attached == 0:
            # No images resolved — fall back to text-only behaviour so the
            # caller still gets a response rather than a silent no-op.
            return self.chat(system_prompt, user_message)
        prompt_chars = len(system_prompt) + len(user_message) + sum(
            # Rough budget signal — count attached image entries as 1k chars
            # each for logging only; real token count is the SDK's problem.
            1000 for _ in range(attached)
        )
        return self._dispatch(
            system_prompt=system_prompt,
            user_content=content,
            prompt_chars=prompt_chars,
        )

    def _dispatch(
        self,
        *,
        system_prompt: str,
        user_content: str | list[dict[str, Any]],
        prompt_chars: int,
    ) -> str:
        """Run the OpenAI call with the shared retry/backoff/hook loop.

        Factored out so `chat` and `chat_multimodal` share the same
        observability + transient-error handling without duplicating it.
        """
        max_retries = config.max_retry
        last_exc: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                t0 = time.monotonic()
                _emit("llm_call_start", {
                    "model": self._model,
                    "attempt": attempt + 1,
                    "prompt_chars": prompt_chars,
                })

                response = self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    temperature=0.1,
                )

                elapsed_ms = (time.monotonic() - t0) * 1000
                content = response.choices[0].message.content
                usage = response.usage
                prompt_tokens = usage.prompt_tokens if usage else -1
                completion_tokens = usage.completion_tokens if usage else -1

                self._logger.debug(
                    "llm model=%s attempt=%d latency_ms=%.0f "
                    "prompt_tokens=%d completion_tokens=%d",
                    self._model, attempt + 1, elapsed_ms,
                    prompt_tokens, completion_tokens,
                )
                _emit("llm_call_complete", {
                    "model": self._model,
                    "attempt": attempt + 1,
                    "latency_ms": elapsed_ms,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                })
                return content.strip() if content else ""

            except _RETRYABLE as exc:
                last_exc = exc
                self._logger.warning(
                    "llm retryable error=%s attempt=%d/%d",
                    type(exc).__name__, attempt + 1, max_retries + 1,
                )
                _emit("llm_call_retry", {
                    "model": self._model,
                    "attempt": attempt + 1,
                    "error": type(exc).__name__,
                })
                if attempt < max_retries:
                    time.sleep(2 ** attempt)

            except APIStatusError as exc:
                if exc.status_code >= 500:
                    last_exc = exc
                    self._logger.warning(
                        "llm server error status=%d attempt=%d/%d",
                        exc.status_code, attempt + 1, max_retries + 1,
                    )
                    _emit("llm_call_retry", {
                        "model": self._model,
                        "attempt": attempt + 1,
                        "error": f"HTTP {exc.status_code}",
                    })
                    if attempt < max_retries:
                        time.sleep(2 ** attempt)
                else:
                    # 4xx: caller error, not retryable
                    self._logger.error(
                        "llm client error status=%d", exc.status_code
                    )
                    _emit("llm_call_error", {
                        "model": self._model,
                        "error": f"HTTP {exc.status_code}",
                    })
                    raise

        _emit("llm_call_error", {
            "model": self._model,
            "error": f"exhausted {max_retries + 1} attempts",
        })
        raise RuntimeError(
            f"LLM call failed after {max_retries + 1} attempts: {last_exc}"
        ) from last_exc


def _encode_image_data_url(path: Path) -> str:
    """Read an image and return a `data:<mime>;base64,...` URL.

    Used by `chat_multimodal` to inline images in the user content block
    without depending on the provider being able to fetch URLs. Unknown
    extensions default to image/png — the vision API will usually accept
    it and surface a 4xx if not, which the retry loop will let propagate.
    """
    mime, _ = mimetypes.guess_type(str(path))
    if not mime or not mime.startswith("image/"):
        mime = "image/png"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"
