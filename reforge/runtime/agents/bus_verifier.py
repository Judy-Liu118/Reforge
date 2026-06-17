"""BusVerifier — VerifierAgent that fans out via MessageBus for multi-verifier consensus.

Usage pattern:

    # 1. Build a bus and register individual verifiers as handlers
    bus = MessageBus()
    for runner_factory in worker_factories:
        ctx = ActorContext.create("verifier", session_scope)
        bus.register(ctx, make_verifier_handler(ctx, RunnerVerifier(runner_factory)))

    # 2. Create a BusVerifier — it satisfies VerifierAgent and can be injected into
    #    ResearchSession in place of a single RunnerVerifier
    sender_ctx = ActorContext.create("orchestrator", session_scope)
    bus_verifier = BusVerifier(bus=bus, sender_ctx=sender_ctx)

    # 3. Each verify() call fan-outs to ALL registered "verifier" handlers and
    #    returns the majority-vote consensus result
    result = bus_verifier.verify(hypothesis)

Wire-up helpers:
    make_verifier_handler(ctx, verifier_agent)
        Wraps a VerifierAgent as a MessageBus MessageHandler.
        Serialises HypothesisRecord → payload and back using Pydantic.
        Preserves correlation_id so spans stay traceable end-to-end.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from reforge.runtime.agents.bus import MessageBus
from reforge.runtime.agents.identity import ActorContext
from reforge.runtime.agents.message import RuntimeMessage
from reforge.runtime.agents.voter import VerifierVoter

if TYPE_CHECKING:
    from reforge.runtime.agents.role import VerifierAgent
    from reforge.runtime.research.models import HypothesisRecord

_VERIFY_REQUEST = "verify_request"
_VERIFY_RESULT = "verify_result"
_VERIFIER_ROLE = "verifier"


def make_verifier_handler(
    ctx: ActorContext,
    verifier: VerifierAgent,
):
    """Return a MessageBus handler that wraps *verifier*.

    The handler deserialises the request payload into a HypothesisRecord,
    calls verifier.verify(), serialises the result back into the response
    payload, and forwards the original correlation_id unchanged.
    """
    from reforge.runtime.research.models import HypothesisRecord as HR

    def handler(msg: RuntimeMessage) -> RuntimeMessage:
        hypothesis: HypothesisRecord = HR.model_validate(msg.payload)
        result: HypothesisRecord = verifier.verify(hypothesis)
        return RuntimeMessage.create(
            message_type=_VERIFY_RESULT,
            sender=ctx.actor_id,
            recipient=msg.sender,
            payload=result.model_dump(),
            correlation_id=msg.correlation_id,
        )

    return handler


class BusVerifier:
    """VerifierAgent backed by MessageBus fan-out + VerifierVoter consensus.

    Satisfies the VerifierAgent Protocol so it can be injected into
    ResearchSession(verifier=bus_verifier) without any other changes.

    On each verify() call:
      1. Serialise the hypothesis into a verify_request RuntimeMessage
      2. bus.send_all(message) → list[RuntimeMessage] from all "verifier" handlers
      3. Deserialise each response payload back to HypothesisRecord
      4. voter.vote(results) → single consensus HypothesisRecord
    """

    def __init__(
        self,
        bus: MessageBus,
        sender_ctx: ActorContext,
        voter: VerifierVoter | None = None,
    ) -> None:
        self._bus = bus
        self._sender_ctx = sender_ctx
        self._voter = voter or VerifierVoter()

    def verify(self, hypothesis: HypothesisRecord) -> HypothesisRecord:
        from reforge.runtime.research.models import HypothesisRecord as HR

        msg = RuntimeMessage.create(
            message_type=_VERIFY_REQUEST,
            sender=self._sender_ctx.actor_id,
            recipient=_VERIFIER_ROLE,
            payload=hypothesis.model_dump(),
        )
        responses = self._bus.send_all(msg)
        results: list[HypothesisRecord] = [
            HR.model_validate(r.payload) for r in responses
        ]
        return self._voter.vote(results, original=hypothesis)
