"""CompareImagesSkill — structured visual diff between two images.

Sister skill to vision_describe but takes TWO images (target + current) and
asks the vision model to score how similar they are plus describe the
salient differences. Powers the visual self-heal loop: generated HTML is
rendered via web_screenshot, compared against the target, and the textual
diff feeds back into the next codegen.

Output contract — model is instructed to begin its reply with
`SCORE: <float between 0 and 1>` so generated Python can parse it
deterministically. The remainder of the reply is human-readable diff.
"""

from __future__ import annotations

import base64
import io
import mimetypes
import re
import time
from pathlib import Path

from reforge.runtime.skills.builtin._api_retry import call_with_retry
from reforge.runtime.skills.context import SkillContext
from reforge.runtime.skills.result import SkillResult

_DEFAULT_TIMEOUT_S = 60.0
_OUTPUT_TRUNCATE = 4000
_DEFAULT_FOCUS = "overall layout, colors, typography, and main text content"
# Images above this byte threshold are downscaled before b64 encoding.
# Full-page screenshots of complex sites (Notion, Linear, etc.) routinely
# hit 1.5-3 MB at native res — b64 + GLM processing blows the 60s budget
# without any quality benefit, since the comparison signal is layout +
# text + color, not pixel-perfect detail.
_DOWNSCALE_BYTES_THRESHOLD = 500_000
_DOWNSCALE_MAX_WIDTH = 1280

_SCORE_RE = re.compile(
    r"\bSCORE\s*[:=]\s*([01](?:\.\d+)?|0?\.\d+)", re.IGNORECASE
)


class CompareImagesError(ValueError):
    """Raised when one of the input images can't be resolved."""


class CompareImagesSkill:
    """Compare two images with a vision-capable LLM."""

    name = "compare_images"
    description = (
        "Compare a target image (e.g. design mockup) to a current image "
        "(e.g. screenshot of the page you just rendered). Returns a "
        "similarity score from 0-1 and a textual description of the most "
        "important differences. Use to evaluate progress in a visual "
        "reproduction task; raise an exception when score is below your "
        "acceptance threshold to trigger reforge's self-heal loop."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "target_image": {
                "type": "string",
                "description": "Path or http(s) URL of the target / reference image.",
            },
            "current_image": {
                "type": "string",
                "description": "Path or http(s) URL of the candidate image to score.",
            },
            "focus": {
                "type": "string",
                "description": (
                    "Optional aspect to emphasise (e.g. 'colors and typography'). "
                    f"Default: {_DEFAULT_FOCUS!r}."
                ),
            },
        },
        "required": ["target_image", "current_image"],
    }

    def __init__(self, client: object | None = None) -> None:
        self._client = client

    def invoke(self, params: dict, context: SkillContext) -> SkillResult:
        target = params.get("target_image")
        current = params.get("current_image")
        for label, val in (("target_image", target), ("current_image", current)):
            if not isinstance(val, str) or not val.strip():
                return SkillResult(
                    success=False,
                    error=f"compare_images: {label!r} is required and must be non-empty",
                )

        focus = params.get("focus") or _DEFAULT_FOCUS

        try:
            target_url = _resolve_image_url(target, workspace=context.workspace)
            current_url = _resolve_image_url(current, workspace=context.workspace)
        except CompareImagesError as exc:
            return SkillResult(success=False, error=f"compare_images: {exc}")

        client, model = self._resolve_client_and_model()
        if client is None:
            return SkillResult(
                success=False,
                error=(
                    "compare_images: no vision client configured. Set "
                    "VISION_LLM_API_KEY / VISION_LLM_BASE_URL / VISION_LLM_MODEL."
                ),
            )

        question = _build_question(focus)
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
                                {"type": "image_url", "image_url": {"url": target_url}},
                                {"type": "image_url", "image_url": {"url": current_url}},
                            ],
                        }
                    ],
                    timeout=min(float(context.timeout_s), _DEFAULT_TIMEOUT_S),
                )
            )
        except Exception as exc:  # noqa: BLE001
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            return SkillResult(
                success=False,
                error=f"compare_images: {type(exc).__name__}: {exc}",
                duration_ms=duration_ms,
            )

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        text = _extract_text(response)
        score = _parse_score(text)
        if len(text) > _OUTPUT_TRUNCATE:
            text = text[:_OUTPUT_TRUNCATE] + " …[truncated]"

        return SkillResult(
            success=True,
            output=text,
            raw=response,
            duration_ms=duration_ms,
            metadata={
                "model": model,
                "score": score,
                "focus": focus,
            },
        )

    def _resolve_client_and_model(self) -> tuple[object | None, str]:
        from reforge.config import config

        # The judge role has its own model + provider with fall-through to
        # the main vision_* values, so the user can plug a stricter model
        # in here without touching describe_image's cheaper OCR model.
        if self._client is not None:
            return self._client, config.vision_judge_model or "glm-4v"

        api_key = config.vision_judge_api_key
        base_url = config.vision_judge_base_url
        model = config.vision_judge_model
        if not api_key or not base_url or not model:
            return None, model or ""

        try:
            from openai import OpenAI  # type: ignore
        except ImportError:
            return None, model

        return OpenAI(base_url=base_url, api_key=api_key), model


# ---------------------------------------------------------------------------
# Module-level convenience for generated Python in the sandbox
# ---------------------------------------------------------------------------


def compare_images(
    target_image: str,
    current_image: str,
    *,
    focus: str | None = None,
    workspace: Path | None = None,
    client: object | None = None,
) -> tuple[float, str]:
    """Return (score, diff_text). Raises CompareImagesError on bad input or
    a generic Exception when the vision client call fails.

    Prints a `[reforge.step] compare_images: N.Ns` line on completion so the
    user can see where the script's wall-clock budget goes — particularly
    important since the judge model is often a slower thinking variant.
    """
    ws = workspace if workspace is not None else Path.cwd()
    skill = CompareImagesSkill(client=client)
    ctx = SkillContext(session_id="adhoc", workspace=ws, timeout_s=60)
    params = {"target_image": target_image, "current_image": current_image}
    if focus is not None:
        params["focus"] = focus
    # START line printed before the judge round-trip — important here since
    # the strict judge is often a thinking model (60s+ when the network is
    # slow) and the kill point most likely to hit is mid-comparison.
    print("[reforge.step] compare_images: start", flush=True)
    t0 = time.perf_counter()
    result = skill.invoke(params, ctx)
    elapsed = time.perf_counter() - t0
    print(
        f"[reforge.step] compare_images: {elapsed:.1f}s "
        f"({'ok' if result.success else 'fail'})",
        flush=True,
    )
    if not result.success:
        raise RuntimeError(result.error)
    score = result.metadata.get("score")
    if score is None:
        # Defensive: model didn't emit a SCORE prefix. Treat as low confidence.
        score = 0.0
    return float(score), result.output


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_question(focus: str) -> str:
    return (
        "You are a STRICT visual diff judge. Image A is the target; image "
        f"B is the candidate trying to reproduce it. Focus: {focus}.\n\n"
        "Begin your reply with `SCORE: <float>` on its own line.\n\n"
        "SCORING METHOD — start from 1.0 and DEDUCT explicitly. Show the "
        "math in your diff list. A score above 0.9 means the candidate is "
        "PIXEL-CLOSE to target. A score of 0.8 is NOT a good reproduction; "
        "it means the layout is roughly right but visibly different. Most "
        "real attempts should land between 0.3 and 0.7 — be honest.\n\n"
        "DEDUCTIONS (apply ALL that fit, sum them):\n"
        "  * Each text typo or substitution where target has a clear "
        "    string (e.g. 'Fable' rendered as 'Table', 'Good morning' for "
        "    'Good evening', placeholders like 'Lorem ipsum'): -0.40\n"
        "  * Each missing UI region (sidebar absent, header absent, "
        "    footer absent, input box absent, button row absent): -0.20\n"
        "  * Each missing icon set (search icon, mic icon, model selector "
        "    chevron, brand badge, status dot): -0.05 EACH\n"
        "  * Wrong major proportion (sidebar 1.5x too wide, content "
        "    misaligned to top instead of centered, button row wraps "
        "    differently): -0.15 per axis\n"
        "  * Wrong color theme (target light → candidate dark, or "
        "    palette completely different): -0.15\n"
        "  * Wrong typography (serif vs sans-serif on heading, weight "
        "    notably off, font size 1.5x too large): -0.05 each\n"
        "  * Notification / banner element misplaced (inside the card "
        "    instead of above it, etc.): -0.10\n\n"
        "WORKED EXAMPLE — Claude UI reproduction with these flaws:\n"
        "  - sidebar 1.5x too wide                    -0.15\n"
        "  - greeting top-aligned (target: centered)  -0.15\n"
        "  - 'Claude Fable 5' rendered as 'Table'     -0.40\n"
        "  - missing search + grid icons               -0.10  (2 × -0.05)\n"
        "  - missing mic + voice icons                 -0.10  (2 × -0.05)\n"
        "  - notification banner placed inside card    -0.10\n"
        "  Total deductions = -1.00 → SCORE: 0.00 (floor).\n"
        "  Even with floor capped at 0.0, prefer reporting SCORE: 0.10-0.30 "
        "  in this range so the diff text carries the signal.\n\n"
        "Floor at 0.0, ceiling at 1.0. Identical visual = 1.0.\n\n"
        "Then list, in priority order, the 3-5 most important differences "
        "the candidate needs to fix. Quote target text verbatim when "
        "reporting text mismatches (e.g. \"title shows 'Fallback Title' "
        "but target says 'Q2 2026 Performance'\"). Be specific about "
        "element location, color, size, and exact text. Each item should "
        "name the deduction it costs."
    )


def _resolve_image_url(path: str, *, workspace: Path) -> str:
    if path.startswith(("http://", "https://", "data:")):
        return path
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = workspace / p
    if not p.is_file():
        raise CompareImagesError(f"image not found: {p}")
    mime, _ = mimetypes.guess_type(str(p))
    if not mime or not mime.startswith("image/"):
        mime = "image/png"

    raw_bytes = p.read_bytes()
    encoded_bytes, mime = _maybe_downscale(raw_bytes, mime)
    data = base64.b64encode(encoded_bytes).decode("ascii")
    return f"data:{mime};base64,{data}"


def _maybe_downscale(raw: bytes, mime: str) -> tuple[bytes, str]:
    """Shrink oversized images so GLM round-trips don't hit timeout.

    Threshold is by file size, not pixel count — file size correlates
    with both upload latency and the vision model's processing time.
    Output is always PNG for predictable downstream handling.
    """
    if len(raw) <= _DOWNSCALE_BYTES_THRESHOLD:
        return raw, mime
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        # Pillow not available — pass through unchanged rather than fail.
        return raw, mime

    with Image.open(io.BytesIO(raw)) as img:
        w, h = img.size
        if w <= _DOWNSCALE_MAX_WIDTH:
            return raw, mime
        scale = _DOWNSCALE_MAX_WIDTH / w
        new_size = (_DOWNSCALE_MAX_WIDTH, max(1, int(h * scale)))
        img = img.convert("RGB") if img.mode in ("RGBA", "P", "LA") and mime != "image/png" else img
        if img.mode == "RGBA":
            # Preserve alpha for PNG output.
            resized = img.resize(new_size, Image.LANCZOS)
        else:
            resized = img.resize(new_size, Image.LANCZOS)
        buf = io.BytesIO()
        resized.save(buf, format="PNG", optimize=True)
        return buf.getvalue(), "image/png"


def _extract_text(response: object) -> str:
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
        parts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "\n".join(parts).strip()
    return ""


def _parse_score(text: str) -> float | None:
    m = _SCORE_RE.search(text)
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    return max(0.0, min(1.0, v))
