"""P31 — Always-Active EventLog in RuntimeRunner.

Before P31, RuntimeRunner.event_log was None when no external log was injected,
which caused wrap_*_node to fall back to identity wrappers (no overrides).

After P31, RuntimeRunner always creates an internal ExecutionEventLog so that:
  - emitter overrides (P28-P30) are ALWAYS applied, never skipped
  - event_log is never None (safe to use without None-check)
  - external log injection still works (for cross-session sharing)

Tests cover:
  1. Auto-created log — not None, correct type, empty initially
  2. Injected log — used as-is, identity preserved
  3. Runner isolation — two runners with no external log get independent logs
  4. Shared external log — two runners injected with same log both write to it
  5. session_id contract — unchanged by P31
"""

from __future__ import annotations

import pytest

from reforge.runtime.orchestration.engine.runner import RuntimeRunner
from reforge.runtime.events.log import ExecutionEventLog


# ---------------------------------------------------------------------------
# 1. Auto-created log
# ---------------------------------------------------------------------------


class TestAutoCreatedLog:
    def test_default_event_log_is_not_none(self) -> None:
        runner = RuntimeRunner()
        assert runner.event_log is not None

    def test_default_event_log_is_correct_type(self) -> None:
        runner = RuntimeRunner()
        assert isinstance(runner.event_log, ExecutionEventLog)

    def test_default_event_log_starts_empty(self) -> None:
        runner = RuntimeRunner()
        assert runner.event_log.replay() == []

    def test_default_event_log_tracks_session_id(self) -> None:
        runner = RuntimeRunner()
        # Log is isolated to this runner's session — no cross-session contamination
        sessions = runner.event_log.sessions()
        assert len(sessions) == 0  # nothing run yet, so empty


# ---------------------------------------------------------------------------
# 2. Injected log
# ---------------------------------------------------------------------------


class TestInjectedLog:
    def test_injected_log_is_used(self) -> None:
        log = ExecutionEventLog()
        runner = RuntimeRunner(event_log=log)
        assert runner.event_log is log

    def test_injected_log_is_not_replaced(self) -> None:
        log = ExecutionEventLog()
        runner = RuntimeRunner(event_log=log)
        # The injected log object is the same instance
        assert runner.event_log is log
        assert id(runner.event_log) == id(log)

    def test_none_injection_triggers_auto_create(self) -> None:
        runner = RuntimeRunner(event_log=None)
        assert runner.event_log is not None
        assert isinstance(runner.event_log, ExecutionEventLog)


# ---------------------------------------------------------------------------
# 3. Runner isolation
# ---------------------------------------------------------------------------


class TestRunnerIsolation:
    def test_two_runners_get_independent_logs(self) -> None:
        r1 = RuntimeRunner()
        r2 = RuntimeRunner()
        assert r1.event_log is not r2.event_log

    def test_independent_logs_are_not_same_object(self) -> None:
        r1 = RuntimeRunner()
        r2 = RuntimeRunner()
        assert id(r1.event_log) != id(r2.event_log)

    def test_different_session_ids(self) -> None:
        r1 = RuntimeRunner()
        r2 = RuntimeRunner()
        assert r1.session_id != r2.session_id


# ---------------------------------------------------------------------------
# 4. Shared external log
# ---------------------------------------------------------------------------


class TestSharedExternalLog:
    def test_shared_log_is_same_object_in_both_runners(self) -> None:
        shared_log = ExecutionEventLog()
        r1 = RuntimeRunner(event_log=shared_log)
        r2 = RuntimeRunner(event_log=shared_log)
        assert r1.event_log is shared_log
        assert r2.event_log is shared_log
        assert r1.event_log is r2.event_log

    def test_shared_log_collects_from_both_sessions(self) -> None:
        from reforge.runtime.events.models import execution_started
        shared_log = ExecutionEventLog()
        r1 = RuntimeRunner(event_log=shared_log)
        r2 = RuntimeRunner(event_log=shared_log)
        # Manually simulate what emitters would do
        shared_log.append(execution_started(r1.session_id, "task A"))
        shared_log.append(execution_started(r2.session_id, "task B"))
        sessions = shared_log.sessions()
        assert r1.session_id in sessions
        assert r2.session_id in sessions


# ---------------------------------------------------------------------------
# 5. session_id contract unchanged
# ---------------------------------------------------------------------------


class TestSessionIdContract:
    def test_session_id_is_non_empty_string(self) -> None:
        runner = RuntimeRunner()
        assert isinstance(runner.session_id, str)
        assert len(runner.session_id) > 0

    def test_session_id_unique_across_instances(self) -> None:
        sessions = {RuntimeRunner().session_id for _ in range(5)}
        assert len(sessions) == 5

    def test_session_id_stable_within_runner(self) -> None:
        runner = RuntimeRunner()
        sid = runner.session_id
        assert runner.session_id == sid  # same value on repeated access
