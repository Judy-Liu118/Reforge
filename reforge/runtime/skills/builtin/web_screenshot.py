"""WebScreenshotSkill — render a web page or local HTML and save a PNG.

Why a skill: lets the runtime's primary code-gen produce HTML, render it,
and feed the screenshot back to a vision model for visual self-heal — the
front-end-from-mockup loop. Without rendering, the runtime would just emit
HTML once and trust the LLM that it looks right.

The module also exposes a `screenshot(source, output_path)` function so
generated Python code can call it directly from inside the sandbox:

    from reforge.runtime.skills.builtin.web_screenshot import screenshot
    screenshot("index.html", "current.png")

Source can be:
  * A local file path (relative to cwd or absolute) — converted to file:// URL
  * A file:// URL — passed through
  * An http(s):// URL — fetched live
"""

from __future__ import annotations

import time
from pathlib import Path

from reforge.runtime.skills.context import SkillContext
from reforge.runtime.skills.result import SkillResult

_DEFAULT_VIEWPORT = (1280, 800)
_DEFAULT_WAIT_MS = 500
# full_page True snapshots the entire scroll height. Heavy pages
# (Wikipedia Main_Page, Notion landing) produce a 6000+ px tall image that
# multiplies downstream vision API latency 2-3x because the image is
# physically larger to encode and reason about. Viewport-only (False) is
# what users see "above the fold" and is the right semantics for
# "replicate this page" tasks; callers can override per-call when they
# specifically want the entire scroll.
_DEFAULT_FULL_PAGE = False
# Playwright's default headless user-agent contains "HeadlessChrome", which
# anti-bot guards (Trello, Notion, Cloudflare-protected sites) flag as a
# bot and either 403 it, redirect-loop it, or sandbag the `load` event.
# We ship a realistic recent-Chrome UA so the bot guard treats us like a
# normal desktop browser. Caller can override via user_agent kwarg.
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
# Navigation timeout. Heavy sites (Trello, Notion, anything with marketing
# pixels / tracking) routinely take 30s+ to finish all subresource loads —
# 60s is the new floor. Caller can override via nav_timeout_ms.
_DEFAULT_TIMEOUT_MS = 60_000
# wait_until="load" blocks until EVERY subresource (ads, fonts, analytics)
# has resolved — pages with even one slow tracker hang it out indefinitely.
# domcontentloaded returns once the HTML is parsed and inline scripts ran,
# which is the right moment for "what does this page look like" capture.
# Caller can override via wait_until kwarg.
_DEFAULT_WAIT_UNTIL = "domcontentloaded"


class WebScreenshotError(RuntimeError):
    """Raised when rendering fails (missing source, browser launch, etc.)."""


class WebScreenshotSkill:
    """Render a page and save a screenshot — Playwright headless Chromium."""

    name = "web_screenshot"
    description = (
        "Render a web page or local HTML file and save a full-page PNG "
        "screenshot. Use when reproducing a visual design: render the "
        "generated HTML, then compare the screenshot with the target image. "
        "Source can be a local HTML file path, a file:// URL, or an "
        "http(s):// URL."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": "Local HTML file path, file:// URL, or http(s) URL.",
            },
            "output_path": {
                "type": "string",
                "description": "Where to save the PNG screenshot.",
            },
            "viewport_width": {"type": "integer", "default": _DEFAULT_VIEWPORT[0]},
            "viewport_height": {"type": "integer", "default": _DEFAULT_VIEWPORT[1]},
            "wait_ms": {
                "type": "integer",
                "default": _DEFAULT_WAIT_MS,
                "description": (
                    "Extra wait after page load before capturing — give "
                    "fonts and CSS animations time to settle."
                ),
            },
            "nav_timeout_ms": {
                "type": "integer",
                "default": _DEFAULT_TIMEOUT_MS,
                "description": "Per-page navigation timeout (default 60s).",
            },
            "wait_until": {
                "type": "string",
                "default": _DEFAULT_WAIT_UNTIL,
                "description": (
                    "Playwright wait_until: 'domcontentloaded' (default), "
                    "'load', 'networkidle', or 'commit'."
                ),
            },
            "user_agent": {
                "type": "string",
                "default": _DEFAULT_USER_AGENT,
                "description": (
                    "Browser UA. Default mimics desktop Chrome to slip past "
                    "anti-bot guards that block headless browsers."
                ),
            },
            "full_page": {"type": "boolean", "default": _DEFAULT_FULL_PAGE},
        },
        "required": ["source", "output_path"],
    }

    def __init__(self, playwright_factory=None) -> None:
        """Optionally inject a fake Playwright factory for testing.

        The real factory is lazily imported at invoke time so the skill can
        live in the codebase even when playwright isn't installed.
        """
        self._playwright_factory = playwright_factory

    def invoke(self, params: dict, context: SkillContext) -> SkillResult:
        source = params.get("source")
        output_path = params.get("output_path")
        if not isinstance(source, str) or not source.strip():
            return SkillResult(
                success=False,
                error="web_screenshot: 'source' is required and must be non-empty",
            )
        if not isinstance(output_path, str) or not output_path.strip():
            return SkillResult(
                success=False,
                error="web_screenshot: 'output_path' is required and must be non-empty",
            )

        viewport = (
            int(params.get("viewport_width", _DEFAULT_VIEWPORT[0])),
            int(params.get("viewport_height", _DEFAULT_VIEWPORT[1])),
        )
        wait_ms = int(params.get("wait_ms", _DEFAULT_WAIT_MS))
        nav_timeout_ms = int(params.get("nav_timeout_ms", _DEFAULT_TIMEOUT_MS))
        wait_until = str(params.get("wait_until", _DEFAULT_WAIT_UNTIL))
        user_agent = str(params.get("user_agent") or _DEFAULT_USER_AGENT)
        full_page = bool(params.get("full_page", _DEFAULT_FULL_PAGE))

        try:
            url = _resolve_source_to_url(source, workspace=context.workspace)
        except WebScreenshotError as exc:
            return SkillResult(success=False, error=f"web_screenshot: {exc}")

        out = Path(output_path)
        if not out.is_absolute():
            out = context.workspace / out
        out.parent.mkdir(parents=True, exist_ok=True)

        start = time.perf_counter()
        try:
            _render(
                url=url,
                output_path=out,
                viewport=viewport,
                wait_ms=wait_ms,
                nav_timeout_ms=nav_timeout_ms,
                wait_until=wait_until,
                user_agent=user_agent,
                full_page=full_page,
                playwright_factory=self._playwright_factory,
            )
        except Exception as exc:  # noqa: BLE001 — surface to caller as skill error
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            return SkillResult(
                success=False,
                error=f"web_screenshot: {type(exc).__name__}: {exc}",
                duration_ms=duration_ms,
            )

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        size = out.stat().st_size if out.exists() else 0
        return SkillResult(
            success=True,
            output=str(out),
            duration_ms=duration_ms,
            metadata={
                "url": url,
                "output_path": str(out),
                "bytes": size,
                "viewport": list(viewport),
            },
        )


# ---------------------------------------------------------------------------
# Helpers — also used by the module-level screenshot() convenience function
# ---------------------------------------------------------------------------


def _resolve_source_to_url(source: str, *, workspace: Path) -> str:
    """Map a user-supplied source to a URL Playwright can load."""
    if source.startswith(("http://", "https://", "file://")):
        return source

    p = Path(source).expanduser()
    if not p.is_absolute():
        p = workspace / p
    if not p.is_file():
        raise WebScreenshotError(f"source file not found: {p}")
    return p.resolve().as_uri()


def _render(
    *,
    url: str,
    output_path: Path,
    viewport: tuple[int, int],
    wait_ms: int,
    nav_timeout_ms: int,
    wait_until: str,
    user_agent: str,
    full_page: bool,
    playwright_factory,
) -> None:
    """Drive Playwright (or an injected fake) to capture one screenshot."""
    if playwright_factory is None:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except ImportError as exc:
            raise WebScreenshotError(
                "playwright not installed. Run `pip install playwright && "
                "playwright install chromium` first."
            ) from exc
        factory = sync_playwright
    else:
        factory = playwright_factory

    with factory() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(
                viewport={"width": viewport[0], "height": viewport[1]},
                user_agent=user_agent,
            )
            page = ctx.new_page()
            page.goto(url, wait_until=wait_until, timeout=nav_timeout_ms)
            if wait_ms > 0:
                page.wait_for_timeout(wait_ms)
            page.screenshot(path=str(output_path), full_page=full_page)
        finally:
            browser.close()


def screenshot(
    source: str,
    output_path: str,
    *,
    viewport_width: int = _DEFAULT_VIEWPORT[0],
    viewport_height: int = _DEFAULT_VIEWPORT[1],
    wait_ms: int = _DEFAULT_WAIT_MS,
    nav_timeout_ms: int = _DEFAULT_TIMEOUT_MS,
    wait_until: str = _DEFAULT_WAIT_UNTIL,
    user_agent: str = _DEFAULT_USER_AGENT,
    full_page: bool = _DEFAULT_FULL_PAGE,
    workspace: Path | None = None,
) -> str:
    """Module-level convenience for generated Python code inside the sandbox.

    Returns the absolute path of the written PNG. Raises WebScreenshotError
    if the source can't be resolved or rendering fails — surfacing as a
    Python exception so the runtime's reflection node sees it and the
    self-heal loop kicks in.

    Prints a `[reforge.step] screenshot: N.Ns` line on completion so the
    user can see how much of the wall-clock budget went to chromium load.
    """
    ws = workspace if workspace is not None else Path.cwd()
    url = _resolve_source_to_url(source, workspace=ws)
    out = Path(output_path)
    if not out.is_absolute():
        out = ws / out
    out.parent.mkdir(parents=True, exist_ok=True)
    # Tag the URL flavour so users can tell URL fetch from local render.
    src_tag = "url" if source.startswith(("http://", "https://")) else "local"
    # Emit a START line before the (potentially long) chromium load so the
    # user can see what's running if the subprocess hits the parent timeout
    # before the finally block fires. Without this, a hang inside _render
    # leaves no trace of which step was active when the budget ran out.
    print(f"[reforge.step] screenshot ({src_tag}): start", flush=True)
    t0 = time.perf_counter()
    ok = False
    try:
        _render(
            url=url,
            output_path=out,
            viewport=(viewport_width, viewport_height),
            wait_ms=wait_ms,
            nav_timeout_ms=nav_timeout_ms,
            wait_until=wait_until,
            user_agent=user_agent,
            full_page=full_page,
            playwright_factory=None,
        )
        ok = True
    finally:
        elapsed = time.perf_counter() - t0
        print(
            f"[reforge.step] screenshot ({src_tag}): {elapsed:.1f}s "
            f"({'ok' if ok else 'fail'})",
            flush=True,
        )
    return str(out)
