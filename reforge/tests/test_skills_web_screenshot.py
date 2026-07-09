"""Tests for WebScreenshotSkill.

Strategy: replace the playwright factory with a hand-written fake so tests
never launch a real browser. The fake records the URL it was asked to
render and writes deterministic bytes to the output path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from reforge.runtime.skills import Skill, SkillContext
from reforge.runtime.skills.builtin import default_skill_registry
from reforge.runtime.skills.builtin.web_screenshot import (
    WebScreenshotError,
    WebScreenshotSkill,
    _resolve_source_to_url,
)


# ---------------------------------------------------------------------------
# Fake playwright that mimics the sync_playwright() context manager surface.
# ---------------------------------------------------------------------------


class _FakePage:
    def __init__(self, browser: "_FakeBrowser") -> None:
        self._browser = browser

    def goto(self, url: str, *, wait_until: str = "domcontentloaded", timeout: int = 60000) -> None:
        self._browser.gotos.append((url, wait_until, timeout))

    def wait_for_timeout(self, ms: int) -> None:
        self._browser.waits.append(ms)

    def screenshot(self, *, path: str, full_page: bool = True) -> None:
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\nfakebody")
        self._browser.screenshots.append((path, full_page))


class _FakeContext:
    def __init__(
        self,
        browser: "_FakeBrowser",
        viewport: dict[str, int],
        user_agent: str | None = None,
    ) -> None:
        self._browser = browser
        self._browser.contexts.append({"viewport": viewport, "user_agent": user_agent})

    def new_page(self) -> _FakePage:
        return _FakePage(self._browser)


class _FakeBrowser:
    def __init__(self) -> None:
        self.contexts: list[dict] = []
        self.gotos: list[tuple] = []
        self.waits: list[int] = []
        self.screenshots: list[tuple] = []
        self.closed: bool = False

    def new_context(self, *, viewport: dict[str, int], user_agent: str | None = None) -> _FakeContext:
        return _FakeContext(self, viewport, user_agent)

    def close(self) -> None:
        self.closed = True


class _FakeChromium:
    def __init__(self, owner: "_FakePlaywright") -> None:
        self._owner = owner

    def launch(self, *, headless: bool = True) -> _FakeBrowser:
        self._owner.launches.append(headless)
        self._owner.browser = _FakeBrowser()
        return self._owner.browser


class _FakePlaywright:
    def __init__(self) -> None:
        self.chromium = _FakeChromium(self)
        self.launches: list[bool] = []
        self.browser: _FakeBrowser | None = None
        self.stopped: bool = False

    def __enter__(self) -> "_FakePlaywright":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stopped = True


def _ctx(tmp_path: Path) -> SkillContext:
    return SkillContext(session_id="ws-test", workspace=tmp_path, timeout_s=10)


# ---------------------------------------------------------------------------
# Protocol + schema
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_satisfies_skill_protocol(self) -> None:
        assert isinstance(WebScreenshotSkill(playwright_factory=_FakePlaywright), Skill)

    def test_schema_requires_source_and_output(self) -> None:
        s = WebScreenshotSkill.input_schema
        assert set(s["required"]) == {"source", "output_path"}


# ---------------------------------------------------------------------------
# Source URL resolution
# ---------------------------------------------------------------------------


class TestSourceResolution:
    def test_http_passes_through(self, tmp_path: Path) -> None:
        assert _resolve_source_to_url("https://x.com/y", workspace=tmp_path) == "https://x.com/y"

    def test_file_url_passes_through(self, tmp_path: Path) -> None:
        assert _resolve_source_to_url("file:///tmp/x.html", workspace=tmp_path) == "file:///tmp/x.html"

    def test_local_html_becomes_file_url(self, tmp_path: Path) -> None:
        (tmp_path / "index.html").write_text("<h1>hi</h1>")
        url = _resolve_source_to_url("index.html", workspace=tmp_path)
        assert url.startswith("file:///")
        assert url.endswith("index.html")

    def test_missing_local_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(WebScreenshotError):
            _resolve_source_to_url("nope.html", workspace=tmp_path)


# ---------------------------------------------------------------------------
# Invoke behaviour
# ---------------------------------------------------------------------------


class TestInvoke:
    def test_renders_local_html_and_writes_png(self, tmp_path: Path) -> None:
        (tmp_path / "index.html").write_text("<h1>hi</h1>")
        skill = WebScreenshotSkill(playwright_factory=_FakePlaywright)
        out = tmp_path / "shot.png"
        result = skill.invoke(
            {"source": "index.html", "output_path": str(out)},
            _ctx(tmp_path),
        )
        assert result.success
        assert out.exists() and out.stat().st_size > 0
        assert result.metadata["bytes"] == out.stat().st_size

    def test_default_wait_until_is_domcontentloaded(self, tmp_path: Path) -> None:
        """Heavy sites with marketing pixels hang on 'load' — default must
        be 'domcontentloaded' so the screenshot returns before slow
        trackers do."""
        (tmp_path / "index.html").write_text("<h1>x</h1>")
        pw = _FakePlaywright()
        WebScreenshotSkill(playwright_factory=lambda: pw).invoke(
            {"source": "index.html", "output_path": "x.png"},
            _ctx(tmp_path),
        )
        # gotos: [(url, wait_until, timeout)]
        assert pw.browser.gotos[0][1] == "domcontentloaded"

    def test_default_nav_timeout_is_60s(self, tmp_path: Path) -> None:
        (tmp_path / "index.html").write_text("<h1>x</h1>")
        pw = _FakePlaywright()
        WebScreenshotSkill(playwright_factory=lambda: pw).invoke(
            {"source": "index.html", "output_path": "x.png"},
            _ctx(tmp_path),
        )
        assert pw.browser.gotos[0][2] == 60_000

    def test_explicit_nav_timeout_and_wait_until_propagate(self, tmp_path: Path) -> None:
        (tmp_path / "index.html").write_text("<h1>x</h1>")
        pw = _FakePlaywright()
        WebScreenshotSkill(playwright_factory=lambda: pw).invoke(
            {
                "source": "index.html",
                "output_path": "x.png",
                "nav_timeout_ms": 90_000,
                "wait_until": "networkidle",
            },
            _ctx(tmp_path),
        )
        assert pw.browser.gotos[0] == (pw.browser.gotos[0][0], "networkidle", 90_000)

    def test_passes_viewport_to_browser(self, tmp_path: Path) -> None:
        (tmp_path / "index.html").write_text("<h1>hi</h1>")
        pw = _FakePlaywright()
        skill = WebScreenshotSkill(playwright_factory=lambda: pw)
        skill.invoke(
            {
                "source": "index.html",
                "output_path": "out.png",
                "viewport_width": 1024,
                "viewport_height": 600,
            },
            _ctx(tmp_path),
        )
        assert pw.browser.contexts[0]["viewport"] == {"width": 1024, "height": 600}

    def test_default_user_agent_is_real_chrome(self, tmp_path: Path) -> None:
        """Anti-bot guards (Trello, Notion, Cloudflare) block the default
        Playwright UA. We ship a desktop Chrome UA so headless captures
        survive on hostile sites."""
        (tmp_path / "index.html").write_text("<h1>x</h1>")
        pw = _FakePlaywright()
        WebScreenshotSkill(playwright_factory=lambda: pw).invoke(
            {"source": "index.html", "output_path": "x.png"},
            _ctx(tmp_path),
        )
        ua = pw.browser.contexts[0]["user_agent"] or ""
        assert "Chrome/" in ua
        assert "Mozilla/" in ua
        assert "HeadlessChrome" not in ua  # the whole point

    def test_default_full_page_is_false(self, tmp_path: Path) -> None:
        """Wikipedia / Notion / heavy pages with full_page=True snapshot a
        6000+ px tall image that multiplies vision API latency 2-3x.
        Default to viewport-only; callers opt into full page when needed."""
        from reforge.runtime.skills.builtin import web_screenshot as ws_mod
        assert ws_mod._DEFAULT_FULL_PAGE is False

        # Verify the helper respects the default (skill class round-trips it via params).
        from inspect import signature
        sig = signature(ws_mod.screenshot)
        assert sig.parameters["full_page"].default is False

    def test_explicit_user_agent_overrides_default(self, tmp_path: Path) -> None:
        (tmp_path / "index.html").write_text("<h1>x</h1>")
        pw = _FakePlaywright()
        WebScreenshotSkill(playwright_factory=lambda: pw).invoke(
            {
                "source": "index.html",
                "output_path": "x.png",
                "user_agent": "TestBot/1.0",
            },
            _ctx(tmp_path),
        )
        assert pw.browser.contexts[0]["user_agent"] == "TestBot/1.0"

    def test_output_path_relative_is_resolved_against_workspace(self, tmp_path: Path) -> None:
        (tmp_path / "page.html").write_text("<h1>x</h1>")
        skill = WebScreenshotSkill(playwright_factory=_FakePlaywright)
        result = skill.invoke(
            {"source": "page.html", "output_path": "outputs/x.png"},
            _ctx(tmp_path),
        )
        assert result.success
        assert (tmp_path / "outputs" / "x.png").exists()

    def test_output_path_absolute_is_honored(self, tmp_path: Path) -> None:
        (tmp_path / "p.html").write_text("<h1>x</h1>")
        absolute = tmp_path / "abs" / "x.png"
        skill = WebScreenshotSkill(playwright_factory=_FakePlaywright)
        result = skill.invoke(
            {"source": "p.html", "output_path": str(absolute)},
            _ctx(tmp_path),
        )
        assert result.success
        assert absolute.exists()

    def test_missing_source_param_returns_error(self, tmp_path: Path) -> None:
        skill = WebScreenshotSkill(playwright_factory=_FakePlaywright)
        r = skill.invoke({"output_path": "x.png"}, _ctx(tmp_path))
        assert not r.success
        assert "source" in r.error

    def test_missing_output_param_returns_error(self, tmp_path: Path) -> None:
        skill = WebScreenshotSkill(playwright_factory=_FakePlaywright)
        r = skill.invoke({"source": "https://x"}, _ctx(tmp_path))
        assert not r.success
        assert "output_path" in r.error

    def test_missing_local_file_returns_skill_error(self, tmp_path: Path) -> None:
        skill = WebScreenshotSkill(playwright_factory=_FakePlaywright)
        r = skill.invoke(
            {"source": "missing.html", "output_path": "x.png"},
            _ctx(tmp_path),
        )
        assert not r.success
        assert "not found" in r.error

    def test_playwright_exception_becomes_skill_error(self, tmp_path: Path) -> None:
        (tmp_path / "p.html").write_text("<h1>x</h1>")

        class _Boom:
            def __enter__(self):
                raise RuntimeError("browser launch failed")
            def __exit__(self, *a):  # pragma: no cover — never reached
                pass

        skill = WebScreenshotSkill(playwright_factory=lambda: _Boom())
        r = skill.invoke(
            {"source": "p.html", "output_path": "x.png"},
            _ctx(tmp_path),
        )
        assert not r.success
        assert "browser launch failed" in r.error


# ---------------------------------------------------------------------------
# Auto-registration
# ---------------------------------------------------------------------------


class TestStepTimingPrint:
    def test_screenshot_helper_prints_step_timing(self, tmp_path, capsys, monkeypatch) -> None:
        """Module-level screenshot() prints `[reforge.step] screenshot ...`
        so the user can see how much of the budget went to chromium."""
        from reforge.runtime.skills.builtin import web_screenshot as ws_mod

        (tmp_path / "index.html").write_text("<h1>x</h1>")
        # Inject the fake at module level so the helper picks it up.
        monkeypatch.setattr(ws_mod, "_render", lambda **_kwargs: None)
        (tmp_path / "out.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")  # pretend output

        ws_mod.screenshot(str(tmp_path / "index.html"), str(tmp_path / "out.png"))
        captured = capsys.readouterr()
        assert "[reforge.step] screenshot" in captured.out
        assert "ok" in captured.out

    def test_screenshot_tags_source_as_url_or_local(self, tmp_path, capsys, monkeypatch) -> None:
        from reforge.runtime.skills.builtin import web_screenshot as ws_mod

        (tmp_path / "index.html").write_text("<h1>x</h1>")
        monkeypatch.setattr(ws_mod, "_render", lambda **_kwargs: None)

        ws_mod.screenshot(str(tmp_path / "index.html"), str(tmp_path / "out.png"))
        captured = capsys.readouterr()
        assert "screenshot (local)" in captured.out

    def test_screenshot_prints_start_before_render(
        self, tmp_path, capsys, monkeypatch
    ) -> None:
        """A START line must appear BEFORE the (potentially hanging) render.

        Without this, a subprocess killed mid-render leaves no trace of which
        step was active — the user can't tell whether the budget was spent on
        screenshot, describe_image, or compare_images.
        """
        from reforge.runtime.skills.builtin import web_screenshot as ws_mod

        events: list[str] = []

        def fake_render(**_kwargs) -> None:
            # Snapshot whatever was printed so far. If the helper had buffered
            # everything till the end, this list would still be empty.
            events.append(capsys.readouterr().out)

        (tmp_path / "index.html").write_text("<h1>x</h1>")
        monkeypatch.setattr(ws_mod, "_render", fake_render)

        ws_mod.screenshot(str(tmp_path / "index.html"), str(tmp_path / "out.png"))

        assert events, "_render was never called"
        # When _render runs, the START line must already be on stdout.
        assert "[reforge.step] screenshot (local): start" in events[0]


class TestAutoRegistration:
    def test_registered_when_playwright_available(self) -> None:
        # Auto-registration probes for playwright at import time and skips
        # the skill if it's not installed. Bare CI installs don't have
        # playwright (it isn't in pyproject [test] extras), so this test
        # would false-negative there — skip when playwright is genuinely
        # absent and the test would not be meaningful.
        pytest.importorskip("playwright", reason="playwright not installed")
        reg = default_skill_registry(include_web_search=False, include_vision=False)
        assert reg.get("web_screenshot") is not None

    def test_explicit_off_disables_registration(self) -> None:
        reg = default_skill_registry(
            include_web_search=False,
            include_vision=False,
            include_web_screenshot=False,
        )
        assert reg.get("web_screenshot") is None
