"""P18.4 — Agent-level tracing: AgentSpan + new EventType variants.

Test categories:
  1. AgentSpan construction — from_actor factory and direct init
  2. Event emission — STARTED on enter, COMPLETED/FAILED on exit
  3. Metadata content — actor_id, actor_role, session_scope, action, correlation_id
  4. Duration tracking — duration_ms populated on exit events
  5. Exception handling — FAILED emitted, exception re-raised
  6. Collector integration — events appended to existing collector list
  7. EventType additions — AGENT_ACTION_* variants exist in the enum
"""

from __future__ import annotations

import time

import pytest

from reforge.observability.tracing.agent_span import AgentSpan
from reforge.observability.tracing.collector import TraceCollector
from reforge.observability.tracing.models import EventType
from reforge.runtime.agents.identity import ActorContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collector(session_id: str = "test-session") -> TraceCollector:
    return TraceCollector(session_id=session_id)


def _ctx(role: str = "verifier", scope: str = "sess-abc") -> ActorContext:
    return ActorContext.create(actor_role=role, session_scope=scope)


# ---------------------------------------------------------------------------
# 1. EventType additions
# ---------------------------------------------------------------------------


class TestEventTypeAdditions:
    def test_agent_action_started_exists(self) -> None:
        assert EventType.AGENT_ACTION_STARTED == "AGENT_ACTION_STARTED"

    def test_agent_action_completed_exists(self) -> None:
        assert EventType.AGENT_ACTION_COMPLETED == "AGENT_ACTION_COMPLETED"

    def test_agent_action_failed_exists(self) -> None:
        assert EventType.AGENT_ACTION_FAILED == "AGENT_ACTION_FAILED"

    def test_new_variants_are_strings(self) -> None:
        for variant in (
            EventType.AGENT_ACTION_STARTED,
            EventType.AGENT_ACTION_COMPLETED,
            EventType.AGENT_ACTION_FAILED,
        ):
            assert isinstance(variant.value, str)


# ---------------------------------------------------------------------------
# 2. AgentSpan construction
# ---------------------------------------------------------------------------


class TestAgentSpanConstruction:
    def test_direct_init_stores_fields(self) -> None:
        collector = _collector()
        span = AgentSpan(
            collector=collector,
            actor_id="actor-1",
            actor_role="verifier",
            session_scope="scope-x",
            action="verify",
            correlation_id="cid-123",
        )
        assert span._actor_id == "actor-1"
        assert span._actor_role == "verifier"
        assert span._session_scope == "scope-x"
        assert span._action == "verify"
        assert span._correlation_id == "cid-123"

    def test_from_actor_extracts_actor_id(self) -> None:
        ctx = _ctx()
        span = AgentSpan.from_actor(_collector(), ctx, action="verify")
        assert span._actor_id == ctx.actor_id

    def test_from_actor_extracts_actor_role(self) -> None:
        ctx = _ctx(role="planner")
        span = AgentSpan.from_actor(_collector(), ctx, action="plan")
        assert span._actor_role == "planner"

    def test_from_actor_extracts_session_scope(self) -> None:
        ctx = _ctx(scope="my-scope")
        span = AgentSpan.from_actor(_collector(), ctx, action="any")
        assert span._session_scope == "my-scope"

    def test_from_actor_passes_correlation_id(self) -> None:
        ctx = _ctx()
        span = AgentSpan.from_actor(_collector(), ctx, action="verify", correlation_id="cid-xyz")
        assert span._correlation_id == "cid-xyz"

    def test_from_actor_default_correlation_id_is_empty(self) -> None:
        ctx = _ctx()
        span = AgentSpan.from_actor(_collector(), ctx, action="verify")
        assert span._correlation_id == ""


# ---------------------------------------------------------------------------
# 3. Event emission
# ---------------------------------------------------------------------------


class TestEventEmission:
    def test_enter_emits_started_event(self) -> None:
        collector = _collector()
        ctx = _ctx()
        with AgentSpan.from_actor(collector, ctx, action="verify"):
            pass
        started = [e for e in collector.events if e.event_type == EventType.AGENT_ACTION_STARTED]
        assert len(started) == 1

    def test_successful_exit_emits_completed_event(self) -> None:
        collector = _collector()
        ctx = _ctx()
        with AgentSpan.from_actor(collector, ctx, action="verify"):
            pass
        completed = [e for e in collector.events if e.event_type == EventType.AGENT_ACTION_COMPLETED]
        assert len(completed) == 1

    def test_success_emits_exactly_two_events(self) -> None:
        collector = _collector()
        ctx = _ctx()
        with AgentSpan.from_actor(collector, ctx, action="verify"):
            pass
        assert len(collector.events) == 2

    def test_exception_emits_failed_event(self) -> None:
        collector = _collector()
        ctx = _ctx()
        with pytest.raises(ValueError):
            with AgentSpan.from_actor(collector, ctx, action="verify"):
                raise ValueError("bad input")
        failed = [e for e in collector.events if e.event_type == EventType.AGENT_ACTION_FAILED]
        assert len(failed) == 1

    def test_exception_emits_no_completed_event(self) -> None:
        collector = _collector()
        ctx = _ctx()
        with pytest.raises(RuntimeError):
            with AgentSpan.from_actor(collector, ctx, action="verify"):
                raise RuntimeError("fail")
        completed = [e for e in collector.events if e.event_type == EventType.AGENT_ACTION_COMPLETED]
        assert len(completed) == 0

    def test_failure_emits_exactly_two_events(self) -> None:
        collector = _collector()
        ctx = _ctx()
        with pytest.raises(ValueError):
            with AgentSpan.from_actor(collector, ctx, action="verify"):
                raise ValueError("x")
        assert len(collector.events) == 2


# ---------------------------------------------------------------------------
# 4. Metadata content
# ---------------------------------------------------------------------------


class TestMetadataContent:
    def _started(self, collector: TraceCollector):
        return next(e for e in collector.events if e.event_type == EventType.AGENT_ACTION_STARTED)

    def _completed(self, collector: TraceCollector):
        return next(e for e in collector.events if e.event_type == EventType.AGENT_ACTION_COMPLETED)

    def test_started_metadata_contains_actor_id(self) -> None:
        collector = _collector()
        ctx = _ctx()
        with AgentSpan.from_actor(collector, ctx, action="verify"):
            pass
        assert self._started(collector).metadata["actor_id"] == ctx.actor_id

    def test_started_metadata_contains_actor_role(self) -> None:
        collector = _collector()
        ctx = _ctx(role="planner")
        with AgentSpan.from_actor(collector, ctx, action="plan"):
            pass
        assert self._started(collector).metadata["actor_role"] == "planner"

    def test_started_metadata_contains_session_scope(self) -> None:
        collector = _collector()
        ctx = _ctx(scope="scope-99")
        with AgentSpan.from_actor(collector, ctx, action="verify"):
            pass
        assert self._started(collector).metadata["session_scope"] == "scope-99"

    def test_started_metadata_contains_action(self) -> None:
        collector = _collector()
        ctx = _ctx()
        with AgentSpan.from_actor(collector, ctx, action="synthesize"):
            pass
        assert self._started(collector).metadata["action"] == "synthesize"

    def test_started_metadata_contains_correlation_id(self) -> None:
        collector = _collector()
        ctx = _ctx()
        with AgentSpan.from_actor(collector, ctx, action="verify", correlation_id="cid-42"):
            pass
        assert self._started(collector).metadata["correlation_id"] == "cid-42"

    def test_completed_event_has_ok_status(self) -> None:
        collector = _collector()
        ctx = _ctx()
        with AgentSpan.from_actor(collector, ctx, action="verify"):
            pass
        assert self._completed(collector).status == "OK"

    def test_failed_event_has_error_type_in_metadata(self) -> None:
        collector = _collector()
        ctx = _ctx()
        with pytest.raises(TypeError):
            with AgentSpan.from_actor(collector, ctx, action="verify"):
                raise TypeError("type mismatch")
        failed = next(e for e in collector.events if e.event_type == EventType.AGENT_ACTION_FAILED)
        assert failed.metadata["error_type"] == "TypeError"

    def test_failed_event_has_error_message_in_metadata(self) -> None:
        collector = _collector()
        ctx = _ctx()
        with pytest.raises(ValueError):
            with AgentSpan.from_actor(collector, ctx, action="verify"):
                raise ValueError("detailed message")
        failed = next(e for e in collector.events if e.event_type == EventType.AGENT_ACTION_FAILED)
        assert "detailed message" in failed.metadata["error"]

    def test_event_session_id_matches_session_scope(self) -> None:
        collector = _collector()
        ctx = _ctx(scope="my-research-session")
        with AgentSpan.from_actor(collector, ctx, action="verify"):
            pass
        for event in collector.events:
            assert event.session_id == "my-research-session"


# ---------------------------------------------------------------------------
# 5. Duration tracking
# ---------------------------------------------------------------------------


class TestDurationTracking:
    def test_started_event_has_zero_duration(self) -> None:
        collector = _collector()
        ctx = _ctx()
        with AgentSpan.from_actor(collector, ctx, action="verify"):
            pass
        started = next(e for e in collector.events if e.event_type == EventType.AGENT_ACTION_STARTED)
        assert started.duration_ms == 0.0

    def test_completed_event_has_nonnegative_duration(self) -> None:
        collector = _collector()
        ctx = _ctx()
        with AgentSpan.from_actor(collector, ctx, action="verify"):
            pass
        completed = next(e for e in collector.events if e.event_type == EventType.AGENT_ACTION_COMPLETED)
        assert completed.duration_ms >= 0.0

    def test_duration_reflects_elapsed_time(self) -> None:
        collector = _collector()
        ctx = _ctx()
        with AgentSpan.from_actor(collector, ctx, action="verify"):
            time.sleep(0.01)  # 10 ms
        completed = next(e for e in collector.events if e.event_type == EventType.AGENT_ACTION_COMPLETED)
        assert completed.duration_ms >= 5.0  # at least 5 ms


# ---------------------------------------------------------------------------
# 6. Collector integration
# ---------------------------------------------------------------------------


class TestCollectorIntegration:
    def test_events_appended_to_existing_events(self) -> None:
        collector = _collector()
        # Pre-populate with a dummy event to confirm we append, not reset
        from reforge.observability.tracing.models import TraceEvent
        collector.events.append(
            TraceEvent.create(session_id="s", event_type=EventType.TASK_COMPLETED)
        )
        ctx = _ctx()
        with AgentSpan.from_actor(collector, ctx, action="verify"):
            pass
        assert len(collector.events) == 3  # pre-existing + STARTED + COMPLETED

    def test_multiple_spans_accumulate_events(self) -> None:
        collector = _collector()
        ctx1 = _ctx(role="verifier", scope="s1")
        ctx2 = _ctx(role="planner", scope="s2")
        with AgentSpan.from_actor(collector, ctx1, action="verify"):
            pass
        with AgentSpan.from_actor(collector, ctx2, action="plan"):
            pass
        assert len(collector.events) == 4  # 2 spans × 2 events each

    def test_two_spans_are_independent(self) -> None:
        collector = _collector()
        ctx1 = _ctx(role="verifier", scope="s1")
        ctx2 = _ctx(role="planner", scope="s2")
        with AgentSpan.from_actor(collector, ctx1, action="verify"):
            pass
        with AgentSpan.from_actor(collector, ctx2, action="plan"):
            pass
        roles = [e.metadata["actor_role"] for e in collector.events]
        assert "verifier" in roles
        assert "planner" in roles
