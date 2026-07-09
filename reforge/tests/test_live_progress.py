"""P37 — Live Progress Display.

Tests cover:
  1. format_live_event: correct one-liner per event kind
  2. format_live_event: None for unknown kind
  3. ProgressPrinter: subscribes on construction, writes to file
  4. ProgressPrinter: stop() cancels subscription
  5. ProgressPrinter: stop() is idempotent
  6. Integration: events from ExecutionEventLog reach ProgressPrinter
  7. Integration: events from PersistentEventLog reach ProgressPrinter
  8. Edge cases: empty/missing payload fields handled gracefully
"""

from __future__ import annotations

import io
from pathlib import Path


from reforge.cli.progress import ProgressPrinter, format_live_event
from reforge.runtime.events.log import ExecutionEventLog
from reforge.runtime.events.models import (
    ExecutionEvent,
    evaluation_completed,
    execution_failed,
    execution_started,
    execution_succeeded,
    policy_decided,
    recovery_attempted,
    reflection_generated,
)
from reforge.runtime.events.persistent_log import PersistentEventLog


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ev(kind: str, session_id: str = "s1", **payload) -> ExecutionEvent:
    return ExecutionEvent(kind=kind, session_id=session_id, payload=payload)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. format_live_event — correct output per kind
# ---------------------------------------------------------------------------


class TestFormatLiveEvent:
    def test_execution_started_contains_symbol(self) -> None:
        ev = execution_started("s1", "run the script")
        line = format_live_event(ev)
        assert line is not None
        assert "→" in line

    def test_execution_started_includes_task(self) -> None:
        ev = execution_started("s1", "run the script")
        line = format_live_event(ev)
        assert "run the script" in line

    def test_execution_succeeded_contains_symbol(self) -> None:
        ev = execution_succeeded("s1", "task")
        line = format_live_event(ev)
        assert line is not None
        assert "✓" in line

    def test_execution_succeeded_with_summary(self) -> None:
        ev = execution_succeeded("s1", "task", output_summary="all good")
        line = format_live_event(ev)
        assert "all good" in line

    def test_execution_failed_contains_symbol(self) -> None:
        ev = execution_failed("s1", "t", category="syntax", recoverable=True, error="SyntaxError")
        line = format_live_event(ev)
        assert line is not None
        assert "✗" in line

    def test_execution_failed_includes_category(self) -> None:
        ev = execution_failed("s1", "t", category="syntax", recoverable=True, error="bad")
        line = format_live_event(ev)
        assert "syntax" in line

    def test_execution_failed_includes_error(self) -> None:
        ev = execution_failed("s1", "t", category="runtime_error", recoverable=False, error="NameError: x")
        line = format_live_event(ev)
        assert "NameError" in line

    def test_recovery_attempted_contains_symbol(self) -> None:
        ev = recovery_attempted("s1", "t", "llm_retry", 1)
        line = format_live_event(ev)
        assert line is not None
        assert "~" in line

    def test_recovery_attempted_includes_strategy(self) -> None:
        ev = recovery_attempted("s1", "t", "llm_retry", 2)
        line = format_live_event(ev)
        assert "llm_retry" in line
        assert "#2" in line

    def test_evaluation_completed_contains_symbol(self) -> None:
        ev = evaluation_completed("s1", score=0.85, passed=True)
        line = format_live_event(ev)
        assert line is not None
        assert "=" in line

    def test_evaluation_completed_score_formatted(self) -> None:
        ev = evaluation_completed("s1", score=0.85, passed=True)
        line = format_live_event(ev)
        assert "0.85" in line

    def test_evaluation_completed_passed_status(self) -> None:
        ev = evaluation_completed("s1", score=1.0, passed=True)
        line = format_live_event(ev)
        assert "pass" in line

    def test_evaluation_completed_failed_status(self) -> None:
        ev = evaluation_completed("s1", score=0.2, passed=False)
        line = format_live_event(ev)
        assert "fail" in line

    def test_reflection_generated_contains_symbol(self) -> None:
        ev = reflection_generated("s1", "variable was undefined")
        line = format_live_event(ev)
        assert line is not None
        assert "*" in line

    def test_reflection_generated_includes_summary(self) -> None:
        ev = reflection_generated("s1", "variable was undefined")
        line = format_live_event(ev)
        assert "variable was undefined" in line

    def test_policy_decided_contains_symbol(self) -> None:
        ev = policy_decided("s1", "ACCEPT", "clean output")
        line = format_live_event(ev)
        assert line is not None
        assert "■" in line

    def test_policy_decided_includes_decision(self) -> None:
        ev = policy_decided("s1", "ACCEPT", "clean output")
        line = format_live_event(ev)
        assert "ACCEPT" in line

    def test_policy_decided_includes_reason(self) -> None:
        ev = policy_decided("s1", "REJECT", "score too low")
        line = format_live_event(ev)
        assert "score too low" in line

    def test_all_lines_start_with_two_spaces(self) -> None:
        events = [
            execution_started("s1", "task"),
            execution_succeeded("s1", "task"),
            execution_failed("s1", "t", category="syntax", recoverable=True, error="e"),
            recovery_attempted("s1", "t", "llm_retry", 1),
            evaluation_completed("s1", score=0.5, passed=False),
            reflection_generated("s1", "root cause"),
            policy_decided("s1", "ACCEPT", "ok"),
        ]
        for ev in events:
            line = format_live_event(ev)
            assert line is not None
            assert line.startswith("  "), f"Expected indent for {ev.kind}"

    def test_long_task_is_truncated(self) -> None:
        long_task = "x" * 200
        ev = execution_started("s1", long_task)
        line = format_live_event(ev)
        assert line is not None
        assert len(line) < 150

    def test_long_error_is_truncated(self) -> None:
        long_err = "e" * 200
        ev = execution_failed("s1", "t", category="syntax", recoverable=True, error=long_err)
        line = format_live_event(ev)
        assert line is not None
        assert len(line) < 150


# ---------------------------------------------------------------------------
# 2. format_live_event — edge cases
# ---------------------------------------------------------------------------


class TestFormatEdgeCases:
    def test_empty_task_produces_short_line(self) -> None:
        ev = execution_started("s1", "")
        line = format_live_event(ev)
        assert line is not None
        assert "Started" in line

    def test_empty_error_produces_clean_line(self) -> None:
        ev = execution_failed("s1", "t", category="unknown", recoverable=False, error="")
        line = format_live_event(ev)
        assert line is not None
        assert "unknown" in line

    def test_empty_reason_policy_has_no_dash(self) -> None:
        ev = policy_decided("s1", "ABORT", "")
        line = format_live_event(ev)
        assert line is not None
        assert "—" not in line

    def test_empty_summary_reflection_has_no_colon(self) -> None:
        ev = reflection_generated("s1", "")
        line = format_live_event(ev)
        assert line is not None
        assert "Reflection" in line
        # should not have trailing ": " when summary is empty
        assert not line.endswith(": ")


# ---------------------------------------------------------------------------
# 3. ProgressPrinter — basic behaviour
# ---------------------------------------------------------------------------


class TestProgressPrinter:
    def test_subscribes_on_construction(self) -> None:
        log = ExecutionEventLog()
        buf = io.StringIO()
        printer = ProgressPrinter(log, file=buf)
        log.append(execution_started("s1", "task"))
        printer.stop()
        assert len(buf.getvalue()) > 0

    def test_stop_cancels_subscription(self) -> None:
        log = ExecutionEventLog()
        buf = io.StringIO()
        printer = ProgressPrinter(log, file=buf)
        printer.stop()
        log.append(execution_started("s1", "task"))
        assert buf.getvalue() == ""

    def test_stop_is_idempotent(self) -> None:
        log = ExecutionEventLog()
        printer = ProgressPrinter(log)
        printer.stop()
        printer.stop()  # must not raise

    def test_all_kinds_produce_output(self) -> None:
        log = ExecutionEventLog()
        buf = io.StringIO()
        printer = ProgressPrinter(log, file=buf)

        events = [
            execution_started("s1", "task"),
            execution_succeeded("s1", "task"),
            execution_failed("s1", "t", category="syntax", recoverable=True, error="err"),
            recovery_attempted("s1", "t", "llm_retry", 1),
            evaluation_completed("s1", score=0.9, passed=True),
            reflection_generated("s1", "cause"),
            policy_decided("s1", "ACCEPT", "ok"),
        ]
        for ev in events:
            log.append(ev)
        printer.stop()

        output = buf.getvalue()
        assert output.count("\n") == len(events)

    def test_output_uses_injected_file(self) -> None:
        log = ExecutionEventLog()
        buf = io.StringIO()
        printer = ProgressPrinter(log, file=buf)
        log.append(execution_started("s1", "my-task"))
        printer.stop()
        assert "my-task" in buf.getvalue()

    def test_multiple_events_each_on_own_line(self) -> None:
        log = ExecutionEventLog()
        buf = io.StringIO()
        printer = ProgressPrinter(log, file=buf)
        log.append(execution_started("s1", "a"))
        log.append(execution_succeeded("s1", "a"))
        printer.stop()
        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        assert len(lines) == 2


# ---------------------------------------------------------------------------
# 4. PersistentEventLog integration
# ---------------------------------------------------------------------------


class TestPersistentLogIntegration:
    def test_persistent_log_events_reach_printer(self, tmp_path: Path) -> None:
        log = PersistentEventLog(tmp_path / "e.jsonl")
        buf = io.StringIO()
        printer = ProgressPrinter(log, file=buf)
        log.append(execution_started("s1", "task"))
        printer.stop()
        assert "→" in buf.getvalue()

    def test_loaded_log_new_events_reach_printer(self, tmp_path: Path) -> None:
        p = tmp_path / "e.jsonl"
        orig = PersistentEventLog(p)
        orig.append(execution_started("s1", "old"))

        loaded = PersistentEventLog.load(p)
        buf = io.StringIO()
        printer = ProgressPrinter(loaded, file=buf)
        loaded.append(execution_succeeded("s2", "new"))
        printer.stop()
        # Only the new event should appear, not the reconstructed one
        assert "✓" in buf.getvalue()
        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        assert len(lines) == 1
