"""P19 — Distributed Tracing: SpanContext + AgentSpan extension + TraceTree.

Test categories:
  1. SpanContext — construction, root/child factories, immutability
  2. AgentSpan with SpanContext — span fields in metadata, backwards compat
  3. TraceTree.build() — single span, parent-child, multi-level, mixed events
  4. TraceTree helpers — all_nodes(), trace_ids()
  5. render_trace_tree — text output format
  6. End-to-end — multi-agent session builds a linked span tree
"""

from __future__ import annotations

import pytest

from reforge.observability.tracing.agent_span import AgentSpan
from reforge.observability.tracing.collector import TraceCollector
from reforge.observability.tracing.models import EventType
from reforge.observability.tracing.span_context import SpanContext
from reforge.observability.tracing.tree import TraceNode, TraceTree, render_trace_tree
from reforge.runtime.agents.identity import ActorContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collector() -> TraceCollector:
    return TraceCollector(session_id="test-session")


def _ctx(role: str = "verifier", scope: str = "scope-test") -> ActorContext:
    return ActorContext.create(actor_role=role, session_scope=scope)


def _run_span(
    collector: TraceCollector,
    ctx: ActorContext,
    action: str,
    span_context: SpanContext | None = None,
    raise_exc: Exception | None = None,
) -> None:
    with AgentSpan.from_actor(
        collector, ctx, action=action, span_context=span_context
    ):
        if raise_exc is not None:
            raise raise_exc


# ---------------------------------------------------------------------------
# 1. SpanContext
# ---------------------------------------------------------------------------


class TestSpanContext:
    def test_root_has_empty_parent(self) -> None:
        ctx = SpanContext.root()
        assert ctx.parent_span_id == ""

    def test_root_is_root(self) -> None:
        assert SpanContext.root().is_root

    def test_root_generates_trace_id(self) -> None:
        ctx = SpanContext.root()
        assert ctx.trace_id
        assert len(ctx.trace_id) > 0

    def test_root_generates_span_id(self) -> None:
        ctx = SpanContext.root()
        assert ctx.span_id
        assert len(ctx.span_id) > 0

    def test_two_roots_have_different_trace_ids(self) -> None:
        assert SpanContext.root().trace_id != SpanContext.root().trace_id

    def test_root_with_explicit_trace_id(self) -> None:
        ctx = SpanContext.root(trace_id="fixed-trace")
        assert ctx.trace_id == "fixed-trace"

    def test_child_inherits_trace_id(self) -> None:
        parent = SpanContext.root()
        child = parent.child()
        assert child.trace_id == parent.trace_id

    def test_child_parent_id_is_parent_span_id(self) -> None:
        parent = SpanContext.root()
        child = parent.child()
        assert child.parent_span_id == parent.span_id

    def test_child_has_different_span_id(self) -> None:
        parent = SpanContext.root()
        child = parent.child()
        assert child.span_id != parent.span_id

    def test_child_is_not_root(self) -> None:
        parent = SpanContext.root()
        child = parent.child()
        assert not child.is_root

    def test_grandchild_inherits_trace_id(self) -> None:
        root = SpanContext.root()
        child = root.child()
        grandchild = child.child()
        assert grandchild.trace_id == root.trace_id

    def test_frozen_prevents_mutation(self) -> None:
        ctx = SpanContext.root()
        with pytest.raises((TypeError, AttributeError)):
            ctx.trace_id = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. AgentSpan with SpanContext
# ---------------------------------------------------------------------------


class TestAgentSpanWithSpanContext:
    def test_started_event_carries_span_id(self) -> None:
        collector = _collector()
        span_ctx = SpanContext.root()
        _run_span(collector, _ctx(), "verify", span_context=span_ctx)
        started = next(e for e in collector.events if e.event_type == EventType.AGENT_ACTION_STARTED)
        assert started.metadata["span_id"] == span_ctx.span_id

    def test_started_event_carries_trace_id(self) -> None:
        collector = _collector()
        span_ctx = SpanContext.root()
        _run_span(collector, _ctx(), "verify", span_context=span_ctx)
        started = next(e for e in collector.events if e.event_type == EventType.AGENT_ACTION_STARTED)
        assert started.metadata["trace_id"] == span_ctx.trace_id

    def test_started_event_carries_parent_span_id(self) -> None:
        collector = _collector()
        root = SpanContext.root()
        child = root.child()
        _run_span(collector, _ctx(), "verify", span_context=child)
        started = next(e for e in collector.events if e.event_type == EventType.AGENT_ACTION_STARTED)
        assert started.metadata["parent_span_id"] == root.span_id

    def test_completed_event_also_carries_span_id(self) -> None:
        collector = _collector()
        span_ctx = SpanContext.root()
        _run_span(collector, _ctx(), "verify", span_context=span_ctx)
        completed = next(e for e in collector.events if e.event_type == EventType.AGENT_ACTION_COMPLETED)
        assert completed.metadata["span_id"] == span_ctx.span_id

    def test_without_span_context_no_span_id_in_metadata(self) -> None:
        """Backwards compatibility: no SpanContext → no span fields."""
        collector = _collector()
        _run_span(collector, _ctx(), "verify", span_context=None)
        started = next(e for e in collector.events if e.event_type == EventType.AGENT_ACTION_STARTED)
        assert "span_id" not in started.metadata

    def test_without_span_context_actor_fields_still_present(self) -> None:
        """Core metadata unaffected by span_context absence."""
        collector = _collector()
        ctx = _ctx(role="planner")
        _run_span(collector, ctx, "plan", span_context=None)
        started = next(e for e in collector.events if e.event_type == EventType.AGENT_ACTION_STARTED)
        assert started.metadata["actor_role"] == "planner"

    def test_failed_event_carries_span_id(self) -> None:
        collector = _collector()
        span_ctx = SpanContext.root()
        with pytest.raises(ValueError):
            _run_span(collector, _ctx(), "verify", span_context=span_ctx, raise_exc=ValueError("x"))
        failed = next(e for e in collector.events if e.event_type == EventType.AGENT_ACTION_FAILED)
        assert failed.metadata["span_id"] == span_ctx.span_id

    def test_child_span_parent_id_links_to_parent(self) -> None:
        collector = _collector()
        root = SpanContext.root()
        child = root.child()
        _run_span(collector, _ctx(), "plan", span_context=root)
        _run_span(collector, _ctx(), "verify", span_context=child)
        child_started = [
            e for e in collector.events
            if e.event_type == EventType.AGENT_ACTION_STARTED
            and e.metadata.get("span_id") == child.span_id
        ]
        assert child_started[0].metadata["parent_span_id"] == root.span_id


# ---------------------------------------------------------------------------
# 3. TraceTree.build()
# ---------------------------------------------------------------------------


class TestTraceTreeBuild:
    def test_empty_events_gives_empty_tree(self) -> None:
        tree = TraceTree([])
        assert tree.build() == []

    def test_events_without_span_id_are_ignored(self) -> None:
        """Legacy flat events (no span_id) do not appear in the tree."""
        collector = _collector()
        _run_span(collector, _ctx(), "verify", span_context=None)
        tree = TraceTree(collector.events)
        assert tree.build() == []

    def test_single_span_builds_one_root(self) -> None:
        collector = _collector()
        span_ctx = SpanContext.root()
        _run_span(collector, _ctx(), "verify", span_context=span_ctx)
        roots = TraceTree(collector.events).build()
        assert len(roots) == 1

    def test_root_node_has_correct_span_id(self) -> None:
        collector = _collector()
        span_ctx = SpanContext.root()
        _run_span(collector, _ctx(), "verify", span_context=span_ctx)
        root = TraceTree(collector.events).build()[0]
        assert root.span_id == span_ctx.span_id

    def test_root_node_has_correct_actor_role(self) -> None:
        collector = _collector()
        ctx = _ctx(role="planner")
        span_ctx = SpanContext.root()
        _run_span(collector, ctx, "plan", span_context=span_ctx)
        root = TraceTree(collector.events).build()[0]
        assert root.actor_role == "planner"

    def test_root_node_status_from_completed_event(self) -> None:
        collector = _collector()
        _run_span(collector, _ctx(), "verify", span_context=SpanContext.root())
        root = TraceTree(collector.events).build()[0]
        assert root.status == "OK"

    def test_root_node_status_from_failed_event(self) -> None:
        collector = _collector()
        with pytest.raises(RuntimeError):
            _run_span(
                collector, _ctx(), "verify",
                span_context=SpanContext.root(),
                raise_exc=RuntimeError("oops"),
            )
        root = TraceTree(collector.events).build()[0]
        assert root.status == "FAILED"

    def test_parent_child_tree(self) -> None:
        collector = _collector()
        root_sc = SpanContext.root()
        child_sc = root_sc.child()
        _run_span(collector, _ctx(role="planner"), "plan", span_context=root_sc)
        _run_span(collector, _ctx(role="verifier"), "verify", span_context=child_sc)
        roots = TraceTree(collector.events).build()
        assert len(roots) == 1
        assert len(roots[0].children) == 1

    def test_child_action_correct(self) -> None:
        collector = _collector()
        root_sc = SpanContext.root()
        child_sc = root_sc.child()
        _run_span(collector, _ctx(role="planner"), "plan", span_context=root_sc)
        _run_span(collector, _ctx(role="verifier"), "verify", span_context=child_sc)
        child = TraceTree(collector.events).build()[0].children[0]
        assert child.action == "verify"

    def test_multiple_children_attached_to_one_parent(self) -> None:
        collector = _collector()
        root_sc = SpanContext.root()
        _run_span(collector, _ctx(role="planner"), "plan", span_context=root_sc)
        for _ in range(3):
            _run_span(
                collector, _ctx(role="verifier"), "verify",
                span_context=root_sc.child(),
            )
        roots = TraceTree(collector.events).build()
        assert len(roots) == 1
        assert len(roots[0].children) == 3

    def test_two_independent_trees(self) -> None:
        collector = _collector()
        _run_span(collector, _ctx(), "verify", span_context=SpanContext.root())
        _run_span(collector, _ctx(), "verify", span_context=SpanContext.root())
        roots = TraceTree(collector.events).build()
        assert len(roots) == 2


# ---------------------------------------------------------------------------
# 4. TraceTree helpers
# ---------------------------------------------------------------------------


class TestTraceTreeHelpers:
    def test_all_nodes_flat_single(self) -> None:
        collector = _collector()
        _run_span(collector, _ctx(), "verify", span_context=SpanContext.root())
        assert len(TraceTree(collector.events).all_nodes()) == 1

    def test_all_nodes_includes_children(self) -> None:
        collector = _collector()
        root_sc = SpanContext.root()
        _run_span(collector, _ctx(role="planner"), "plan", span_context=root_sc)
        _run_span(collector, _ctx(role="verifier"), "verify", span_context=root_sc.child())
        _run_span(collector, _ctx(role="verifier"), "verify", span_context=root_sc.child())
        assert len(TraceTree(collector.events).all_nodes()) == 3

    def test_trace_ids_single_trace(self) -> None:
        collector = _collector()
        root_sc = SpanContext.root()
        _run_span(collector, _ctx(), "verify", span_context=root_sc)
        _run_span(collector, _ctx(), "verify", span_context=root_sc.child())
        ids = TraceTree(collector.events).trace_ids()
        assert ids == {root_sc.trace_id}

    def test_trace_ids_multiple_traces(self) -> None:
        collector = _collector()
        sc1 = SpanContext.root()
        sc2 = SpanContext.root()
        _run_span(collector, _ctx(), "verify", span_context=sc1)
        _run_span(collector, _ctx(), "verify", span_context=sc2)
        ids = TraceTree(collector.events).trace_ids()
        assert ids == {sc1.trace_id, sc2.trace_id}


# ---------------------------------------------------------------------------
# 5. render_trace_tree
# ---------------------------------------------------------------------------


class TestRenderTraceTree:
    def test_empty_roots_gives_empty_string(self) -> None:
        assert render_trace_tree([]) == ""

    def test_single_root_contains_role_and_action(self) -> None:
        collector = _collector()
        ctx = _ctx(role="planner")
        _run_span(collector, ctx, "plan", span_context=SpanContext.root())
        roots = TraceTree(collector.events).build()
        rendered = render_trace_tree(roots)
        assert "planner" in rendered
        assert "plan" in rendered

    def test_single_root_contains_span_id_prefix(self) -> None:
        collector = _collector()
        span_ctx = SpanContext.root()
        _run_span(collector, _ctx(), "verify", span_context=span_ctx)
        roots = TraceTree(collector.events).build()
        rendered = render_trace_tree(roots)
        assert span_ctx.span_id[:8] in rendered

    def test_child_is_indented(self) -> None:
        collector = _collector()
        root_sc = SpanContext.root()
        child_sc = root_sc.child()
        _run_span(collector, _ctx(role="planner"), "plan", span_context=root_sc)
        _run_span(collector, _ctx(role="verifier"), "verify", span_context=child_sc)
        roots = TraceTree(collector.events).build()
        rendered = render_trace_tree(roots)
        lines = rendered.splitlines()
        assert len(lines) == 2
        assert lines[1].startswith("  ")  # indented by 2 spaces

    def test_status_appears_in_output(self) -> None:
        collector = _collector()
        _run_span(collector, _ctx(), "verify", span_context=SpanContext.root())
        roots = TraceTree(collector.events).build()
        rendered = render_trace_tree(roots)
        assert "OK" in rendered


# ---------------------------------------------------------------------------
# 6. End-to-end: multi-agent session with span tree
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_full_session_builds_linked_span_tree(self) -> None:
        """Root span (orchestrator) → child spans (one per verifier call)."""
        from unittest.mock import MagicMock

        from reforge.runtime.agents.multi_agent import build_bus_research_session
        from reforge.runtime.agents.role import SynthesisResult
        from reforge.runtime.research.models import HypothesisRecord, ResearchPlan

        # Stub planner
        planner = MagicMock()
        planner.plan.side_effect = lambda q, pf=None, context="": ResearchPlan(
            question=q,
            hypotheses=[
                HypothesisRecord(hypothesis="H1", verification_request="check H1"),
                HypothesisRecord(hypothesis="H2", verification_request="check H2"),
            ],
        )
        # Stub synthesizer
        synth = MagicMock()
        synth.synthesize.return_value = SynthesisResult(conclusion="done", contradictions=[])

        # Stub verifiers (2 confirmed + 1 rejected)
        def _stub(status: str):
            m = MagicMock()
            m.verify.side_effect = lambda h: h.model_copy(
                update={"status": status, "confidence": 0.8, "evidence": [status]}
            )
            return m

        collector = TraceCollector(session_id="e2e-p19")

        # Build session — no span_context in factory yet; we test with bare AgentSpan
        # usage by manually wrapping verifiers with traced handlers carrying SpanContext
        root_ctx = SpanContext.root(trace_id="fixed-trace-id")

        # Wire bus manually to inject SpanContext
        from reforge.runtime.agents.bus import MessageBus
        from reforge.runtime.agents.bus_verifier import BusVerifier, make_verifier_handler
        from reforge.runtime.agents.identity import ActorContext
        from reforge.runtime.agents.voter import VerifierVoter
        from reforge.runtime.research.models import HypothesisRecord as HR
        from reforge.runtime.agents.message import RuntimeMessage

        bus = MessageBus()
        for sv in [_stub("confirmed"), _stub("confirmed"), _stub("rejected")]:
            actor_ctx = ActorContext.create("verifier", "e2e-scope")

            def _make_handler(av, ac, parent_sc):
                def handler(msg: RuntimeMessage) -> RuntimeMessage:
                    # Fresh child span per invocation — each hypothesis gets its own span
                    call_sc = parent_sc.child()
                    with AgentSpan.from_actor(
                        collector, ac, action="verify",
                        correlation_id=msg.correlation_id,
                        span_context=call_sc,
                    ):
                        h = HR.model_validate(msg.payload)
                        result = av.verify(h)
                    return RuntimeMessage.create(
                        message_type="verify_result",
                        sender=ac.actor_id,
                        recipient=msg.sender,
                        payload=result.model_dump(),
                        correlation_id=msg.correlation_id,
                    )
                return handler

            bus.register(actor_ctx, _make_handler(sv, actor_ctx, root_ctx))

        sender = ActorContext.create("orchestrator", "e2e-scope")
        bus_verifier = BusVerifier(bus=bus, sender_ctx=sender, voter=VerifierVoter())

        from reforge.runtime.research.session import ResearchSession
        session = ResearchSession(
            verifier=bus_verifier,
            planner=planner,
            synthesizer=synth,
            max_rounds=1,
        )
        result = session.run("test question")

        # The session produced results
        assert len(result.final_hypotheses) == 2
        assert all(h.status == "confirmed" for h in result.final_hypotheses)

        # The trace tree can be assembled from collector.events
        tree = TraceTree(collector.events)
        nodes = tree.all_nodes()
        # 3 verifiers × 2 hypotheses = 6 spans
        assert len(nodes) == 6

        # All spans share the same trace_id
        assert tree.trace_ids() == {"fixed-trace-id"}

        # render_trace_tree produces non-empty output
        roots = tree.build()
        rendered = render_trace_tree(roots)
        assert "verifier" in rendered
        assert "verify" in rendered

    def test_child_spans_link_to_root_via_parent_span_id(self) -> None:
        """Directly verify parent-child linkage in a two-level tree."""
        collector = _collector()
        root_sc = SpanContext.root()
        child_sc1 = root_sc.child()
        child_sc2 = root_sc.child()

        _run_span(collector, _ctx(role="orchestrator"), "session", span_context=root_sc)
        _run_span(collector, _ctx(role="verifier"), "verify", span_context=child_sc1)
        _run_span(collector, _ctx(role="verifier"), "verify", span_context=child_sc2)

        roots = TraceTree(collector.events).build()
        assert len(roots) == 1
        root_node = roots[0]
        assert root_node.actor_role == "orchestrator"
        assert len(root_node.children) == 2
        child_roles = {c.actor_role for c in root_node.children}
        assert child_roles == {"verifier"}
