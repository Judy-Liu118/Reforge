"""Tests for CLI formatter helpers.

format_stdout_tail surfaces the script's stdout on a failed attempt so
visible diagnostics (e.g. `[reforge.step] <helper>: N.Ns` timing prints)
aren't swallowed when only stderr is shown.
"""

from __future__ import annotations

from reforge.cli.formatter import format_stdout_tail
from reforge.runtime.domain.state.models import ExecutionState, RuntimeState


def _state(
    stdout: str = "",
    exit_code: int | None = None,
) -> RuntimeState:
    return RuntimeState(
        user_request="x",
        exec_state=ExecutionState(stdout=stdout, exit_code=exit_code),
    )


class TestFormatStdoutTail:
    def test_no_stdout_returns_none(self) -> None:
        assert format_stdout_tail(_state()) is None

    def test_clean_exit_returns_none(self) -> None:
        """Successful run already printed everything live — no need to repeat."""
        assert format_stdout_tail(_state(stdout="all good\nstuff", exit_code=0)) is None

    def test_short_stdout_on_failure_shown_in_full(self) -> None:
        out = format_stdout_tail(
            _state(stdout="line1\nline2\nline3", exit_code=1)
        )
        assert out is not None
        assert "[stdout tail]" in out
        assert "line1" in out
        assert "line3" in out

    def test_long_stdout_truncated_to_max_lines(self) -> None:
        many_lines = "\n".join(f"line{i}" for i in range(60))
        out = format_stdout_tail(
            _state(stdout=many_lines, exit_code=1),
            max_lines=20,
        )
        assert out is not None
        # last 20 are kept, including 'line59' but not 'line0'
        assert "line59" in out
        assert "line0\n" not in out  # the first line shouldn't appear
        assert "last 20 of 60" in out

    def test_surfaces_reforge_step_timing_lines(self) -> None:
        """The whole point: visual self-heal step timings must reach the user."""
        stdout = (
            "Capturing target page...\n"
            "[reforge.step] screenshot (url): 18.4s (ok)\n"
            "[reforge.step] describe_image: 12.7s (ok)\n"
            "[reforge.step] compare_images: 78.3s (ok)\n"
            "Visual similarity score: 0.45\n"
        )
        out = format_stdout_tail(_state(stdout=stdout, exit_code=1))
        assert out is not None
        assert "[reforge.step] screenshot" in out
        assert "[reforge.step] describe_image" in out
        assert "[reforge.step] compare_images" in out

    def test_empty_lines_skipped(self) -> None:
        out = format_stdout_tail(
            _state(stdout="line1\n\n\nline2\n\n", exit_code=1)
        )
        assert out is not None
        assert "line1" in out
        assert "line2" in out
        # Blank lines should not consume the budget
        assert out.count("line") == 2
