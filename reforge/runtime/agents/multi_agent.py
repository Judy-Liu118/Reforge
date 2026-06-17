"""build_bus_research_session — assemble a full P18 multi-verifier stack.

Wires together all P18 components into a ResearchSession:

    session, bus, sender_ctx = build_bus_research_session(
        verifier_agents=[RunnerVerifier(...), RunnerVerifier(...)],
        session_scope="research-xyz",
        collector=trace_collector,   # optional
        planner=my_planner,          # optional — defaults to ResearchPlanner()
        synthesizer=my_synthesizer,  # optional — defaults to DefaultSynthesizer()
        max_rounds=3,
    )
    result = session.run("What causes latency spikes?")

Each verifier gets its own ActorContext(actor_role="verifier").  When a
TraceCollector is supplied every verify() call is wrapped in an AgentSpan so
AGENT_ACTION_STARTED / COMPLETED / FAILED events flow into the collector.

The returned (session, bus, sender_ctx) triple lets callers inspect routing
state and correlate trace events with specific actor_ids.

Import directly — not re-exported from agents/__init__ to avoid a potential
circular-import through research.session → agents.*.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from reforge.runtime.agents.bus import MessageBus
from reforge.runtime.agents.bus_verifier import BusVerifier, make_verifier_handler
from reforge.runtime.agents.identity import ActorContext
from reforge.runtime.agents.message import RuntimeMessage

if TYPE_CHECKING:
    from reforge.observability.tracing.collector import TraceCollector
    from reforge.runtime.agents.bus import MessageHandler
    from reforge.runtime.agents.role import (
        PlannerAgent,
        SynthesizerAgent,
        VerifierAgent,
    )
    from reforge.runtime.research.session import ResearchSession

_VERIFIER_ROLE = "verifier"
_ORCHESTRATOR_ROLE = "orchestrator"


def build_bus_research_session(
    verifier_agents: list[VerifierAgent],
    session_scope: str,
    *,
    collector: TraceCollector | None = None,
    planner: PlannerAgent | None = None,
    synthesizer: SynthesizerAgent | None = None,
    max_rounds: int = 3,
) -> tuple[ResearchSession, MessageBus, ActorContext]:
    """Return (ResearchSession, MessageBus, sender_ctx) backed by multi-verifier consensus.

    verifier_agents — one VerifierAgent per worker; each gets an independent
                      ActorContext so tracing can identify which worker acted.
    session_scope   — shared scope string used in every ActorContext and
                      AgentSpan.session_id so all spans are co-locatable.
    collector       — when provided, each verify() call emits AgentSpan events.
    """
    from reforge.runtime.research.session import ResearchSession

    bus = MessageBus()

    for agent in verifier_agents:
        ctx = ActorContext.create(actor_role=_VERIFIER_ROLE, session_scope=session_scope)
        handler: MessageHandler = (
            _make_traced_handler(ctx, agent, collector)
            if collector is not None
            else make_verifier_handler(ctx, agent)
        )
        bus.register(ctx, handler)

    sender_ctx = ActorContext.create(
        actor_role=_ORCHESTRATOR_ROLE, session_scope=session_scope
    )
    bus_verifier = BusVerifier(bus=bus, sender_ctx=sender_ctx)

    session = ResearchSession(
        verifier=bus_verifier,
        planner=planner,
        synthesizer=synthesizer,
        max_rounds=max_rounds,
    )
    return session, bus, sender_ctx


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_traced_handler(
    ctx: ActorContext,
    verifier: VerifierAgent,
    collector: TraceCollector,
) -> MessageHandler:
    """Return a MessageHandler that wraps *verifier* with AgentSpan tracing."""
    from reforge.observability.tracing.agent_span import AgentSpan
    from reforge.runtime.research.models import HypothesisRecord as HR

    def handler(msg: RuntimeMessage) -> RuntimeMessage:
        with AgentSpan.from_actor(
            collector, ctx, action="verify", correlation_id=msg.correlation_id
        ):
            hypothesis = HR.model_validate(msg.payload)
            result = verifier.verify(hypothesis)
        return RuntimeMessage.create(
            message_type="verify_result",
            sender=ctx.actor_id,
            recipient=msg.sender,
            payload=result.model_dump(),
            correlation_id=msg.correlation_id,
        )

    return handler
