"""ExecutionContext + trace_id propagation tests.

Covers:
  - ExecutionContext.new() creates a fresh trace_id
  - ExecutionContext.child() inherits trace_id, derives a new session_id
  - All factory functions accept and propagate trace_id + parent_event_id
  - ExecutionEvent stores trace_id / parent_event_id (None by default)
  - Round-trip through ExecutionEventLog preserves trace_id
"""

from __future__ import annotations

from reforge.runtime.events import (
    ExecutionContext,
    ExecutionEvent,
    ExecutionEventLog,
    evaluation_completed,
    execution_failed,
    execution_started,
    execution_succeeded,
    policy_decided,
    recovery_attempted,
    reflection_generated,
    task_completed,
)


# ---------------------------------------------------------------------------
# Backwards compatibility
# ---------------------------------------------------------------------------


class TestBackwardsCompat:
    def test_event_defaults_have_no_trace(self) -> None:
        ev = execution_started(session_id="s1", task="hi")
        assert ev.trace_id is None
        assert ev.parent_event_id is None

    def test_old_caller_signature_unchanged(self) -> None:
        # The most common positional call sites must still work.
        a = execution_started("s", "task")
        b = execution_succeeded("s", "task", "output")
        c = recovery_attempted("s", "task", "strategy", 1)
        d = task_completed("s", "SUCCESS", "ok")
        assert all(e.trace_id is None for e in (a, b, c, d))


# ---------------------------------------------------------------------------
# ExecutionContext
# ---------------------------------------------------------------------------


class TestExecutionContext:
    def test_new_creates_fresh_trace_id(self) -> None:
        ctx_a = ExecutionContext.new("session-a")
        ctx_b = ExecutionContext.new("session-b")
        assert ctx_a.trace_id and ctx_b.trace_id
        assert ctx_a.trace_id != ctx_b.trace_id
        assert ctx_a.session_id == "session-a"

    def test_child_inherits_trace_id(self) -> None:
        parent = ExecutionContext.new("parent-session")
        child = parent.child("child-session")
        assert child.trace_id == parent.trace_id
        assert child.session_id == "child-session"
        # parent context is unchanged (frozen)
        assert parent.session_id == "parent-session"

    def test_child_records_parent_event_id(self) -> None:
        parent = ExecutionContext.new("p")
        child = parent.child("c", parent_event_id="evt-42")
        assert child.parent_event_id == "evt-42"

    def test_child_inherits_parent_event_id_when_not_overridden(self) -> None:
        ctx = ExecutionContext(
            trace_id="t",
            session_id="p",
            parent_event_id="root-evt",
        )
        child = ctx.child("c")
        assert child.parent_event_id == "root-evt"

    def test_context_is_frozen(self) -> None:
        ctx = ExecutionContext.new("s")
        import dataclasses
        import pytest

        with pytest.raises(dataclasses.FrozenInstanceError):
            ctx.session_id = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Factory propagation
# ---------------------------------------------------------------------------


class TestFactoryPropagation:
    """Every factory must accept trace_id + parent_event_id and propagate them."""

    def test_execution_started_propagates(self) -> None:
        ev = execution_started("s", "t", trace_id="trace-1", parent_event_id="p-1")
        assert ev.trace_id == "trace-1"
        assert ev.parent_event_id == "p-1"

    def test_execution_succeeded_propagates(self) -> None:
        ev = execution_succeeded("s", "t", trace_id="x", parent_event_id="y")
        assert (ev.trace_id, ev.parent_event_id) == ("x", "y")

    def test_execution_failed_propagates(self) -> None:
        ev = execution_failed(
            "s", "t",
            category="syntax", recoverable=True, error="oops",
            trace_id="trace-2", parent_event_id="p-2",
        )
        assert ev.trace_id == "trace-2"
        assert ev.parent_event_id == "p-2"

    def test_recovery_attempted_propagates(self) -> None:
        ev = recovery_attempted("s", "t", "fix", 2, trace_id="r-trace")
        assert ev.trace_id == "r-trace"

    def test_evaluation_completed_propagates(self) -> None:
        ev = evaluation_completed(
            "s", score=0.9, passed=True, trace_id="ev-trace"
        )
        assert ev.trace_id == "ev-trace"

    def test_reflection_generated_propagates(self) -> None:
        ev = reflection_generated("s", "summary", trace_id="rf-trace")
        assert ev.trace_id == "rf-trace"

    def test_policy_decided_propagates(self) -> None:
        ev = policy_decided("s", "RETRY", "syntax", trace_id="po-trace")
        assert ev.trace_id == "po-trace"

    def test_task_completed_propagates(self) -> None:
        ev = task_completed("s", "SUCCESS", "ok", trace_id="tc-trace")
        assert ev.trace_id == "tc-trace"


# ---------------------------------------------------------------------------
# End-to-end: dashboard can pivot by trace_id across subtasks
# ---------------------------------------------------------------------------


class TestEndToEndTracePivot:
    def test_parent_and_child_events_share_trace_id(self) -> None:
        """The story: one user request -> spawned subtask. The dashboard
        can group both sessions under the same trace_id."""
        root = ExecutionContext.new("session-root")
        child = root.child("session-child", parent_event_id="root-completed")

        log = ExecutionEventLog()
        log.append(execution_started(
            root.session_id, "outer task",
            trace_id=root.trace_id,
        ))
        log.append(execution_started(
            child.session_id, "inner task",
            trace_id=child.trace_id,
            parent_event_id=child.parent_event_id,
        ))

        all_evs = list(log.replay())
        trace_ids = {e.trace_id for e in all_evs}
        assert trace_ids == {root.trace_id}
        # And we can recover the parent-child link
        inner = [e for e in all_evs if e.session_id == "session-child"][0]
        assert inner.parent_event_id == "root-completed"

    def test_event_log_round_trip_preserves_trace_id(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s", "t", trace_id="abc"))
        ev = list(log.replay())[0]
        assert isinstance(ev, ExecutionEvent)
        assert ev.trace_id == "abc"

    def test_sibling_subtasks_share_one_trace_id(self) -> None:
        """Parallel siblings spawned from the same context share trace_id
        but each gets its own session_id — exactly what a fan-out planner
        does."""
        root = ExecutionContext.new("root")
        a = root.child("worker-a")
        b = root.child("worker-b")
        assert a.trace_id == b.trace_id == root.trace_id
        assert a.session_id != b.session_id
