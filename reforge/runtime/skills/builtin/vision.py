"""VisionDescribeSkill — tool-call boundary for image understanding.

The runtime's primary LLM (DeepSeek, etc.) is text-only. This skill is the
seam where image input becomes text the primary LLM can reason over: a
vision-capable model (Zhipu GLM-4.6V by default, but any OpenAI-compatible
vision endpoint works) reads the image and returns a description scoped
by an optional question.

Why a skill and not a CLI flag: the planning LLM can call it multiple
times in one task ("re-look at the screenshot, focus on the legend"),
emit it through the same governor + memory + events as any other tool,
and chain its output into subsequent code-gen steps.
"""

from __future__ import annotations

import base64
import mimetypes
import time
from pathlib import Path

from reforge.runtime.skills.builtin._api_retry import call_with_retry
from reforge.runtime.skills.context import SkillContext
from reforge.runtime.skills.result import SkillResult

_DEFAULT_QUESTION = (
    "Describe what is shown in this image in detail. If there are charts, "
    "tables, code, error messages, or UI elements, transcribe their content."
)
_DEFAULT_TIMEOUT_S = 60.0
_OUTPUT_TRUNCATE = 4000


class VisionDescribeSkill:
    """Describe an image via a vision-capable LLM and return text.

    Image input can be a local filesystem path (PNG / JPG / WebP / GIF) or
    a remote `http(s)://` URL. Local files are read once and inlined as a
    base64 data URL so the remote provider never sees a local path.
    """

    name = "vision_describe"
    description = (
        "Describe the contents of an image. Accepts a local file path or an "
        "http(s) URL. Optionally pass `question` to focus the description "
        "(e.g. 'what error is shown', 'what columns does this chart have'). "
        "Use this whenever the task references a screenshot, chart, UI mockup, "
        "or any other image the runtime cannot read as text directly."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "image_path": {
                "type": "string",
                "description": "Local file path or http(s) URL of the image.",
            },
            "question": {
                "type": "string",
                "description": (
                    "Optional focus question. When omitted, a general "
                    "description is returned."
                ),
            },
        },
        "required": ["image_path"],
    }
    prompt_fragment = (
        "When the LLM already sees the image (vision codegen path), skip "
        "this — it's a redundant round-trip. Use it when reasoning over "
        "an image with a text-only LLM, or when a region is too small to "
        "read with confidence even with vision. Pass `question` to focus "
        "the description (e.g. \"enumerate every visible text region from "
        "top to bottom\"). The return value is FREE-FORM text; do NOT "
        "parse it with regex, json.loads, or any structured extractor — "
        "transcribe the strings you see in the description literally into "
        "downstream code (HTML, prompts, etc.)."
    )

    def __init__(self, client: object | None = None) -> None:
        """Inject a custom OpenAI-compatible client for testing; otherwise
        the client is built lazily from VISION_LLM_* config at invoke time."""
        self._client = client

    # ------------------------------------------------------------------

    def invoke(self, params: dict, context: SkillContext) -> SkillResult:
        image_path = params.get("image_path")
        if not isinstance(image_path, str) or not image_path.strip():
            return SkillResult(
                success=False,
                error="vision_describe: 'image_path' is required and must be non-empty",
            )
        question = params.get("question") or _DEFAULT_QUESTION

        try:
            image_url = _resolve_image_url(image_path)
        except VisionInputError as exc:
            return SkillResult(success=False, error=f"vision_describe: {exc}")

        client, model = self._resolve_client_and_model()
        if client is None:
            return SkillResult(
                success=False,
                error=(
                    "vision_describe: no vision client configured. Set "
                    "VISION_LLM_API_KEY / VISION_LLM_BASE_URL / VISION_LLM_MODEL "
                    "(or pass a client= for tests)."
                ),
            )

        start = time.perf_counter()
        try:
            response = call_with_retry(
                lambda: client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": question},
                                {"type": "image_url", "image_url": {"url": image_url}},
                            ],
                        }
                    ],
                    timeout=min(float(context.timeout_s), _DEFAULT_TIMEOUT_S),
                )
            )
        except Exception as exc:  # noqa: BLE001 — remote API failure surfaces as skill error
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            return SkillResult(
                success=False,
                error=f"vision_describe: {type(exc).__name__}: {exc}",
                duration_ms=duration_ms,
            )

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        text = _extract_text(response)
        if len(text) > _OUTPUT_TRUNCATE:
            text = text[:_OUTPUT_TRUNCATE] + " …[truncated]"

        return SkillResult(
            success=True,
            output=text,
            raw=response,
            duration_ms=duration_ms,
            metadata={
                "model": model,
                "image_source": "url" if image_path.startswith(("http://", "https://")) else "local",
                "question_was_default": params.get("question") is None,
            },
        )

    # ------------------------------------------------------------------

    def _resolve_client_and_model(self) -> tuple[object | None, str]:
        if self._client is not None:
            from reforge.config import config
            return self._client, config.vision_model or "glm-4v"

        from reforge.config import config
        api_key = config.vision_api_key
        base_url = config.vision_base_url
        model = config.vision_model
        if not api_key or not base_url or not model:
            return None, model or ""

        try:
            from openai import OpenAI  # type: ignore
        except ImportError:
            return None, model

        return OpenAI(base_url=base_url, api_key=api_key), model


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class VisionInputError(ValueError):
    """Raised when image_path can't be resolved to something a vision API accepts."""


def _resolve_image_url(image_path: str) -> str:
    """Return a value suitable for the `image_url.url` field.

    URLs pass through unchanged. Local paths are read and inlined as a
    ``data:<mime>;base64,...`` URL.
    """
    if image_path.startswith(("http://", "https://", "data:")):
        return image_path

    path = Path(image_path).expanduser()
    if not path.is_file():
        raise VisionInputError(f"image not found: {image_path}")

    mime, _ = mimetypes.guess_type(str(path))
    if not mime or not mime.startswith("image/"):
        # Default to png for unknown extensions; the provider will usually accept it.
        mime = "image/png"

    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


# ---------------------------------------------------------------------------
# Module-level convenience for generated Python in the sandbox
# ---------------------------------------------------------------------------


def describe_image(
    image_path: str,
    *,
    question: str | None = None,
    workspace: Path | None = None,
    client: object | None = None,
) -> str:
    """Return a plain-text description of an image via the vision LLM.

    Mirrors the pattern of `compare_images()` and `screenshot()` so generated
    Python can `from reforge.helpers import describe_image` and call it
    directly. Raises RuntimeError on skill failure so the runtime's reflect
    loop sees the cause.

    Prints a `[reforge.step] describe_image: N.Ns` line on completion so the
    user can see where the script's wall-clock budget goes.
    """
    ws = workspace if workspace is not None else Path.cwd()
    skill = VisionDescribeSkill(client=client)
    ctx = SkillContext(session_id="adhoc", workspace=ws, timeout_s=60)
    params: dict = {"image_path": image_path}
    if question is not None:
        params["question"] = question
    # START line printed before the (potentially long) vision API round-trip
    # so the user can see what's running even if the subprocess is killed
    # before the operation returns.
    print("[reforge.step] describe_image: start", flush=True)
    t0 = time.perf_counter()
    result = skill.invoke(params, ctx)
    elapsed = time.perf_counter() - t0
    print(
        f"[reforge.step] describe_image: {elapsed:.1f}s "
        f"({'ok' if result.success else 'fail'})",
        flush=True,
    )
    if not result.success:
        raise RuntimeError(result.error)
    return result.output


def _extract_text(response: object) -> str:
    """Pull the text content out of a chat-completions response.

    Tolerant to both the openai-python SDK shape (objects) and a plain dict
    so tests can pass a minimal fake without mocking the whole SDK.
    """
    try:
        choices = getattr(response, "choices", None) or response["choices"]  # type: ignore[index]
        first = choices[0]
        message = getattr(first, "message", None) or first["message"]
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""

    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        # OpenAI sometimes returns content as a list of parts
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "\n".join(parts).strip()
    return ""
