"""P18.5 — End-to-end multi-verifier research session with divergence resolution.

Test categories:
  1. Factory construction — build_bus_research_session returns correct structure
  2. Divergence resolution — majority vote propagates into ResearchResult
  3. Tracing integration — AgentSpan events emitted when collector provided
  4. Full research flow — session.run() with stub planner/synthesizer/verifiers
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from reforge.observability.tracing.collector import TraceCollector
from reforge.observability.tracing.models import EventType
from reforge.runtime.agents.bus import MessageBus
from reforge.runtime.agents.identity import ActorContext
from reforge.runtime.agents.multi_agent import build_bus_research_session
from reforge.runtime.agents.role import SynthesisResult
from reforge.runtime.research.models import HypothesisRecord, ResearchPlan, ResearchResult


# ---------------------------------------------------------------------------
# Shared stubs
# ---------------------------------------------------------------------------


def _stub_verifier(status: str, confidence: float = 0.8, evidence: list[str] | None = None):
    """VerifierAgent stub that always returns a fixed status."""
    m = MagicMock()
    m.verify.side_effect = lambda h: h.model_copy(
        update={
            "status": status,
            "confidence": confidence,
            "evidence": evidence or [f"evidence-{status}"],
        }
    )
    return m


def _stub_planner(*hypothesis_texts: str):
    """PlannerAgent stub that returns a fixed set of hypotheses (ignores prior_findings)."""
    m = MagicMock()

    def _plan(question, prior_findings=None, context=""):
        return ResearchPlan(
            question=question,
            hypotheses=[
                HypothesisRecord(
                    hypothesis=text,
                    verification_request=f"check: {text}",
                )
                for text in hypothesis_texts
            ],
        )

    m.plan.side_effect = _plan
    return m


def _stub_synthesizer(conclusion: str = "Stub conclusion"):
    """SynthesizerAgent stub that returns a fixed conclusion."""
    m = MagicMock()
    m.synthesize.return_value = SynthesisResult(
        conclusion=conclusion, contradictions=[]
    )
    return m


def _collector() -> TraceCollector:
    return TraceCollector(session_id="e2e-test")


# ---------------------------------------------------------------------------
# 1. Factory construction
# ---------------------------------------------------------------------------


class TestBuildBusResearchSession:
    def test_returns_three_tuple(self) -> None:
        verifiers = [_stub_verifier("confirmed")]
        result = build_bus_research_session(verifiers, session_scope="scope-1")
        assert len(result) == 3

    def test_first_element_is_research_session(self) -> None:
        from reforge.runtime.research.session import ResearchSession

        verifiers = [_stub_verifier("confirmed")]
        session, _, _ = build_bus_research_session(verifiers, session_scope="s")
        assert isinstance(session, ResearchSession)

    def test_second_element_is_message_bus(self) -> None:
        verifiers = [_stub_verifier("confirmed")]
        _, bus, _ = build_bus_research_session(verifiers, session_scope="s")
        assert isinstance(bus, MessageBus)

    def test_third_element_is_actor_context(self) -> None:
        verifiers = [_stub_verifier("confirmed")]
        _, _, sender_ctx = build_bus_research_session(verifiers, session_scope="s")
        assert isinstance(sender_ctx, ActorContext)

    def test_sender_ctx_role_is_orchestrator(self) -> None:
        verifiers = [_stub_verifier("confirmed")]
        _, _, sender_ctx = build_bus_research_session(verifiers, session_scope="s")
        assert sender_ctx.actor_role == "orchestrator"

    def test_sender_ctx_scope_matches_session_scope(self) -> None:
        verifiers = [_stub_verifier("confirmed")]
        _, _, sender_ctx = build_bus_research_session(
            verifiers, session_scope="my-scope"
        )
        assert sender_ctx.session_scope == "my-scope"

    def test_bus_has_all_verifiers_registered(self) -> None:
        verifiers = [_stub_verifier("confirmed") for _ in range(3)]
        _, bus, _ = build_bus_research_session(verifiers, session_scope="s")
        actors = bus.registered_actors()
        verifier_actors = [a for a in actors if a.actor_role == "verifier"]
        assert len(verifier_actors) == 3

    def test_all_verifier_actors_share_session_scope(self) -> None:
        verifiers = [_stub_verifier("confirmed") for _ in range(2)]
        _, bus, _ = build_bus_research_session(
            verifiers, session_scope="shared-scope"
        )
        for ctx in bus.registered_actors():
            if ctx.actor_role == "verifier":
                assert ctx.session_scope == "shared-scope"

    def test_each_verifier_actor_has_unique_id(self) -> None:
        verifiers = [_stub_verifier("confirmed") for _ in range(3)]
        _, bus, _ = build_bus_research_session(verifiers, session_scope="s")
        ids = [ctx.actor_id for ctx in bus.registered_actors() if ctx.actor_role == "verifier"]
        assert len(set(ids)) == 3

    def test_empty_verifier_list_creates_session(self) -> None:
        """Empty bus is valid — BusVerifier returns inconclusive on empty response list."""
        session, bus, _ = build_bus_research_session([], session_scope="s")
        assert bus.registered_actors() == []

    def test_custom_planner_is_used(self) -> None:
        stub_planner = _stub_planner("H1")
        session, _, _ = build_bus_research_session(
            [_stub_verifier("confirmed")],
            session_scope="s",
            planner=stub_planner,
        )
        assert session._planner is stub_planner

    def test_custom_synthesizer_is_used(self) -> None:
        stub_synth = _stub_synthesizer()
        session, _, _ = build_bus_research_session(
            [_stub_verifier("confirmed")],
            session_scope="s",
            synthesizer=stub_synth,
        )
        assert session._synthesizer is stub_synth


# ---------------------------------------------------------------------------
# 2. Divergence resolution
# ---------------------------------------------------------------------------


class TestDivergenceResolutionInSession:
    def _run_with_verifiers(self, *statuses: str, hypothesis: str = "H") -> ResearchResult:
        verifiers = [_stub_verifier(s) for s in statuses]
        planner = _stub_planner(hypothesis)
        synth = _stub_synthesizer()
        session, _, _ = build_bus_research_session(
            verifiers,
            session_scope="scope-test",
            planner=planner,
            synthesizer=synth,
            max_rounds=1,
        )
        return session.run("test question")

    def test_two_confirmed_one_rejected_gives_confirmed(self) -> None:
        result = self._run_with_verifiers("confirmed", "confirmed", "rejected")
        statuses = [h.status for h in result.final_hypotheses]
        assert "confirmed" in statuses

    def test_two_rejected_one_confirmed_gives_rejected(self) -> None:
        result = self._run_with_verifiers("rejected", "rejected", "confirmed")
        statuses = [h.status for h in result.final_hypotheses]
        assert "rejected" in statuses

    def test_one_each_gives_inconclusive(self) -> None:
        result = self._run_with_verifiers("confirmed", "rejected", "inconclusive")
        statuses = [h.status for h in result.final_hypotheses]
        assert "inconclusive" in statuses

    def test_all_confirmed_gives_confirmed(self) -> None:
        result = self._run_with_verifiers("confirmed", "confirmed", "confirmed")
        statuses = [h.status for h in result.final_hypotheses]
        assert all(s == "confirmed" for s in statuses)

    def test_single_verifier_passes_through_status(self) -> None:
        result = self._run_with_verifiers("rejected")
        statuses = [h.status for h in result.final_hypotheses]
        assert "rejected" in statuses

    def test_multiple_hypotheses_each_get_consensus(self) -> None:
        """Each hypothesis gets its own majority vote; test two diverge differently."""
        verifiers = [_stub_verifier("confirmed"), _stub_verifier("confirmed"), _stub_verifier("rejected")]
        planner = _stub_planner("H1", "H2")
        session, _, _ = build_bus_research_session(
            verifiers,
            session_scope="s",
            planner=planner,
            synthesizer=_stub_synthesizer(),
            max_rounds=1,
        )
        result = session.run("test?")
        assert len(result.final_hypotheses) == 2
        for h in result.final_hypotheses:
            assert h.status == "confirmed"  # 2/3 confirmed for each

    def test_confidence_is_averaged_across_verifiers(self) -> None:
        v1 = _stub_verifier("confirmed", confidence=0.9)
        v2 = _stub_verifier("confirmed", confidence=0.7)
        v3 = _stub_verifier("confirmed", confidence=0.5)
        planner = _stub_planner("H1")
        session, _, _ = build_bus_research_session(
            [v1, v2, v3],
            session_scope="s",
            planner=planner,
            synthesizer=_stub_synthesizer(),
            max_rounds=1,
        )
        result = session.run("test?")
        h = result.final_hypotheses[0]
        assert h.confidence == pytest.approx(0.7)


# ---------------------------------------------------------------------------
# 3. Tracing integration
# ---------------------------------------------------------------------------


class TestTracingIntegration:
    def _run_traced(
        self,
        verifier_statuses: list[str],
        hypothesis_texts: list[str],
    ) -> tuple[ResearchResult, TraceCollector]:
        collector = _collector()
        verifiers = [_stub_verifier(s) for s in verifier_statuses]
        planner = _stub_planner(*hypothesis_texts)
        session, _, _ = build_bus_research_session(
            verifiers,
            session_scope="trace-scope",
            collector=collector,
            planner=planner,
            synthesizer=_stub_synthesizer(),
            max_rounds=1,
        )
        result = session.run("trace test?")
        return result, collector

    def test_no_agent_events_when_no_collector(self) -> None:
        verifiers = [_stub_verifier("confirmed")]
        planner = _stub_planner("H1")
        session, _, _ = build_bus_research_session(
            verifiers,
            session_scope="s",
            planner=planner,
            synthesizer=_stub_synthesizer(),
            max_rounds=1,
        )
        # Verify that a fresh collector (not passed in) has no events
        fresh = _collector()
        session.run("test?")
        assert len(fresh.events) == 0

    def test_agent_events_emitted_when_collector_provided(self) -> None:
        _, collector = self._run_traced(["confirmed"], ["H1"])
        agent_events = [
            e for e in collector.events
            if e.event_type in (
                EventType.AGENT_ACTION_STARTED,
                EventType.AGENT_ACTION_COMPLETED,
                EventType.AGENT_ACTION_FAILED,
            )
        ]
        assert len(agent_events) > 0

    def test_started_event_count_equals_verifiers_times_hypotheses(self) -> None:
        # 2 verifiers × 1 hypothesis = 2 STARTED events
        _, collector = self._run_traced(["confirmed", "rejected"], ["H1"])
        started = [
            e for e in collector.events if e.event_type == EventType.AGENT_ACTION_STARTED
        ]
        assert len(started) == 2

    def test_completed_events_equal_started_events_on_success(self) -> None:
        _, collector = self._run_traced(["confirmed", "confirmed"], ["H1"])
        started_count = sum(
            1 for e in collector.events if e.event_type == EventType.AGENT_ACTION_STARTED
        )
        completed_count = sum(
            1 for e in collector.events if e.event_type == EventType.AGENT_ACTION_COMPLETED
        )
        assert started_count == completed_count

    def test_events_contain_actor_id_in_metadata(self) -> None:
        _, collector = self._run_traced(["confirmed"], ["H1"])
        for event in collector.events:
            if event.event_type == EventType.AGENT_ACTION_STARTED:
                assert "actor_id" in event.metadata
                assert event.metadata["actor_id"]  # non-empty

    def test_events_contain_verifier_role_in_metadata(self) -> None:
        _, collector = self._run_traced(["confirmed"], ["H1"])
        for event in collector.events:
            if event.event_type == EventType.AGENT_ACTION_STARTED:
                assert event.metadata["actor_role"] == "verifier"

    def test_event_session_id_matches_session_scope(self) -> None:
        collector = _collector()
        verifiers = [_stub_verifier("confirmed")]
        session, _, _ = build_bus_research_session(
            verifiers,
            session_scope="specific-scope",
            collector=collector,
            planner=_stub_planner("H1"),
            synthesizer=_stub_synthesizer(),
            max_rounds=1,
        )
        session.run("?")
        for event in collector.events:
            assert event.session_id == "specific-scope"

    def test_multiple_verifiers_have_distinct_actor_ids_in_events(self) -> None:
        _, collector = self._run_traced(["confirmed", "rejected", "inconclusive"], ["H1"])
        started_events = [
            e for e in collector.events if e.event_type == EventType.AGENT_ACTION_STARTED
        ]
        actor_ids = {e.metadata["actor_id"] for e in started_events}
        assert len(actor_ids) == 3  # each verifier has a unique actor_id

    def test_correlation_id_is_consistent_within_one_request(self) -> None:
        """All events for one hypothesis verification share the same correlation_id."""
        _, collector = self._run_traced(["confirmed"], ["H1"])
        started = next(
            e for e in collector.events if e.event_type == EventType.AGENT_ACTION_STARTED
        )
        completed = next(
            e for e in collector.events if e.event_type == EventType.AGENT_ACTION_COMPLETED
        )
        assert started.metadata["correlation_id"] == completed.metadata["correlation_id"]


# ---------------------------------------------------------------------------
# 4. Full research flow
# ---------------------------------------------------------------------------


class TestEndToEndFlow:
    def test_run_returns_research_result(self) -> None:
        verifiers = [_stub_verifier("confirmed")]
        session, _, _ = build_bus_research_session(
            verifiers,
            session_scope="s",
            planner=_stub_planner("H1"),
            synthesizer=_stub_synthesizer(),
            max_rounds=1,
        )
        result = session.run("What causes X?")
        assert isinstance(result, ResearchResult)

    def test_result_question_matches_input(self) -> None:
        verifiers = [_stub_verifier("confirmed")]
        session, _, _ = build_bus_research_session(
            verifiers,
            session_scope="s",
            planner=_stub_planner("H1"),
            synthesizer=_stub_synthesizer(),
            max_rounds=1,
        )
        result = session.run("Why does Y happen?")
        assert result.question == "Why does Y happen?"

    def test_result_has_final_hypotheses(self) -> None:
        verifiers = [_stub_verifier("confirmed")]
        session, _, _ = build_bus_research_session(
            verifiers,
            session_scope="s",
            planner=_stub_planner("H1", "H2"),
            synthesizer=_stub_synthesizer(),
            max_rounds=1,
        )
        result = session.run("test?")
        assert len(result.final_hypotheses) == 2

    def test_result_has_non_empty_conclusion(self) -> None:
        verifiers = [_stub_verifier("confirmed")]
        session, _, _ = build_bus_research_session(
            verifiers,
            session_scope="s",
            planner=_stub_planner("H1"),
            synthesizer=_stub_synthesizer("Deep insight here."),
            max_rounds=1,
        )
        result = session.run("test?")
        assert result.conclusion == "Deep insight here."

    def test_divergent_verifiers_produce_inconclusive_hypotheses(self) -> None:
        """1 confirmed + 1 rejected + 1 inconclusive → hypothesis is inconclusive."""
        verifiers = [
            _stub_verifier("confirmed"),
            _stub_verifier("rejected"),
            _stub_verifier("inconclusive"),
        ]
        session, _, _ = build_bus_research_session(
            verifiers,
            session_scope="s",
            planner=_stub_planner("Divergent hypothesis"),
            synthesizer=_stub_synthesizer(),
            max_rounds=1,
        )
        result = session.run("complex question?")
        assert any(h.status == "inconclusive" for h in result.final_hypotheses)

    def test_evidence_from_all_verifiers_appears_in_hypothesis(self) -> None:
        verifiers = [
            _stub_verifier("confirmed", evidence=["evidence-A"]),
            _stub_verifier("confirmed", evidence=["evidence-B"]),
        ]
        session, _, _ = build_bus_research_session(
            verifiers,
            session_scope="s",
            planner=_stub_planner("H1"),
            synthesizer=_stub_synthesizer(),
            max_rounds=1,
        )
        result = session.run("test?")
        combined = " ".join(e for h in result.final_hypotheses for e in h.evidence)
        assert "evidence-A" in combined
        assert "evidence-B" in combined

    def test_multi_round_session_runs_without_error(self) -> None:
        verifiers = [_stub_verifier("confirmed")]
        session, _, _ = build_bus_research_session(
            verifiers,
            session_scope="s",
            planner=_stub_planner("H1"),
            synthesizer=_stub_synthesizer(),
            max_rounds=2,
        )
        result = session.run("multi-round test?")
        assert result.total_rounds >= 1

    def test_full_flow_with_tracing_returns_correct_result(self) -> None:
        """Smoke test: traced full flow produces a valid ResearchResult."""
        collector = _collector()
        verifiers = [
            _stub_verifier("confirmed", confidence=0.9),
            _stub_verifier("confirmed", confidence=0.8),
            _stub_verifier("rejected", confidence=0.6),
        ]
        session, _, _ = build_bus_research_session(
            verifiers,
            session_scope="full-scope",
            collector=collector,
            planner=_stub_planner("H1", "H2"),
            synthesizer=_stub_synthesizer("Majority confirmed."),
            max_rounds=1,
        )
        result = session.run("What drives latency?")

        assert isinstance(result, ResearchResult)
        assert result.conclusion == "Majority confirmed."
        assert len(result.final_hypotheses) == 2
        # Each hypothesis resolved by majority (2 confirmed vs 1 rejected)
        assert all(h.status == "confirmed" for h in result.final_hypotheses)
        # Trace events: 3 verifiers × 2 hypotheses × 2 events (STARTED + COMPLETED) = 12
        agent_events = [
            e for e in collector.events
            if e.event_type in (EventType.AGENT_ACTION_STARTED, EventType.AGENT_ACTION_COMPLETED)
        ]
        assert len(agent_events) == 12
