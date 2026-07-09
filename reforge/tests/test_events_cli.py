"""P35 — Events CLI handlers.

Tests cover:
  1. handle_events_list — empty log, single session, multiple sessions, columns
  2. handle_events_show — session found, session not found, timeline content
  3. handle_events_summary — empty log, counts, kind breakdown
  4. Edge cases — partial session (in_progress), multi-attempt session
"""

from __future__ import annotations

from pathlib import Path


from reforge.cli.events import (
    DEFAULT_EVENT_LOG_PATH,
    handle_events_list,
    handle_events_show,
    handle_events_summary,
)
from reforge.runtime.events.models import (
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
# Helpers — build a populated log at a tmp path
# ---------------------------------------------------------------------------


def _make_log(path: Path) -> PersistentEventLog:
    return PersistentEventLog(path)


def _single_success(path: Path, sid: str = "s1") -> PersistentEventLog:
    log = _make_log(path)
    log.append(execution_started(sid, "task"))
    log.append(execution_succeeded(sid, "task"))
    log.append(evaluation_completed(sid, score=1.0, passed=True))
    log.append(reflection_generated(sid, "Execution succeeded"))
    log.append(policy_decided(sid, "ACCEPT", "clean"))
    return log


def _single_failure(path: Path, sid: str = "s1") -> PersistentEventLog:
    log = _make_log(path)
    log.append(execution_started(sid, "task"))
    log.append(execution_failed(sid, "task", category="syntax", recoverable=True, error="SyntaxError"))
    log.append(evaluation_completed(sid, score=0.2, passed=False, reasons=["drift"]))
    log.append(reflection_generated(sid, "missing colon"))
    log.append(policy_decided(sid, "STOP", "max retries"))
    return log


def _retry_then_success(path: Path, sid: str = "s1") -> PersistentEventLog:
    log = _make_log(path)
    # Attempt 1 — fail + retry
    log.append(execution_started(sid, "task"))
    log.append(execution_failed(sid, "task", category="syntax", recoverable=True, error="err"))
    log.append(evaluation_completed(sid, score=0.3, passed=False))
    log.append(reflection_generated(sid, "syntax error"))
    log.append(policy_decided(sid, "RETRY", "retry"))
    log.append(recovery_attempted(sid, "task", "llm_retry", 1))
    # Attempt 2 — success
    log.append(execution_started(sid, "task"))
    log.append(execution_succeeded(sid, "task"))
    log.append(evaluation_completed(sid, score=1.0, passed=True))
    log.append(reflection_generated(sid, "Execution succeeded"))
    log.append(policy_decided(sid, "ACCEPT", "clean"))
    return log


# ---------------------------------------------------------------------------
# 1. handle_events_list
# ---------------------------------------------------------------------------


class TestHandleEventsList:
    def test_empty_log_prints_no_sessions(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "empty.jsonl"
        handle_events_list(path=p)
        out = capsys.readouterr().out
        assert "No event sessions found" in out

    def test_single_session_shows_header(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "e.jsonl"
        _single_success(p, "abc123")
        handle_events_list(path=p)
        out = capsys.readouterr().out
        assert "Sessions: 1" in out

    def test_session_id_in_output(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "e.jsonl"
        _single_success(p, "my-session")
        handle_events_list(path=p)
        out = capsys.readouterr().out
        assert "my-session" in out

    def test_outcome_in_output(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "e.jsonl"
        _single_success(p, "s1")
        handle_events_list(path=p)
        out = capsys.readouterr().out
        assert "succeeded" in out

    def test_failed_outcome_shown(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "e.jsonl"
        _single_failure(p, "s1")
        handle_events_list(path=p)
        out = capsys.readouterr().out
        assert "failed" in out

    def test_attempt_count_shown(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "e.jsonl"
        _retry_then_success(p, "s1")
        handle_events_list(path=p)
        out = capsys.readouterr().out
        assert "2" in out  # 2 attempts

    def test_event_count_column_present(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "e.jsonl"
        _single_success(p, "s1")
        handle_events_list(path=p)
        out = capsys.readouterr().out
        assert "5" in out  # 5 events in the single success pipeline

    def test_multiple_sessions_all_shown(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "e.jsonl"
        log = _make_log(p)
        # Session alice
        log.append(execution_started("alice", "t"))
        log.append(policy_decided("alice", "ACCEPT", "ok"))
        # Session bob
        log.append(execution_started("bob", "t"))
        log.append(policy_decided("bob", "STOP", "fail"))
        handle_events_list(path=p)
        out = capsys.readouterr().out
        assert "alice" in out
        assert "bob" in out
        assert "Sessions: 2" in out


# ---------------------------------------------------------------------------
# 2. handle_events_show
# ---------------------------------------------------------------------------


class TestHandleEventsShow:
    def test_session_not_found(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "e.jsonl"
        _single_success(p, "exists")
        handle_events_show("no-such-session", path=p)
        out = capsys.readouterr().out
        assert "not found" in out.lower()
        assert "no-such-session" in out

    def test_shows_session_id_in_header(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "e.jsonl"
        _single_success(p, "target-session")
        handle_events_show("target-session", path=p)
        out = capsys.readouterr().out
        assert "target-session" in out

    def test_shows_outcome(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "e.jsonl"
        _single_success(p, "s1")
        handle_events_show("s1", path=p)
        out = capsys.readouterr().out
        assert "succeeded" in out

    def test_shows_attempt_section(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "e.jsonl"
        _single_success(p, "s1")
        handle_events_show("s1", path=p)
        out = capsys.readouterr().out
        assert "Attempt 1" in out

    def test_shows_eval_score(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "e.jsonl"
        _single_failure(p, "s1")
        handle_events_show("s1", path=p)
        out = capsys.readouterr().out
        assert "0.20" in out

    def test_shows_reflection(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "e.jsonl"
        _single_failure(p, "s1")
        handle_events_show("s1", path=p)
        out = capsys.readouterr().out
        assert "missing colon" in out

    def test_shows_policy_decision(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "e.jsonl"
        _single_failure(p, "s1")
        handle_events_show("s1", path=p)
        out = capsys.readouterr().out
        assert "STOP" in out

    def test_retry_session_shows_two_attempts(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "e.jsonl"
        _retry_then_success(p, "s1")
        handle_events_show("s1", path=p)
        out = capsys.readouterr().out
        assert "Attempt 1" in out
        assert "Attempt 2" in out

    def test_not_found_suggests_list(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "e.jsonl"
        handle_events_show("ghost", path=p)
        out = capsys.readouterr().out
        assert "--events-list" in out


# ---------------------------------------------------------------------------
# 3. handle_events_summary
# ---------------------------------------------------------------------------


class TestHandleEventsSummary:
    def test_empty_log_message(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "e.jsonl"
        handle_events_summary(path=p)
        out = capsys.readouterr().out
        assert "No events recorded" in out

    def test_total_event_count(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "e.jsonl"
        _single_success(p, "s1")  # 5 events
        handle_events_summary(path=p)
        out = capsys.readouterr().out
        assert "5" in out

    def test_session_count(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "e.jsonl"
        log = _make_log(p)
        log.append(execution_started("s1", "t"))
        log.append(execution_started("s2", "t"))
        handle_events_summary(path=p)
        out = capsys.readouterr().out
        assert "2" in out

    def test_kind_breakdown_includes_execution_started(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "e.jsonl"
        _single_success(p, "s1")
        handle_events_summary(path=p)
        out = capsys.readouterr().out
        assert "EXECUTION_STARTED" in out

    def test_kind_breakdown_includes_policy_decided(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "e.jsonl"
        _single_success(p, "s1")
        handle_events_summary(path=p)
        out = capsys.readouterr().out
        assert "POLICY_DECIDED" in out

    def test_zero_count_kinds_not_shown(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "e.jsonl"
        log = _make_log(p)
        log.append(execution_started("s1", "t"))  # only EXECUTION_STARTED
        handle_events_summary(path=p)
        out = capsys.readouterr().out
        assert "EXECUTION_STARTED" in out
        assert "EXECUTION_FAILED" not in out

    def test_recovery_shown_when_present(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "e.jsonl"
        _retry_then_success(p, "s1")
        handle_events_summary(path=p)
        out = capsys.readouterr().out
        assert "RECOVERY_ATTEMPTED" in out


# ---------------------------------------------------------------------------
# 4. Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_partial_session_shows_in_progress(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "e.jsonl"
        log = _make_log(p)
        log.append(execution_started("partial", "t"))
        # No POLICY_DECIDED — session is in_progress
        handle_events_list(path=p)
        out = capsys.readouterr().out
        assert "in_progress" in out

    def test_default_path_is_path_object(self) -> None:
        assert isinstance(DEFAULT_EVENT_LOG_PATH, Path)

    def test_show_empty_log_session_not_found(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "empty.jsonl"
        handle_events_show("any-session", path=p)
        out = capsys.readouterr().out
        assert "not found" in out.lower()
