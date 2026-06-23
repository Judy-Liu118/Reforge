"""P18.3 — Multi-verifier consensus: VerifierVoter + BusVerifier.

Test categories:
  1. VerifierVoter — pure voting logic (no I/O)
  2. make_verifier_handler — VerifierAgent → MessageHandler bridge
  3. BusVerifier — fan-out + consensus via MessageBus
  4. Protocol conformance — BusVerifier satisfies VerifierAgent
  5. End-to-end — 3-verifier divergence resolution
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from reforge.runtime.agents.bus import MessageBus
from reforge.runtime.agents.bus_verifier import BusVerifier, make_verifier_handler
from reforge.runtime.agents.identity import ActorContext
from reforge.runtime.agents.role import VerifierAgent
from reforge.runtime.agents.voter import VerifierVoter
from reforge.runtime.research.models import HypothesisRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hyp(
    text: str = "H",
    status: str = "pending",
    confidence: float = 0.0,
    evidence: list[str] | None = None,
) -> HypothesisRecord:
    return HypothesisRecord(
        hypothesis=text,
        verification_request=f"check {text}",
        status=status,  # type: ignore[arg-type]
        confidence=confidence,
        evidence=evidence or [],
    )


def _ctx(role: str = "verifier", scope: str = "sess") -> ActorContext:
    return ActorContext.create(actor_role=role, session_scope=scope)


def _stub_verifier(status: str, confidence: float = 0.5, evidence: list[str] | None = None):
    """Return a minimal VerifierAgent-shaped stub."""
    m = MagicMock()
    m.verify.side_effect = lambda h: h.model_copy(update={
        "status": status,
        "confidence": confidence,
        "evidence": evidence or [f"evidence-for-{status}"],
    })
    return m


# ---------------------------------------------------------------------------
# 1. VerifierVoter
# ---------------------------------------------------------------------------


class TestVerifierVoter:
    def test_empty_results_returns_inconclusive(self) -> None:
        voter = VerifierVoter()
        result = voter.vote([])
        assert result.status == "inconclusive"
        assert result.confidence == 0.0

    def test_empty_results_with_original_preserves_identity(self) -> None:
        """Voter must retain hypothesis identity when caller passes original."""
        voter = VerifierVoter()
        original = HypothesisRecord(
            hypothesis="caching layer faulty",
            verification_request="probe cache hit ratio",
            round_number=2,
        )
        result = voter.vote([], original=original)
        assert result.status == "inconclusive"
        assert result.confidence == 0.0
        assert result.hypothesis_id == original.hypothesis_id
        assert result.hypothesis == "caching layer faulty"
        assert result.verification_request == "probe cache hit ratio"
        assert result.round_number == 2
        assert result.evidence == []

    def test_non_empty_results_ignore_original(self) -> None:
        """When results exist, results[0] is the identity base, not original."""
        voter = VerifierVoter()
        original = HypothesisRecord(hypothesis="ignored")
        results = [_hyp(text="winner", status="confirmed", confidence=0.9)]
        r = voter.vote(results, original=original)
        assert r.hypothesis == "winner"

    def test_single_confirmed_passes_through(self) -> None:
        voter = VerifierVoter()
        r = voter.vote([_hyp(status="confirmed", confidence=0.9)])
        assert r.status == "confirmed"

    def test_single_rejected_passes_through(self) -> None:
        voter = VerifierVoter()
        r = voter.vote([_hyp(status="rejected", confidence=0.8)])
        assert r.status == "rejected"

    def test_all_confirmed_gives_confirmed(self) -> None:
        voter = VerifierVoter()
        results = [_hyp(status="confirmed") for _ in range(3)]
        assert voter.vote(results).status == "confirmed"

    def test_all_rejected_gives_rejected(self) -> None:
        voter = VerifierVoter()
        results = [_hyp(status="rejected") for _ in range(3)]
        assert voter.vote(results).status == "rejected"

    def test_all_inconclusive_gives_inconclusive(self) -> None:
        voter = VerifierVoter()
        results = [_hyp(status="inconclusive") for _ in range(3)]
        assert voter.vote(results).status == "inconclusive"

    def test_majority_confirmed_wins(self) -> None:
        voter = VerifierVoter()
        results = [
            _hyp(status="confirmed"),
            _hyp(status="confirmed"),
            _hyp(status="rejected"),
        ]
        assert voter.vote(results).status == "confirmed"

    def test_majority_rejected_wins(self) -> None:
        voter = VerifierVoter()
        results = [
            _hyp(status="confirmed"),
            _hyp(status="rejected"),
            _hyp(status="rejected"),
        ]
        assert voter.vote(results).status == "rejected"

    def test_exact_tie_gives_inconclusive(self) -> None:
        voter = VerifierVoter()
        results = [_hyp(status="confirmed"), _hyp(status="rejected")]
        assert voter.vote(results).status == "inconclusive"

    def test_split_three_ways_gives_inconclusive(self) -> None:
        voter = VerifierVoter()
        results = [
            _hyp(status="confirmed"),
            _hyp(status="rejected"),
            _hyp(status="inconclusive"),
        ]
        assert voter.vote(results).status == "inconclusive"

    def test_confidence_is_averaged(self) -> None:
        voter = VerifierVoter()
        results = [
            _hyp(status="confirmed", confidence=0.9),
            _hyp(status="confirmed", confidence=0.7),
            _hyp(status="confirmed", confidence=0.5),
        ]
        result = voter.vote(results)
        assert result.confidence == pytest.approx(0.7)

    def test_confidence_clamped_to_one(self) -> None:
        voter = VerifierVoter()
        # pydantic clamps at 1.0; voter must also not exceed it
        results = [_hyp(status="confirmed", confidence=1.0) for _ in range(5)]
        assert voter.vote(results).confidence <= 1.0

    def test_evidence_is_aggregated_from_all_results(self) -> None:
        voter = VerifierVoter()
        results = [
            _hyp(status="confirmed", evidence=["e1"]),
            _hyp(status="confirmed", evidence=["e2"]),
            _hyp(status="confirmed", evidence=["e3"]),
        ]
        ev = voter.vote(results).evidence
        assert "e1" in ev
        assert "e2" in ev
        assert "e3" in ev

    def test_evidence_deduplicates_identical_strings(self) -> None:
        voter = VerifierVoter()
        results = [
            _hyp(status="confirmed", evidence=["shared evidence"]),
            _hyp(status="confirmed", evidence=["shared evidence"]),
        ]
        ev = voter.vote(results).evidence
        assert ev.count("shared evidence") == 1

    def test_hypothesis_id_preserved_from_first_result(self) -> None:
        voter = VerifierVoter()
        first = _hyp(status="confirmed")
        second = _hyp(status="confirmed")
        result = voter.vote([first, second])
        assert result.hypothesis_id == first.hypothesis_id


# ---------------------------------------------------------------------------
# 2. make_verifier_handler
# ---------------------------------------------------------------------------


class TestMakeVerifierHandler:
    def test_handler_calls_verifier_verify(self) -> None:
        from reforge.runtime.agents.message import RuntimeMessage

        ctx = _ctx()
        stub = _stub_verifier("confirmed")
        handler = make_verifier_handler(ctx, stub)

        hyp = _hyp("H1")
        msg = RuntimeMessage.create(
            message_type="verify_request",
            sender="planner-1",
            recipient=ctx.actor_id,
            payload=hyp.model_dump(),
        )
        handler(msg)
        stub.verify.assert_called_once()

    def test_handler_returns_verify_result_message_type(self) -> None:
        from reforge.runtime.agents.message import RuntimeMessage

        ctx = _ctx()
        handler = make_verifier_handler(ctx, _stub_verifier("confirmed"))
        hyp = _hyp()
        msg = RuntimeMessage.create(
            message_type="verify_request",
            sender="s",
            recipient=ctx.actor_id,
            payload=hyp.model_dump(),
        )
        response = handler(msg)
        assert response.message_type == "verify_result"

    def test_handler_preserves_correlation_id(self) -> None:
        import uuid
        from reforge.runtime.agents.message import RuntimeMessage

        ctx = _ctx()
        handler = make_verifier_handler(ctx, _stub_verifier("confirmed"))
        cid = str(uuid.uuid4())
        msg = RuntimeMessage.create(
            message_type="verify_request",
            sender="s",
            recipient=ctx.actor_id,
            payload=_hyp().model_dump(),
            correlation_id=cid,
        )
        response = handler(msg)
        assert response.correlation_id == cid

    def test_handler_serialises_result_into_payload(self) -> None:
        from reforge.runtime.agents.message import RuntimeMessage

        ctx = _ctx()
        handler = make_verifier_handler(ctx, _stub_verifier("rejected", confidence=0.3))
        msg = RuntimeMessage.create(
            message_type="verify_request",
            sender="s",
            recipient=ctx.actor_id,
            payload=_hyp().model_dump(),
        )
        response = handler(msg)
        assert response.payload["status"] == "rejected"
        assert response.payload["confidence"] == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# 3. BusVerifier
# ---------------------------------------------------------------------------


class TestBusVerifier:
    def _build_bus(self, *statuses: str) -> tuple[MessageBus, ActorContext]:
        """Register one stub verifier per status on a fresh bus."""
        bus = MessageBus()
        for status in statuses:
            ctx = _ctx("verifier")
            bus.register(ctx, make_verifier_handler(ctx, _stub_verifier(status)))
        sender = _ctx("orchestrator")
        return bus, sender

    def test_satisfies_verifier_agent_protocol(self) -> None:
        bus, sender = self._build_bus("confirmed")
        bv = BusVerifier(bus=bus, sender_ctx=sender)
        assert isinstance(bv, VerifierAgent)

    def test_single_verifier_passes_through(self) -> None:
        bus, sender = self._build_bus("confirmed")
        bv = BusVerifier(bus=bus, sender_ctx=sender)
        result = bv.verify(_hyp())
        assert result.status == "confirmed"

    def test_broadcasts_to_all_registered_verifiers(self) -> None:
        call_count = 0
        bus = MessageBus()
        for _ in range(3):
            ctx = _ctx("verifier")

            def make_counting_handler(c: ActorContext):
                def h(msg):
                    nonlocal call_count
                    call_count += 1
                    from reforge.runtime.agents.message import RuntimeMessage
                    hyp = HypothesisRecord.model_validate(msg.payload)
                    result = hyp.model_copy(update={"status": "confirmed", "confidence": 0.8})
                    return RuntimeMessage.create(
                        message_type="verify_result",
                        sender=c.actor_id,
                        recipient=msg.sender,
                        payload=result.model_dump(),
                        correlation_id=msg.correlation_id,
                    )
                return h

            bus.register(ctx, make_counting_handler(ctx))

        sender = _ctx("orchestrator")
        bv = BusVerifier(bus=bus, sender_ctx=sender)
        bv.verify(_hyp())
        assert call_count == 3

    def test_majority_confirmed_from_three_verifiers(self) -> None:
        bus, sender = self._build_bus("confirmed", "confirmed", "rejected")
        bv = BusVerifier(bus=bus, sender_ctx=sender)
        result = bv.verify(_hyp())
        assert result.status == "confirmed"

    def test_majority_rejected_from_three_verifiers(self) -> None:
        bus, sender = self._build_bus("rejected", "rejected", "confirmed")
        bv = BusVerifier(bus=bus, sender_ctx=sender)
        result = bv.verify(_hyp())
        assert result.status == "rejected"

    def test_no_majority_gives_inconclusive(self) -> None:
        bus, sender = self._build_bus("confirmed", "rejected", "inconclusive")
        bv = BusVerifier(bus=bus, sender_ctx=sender)
        result = bv.verify(_hyp())
        assert result.status == "inconclusive"

    def test_custom_voter_is_used(self) -> None:
        bus, sender = self._build_bus("confirmed", "confirmed")
        voter = MagicMock(spec=VerifierVoter)
        voter.vote.return_value = _hyp(status="inconclusive")
        bv = BusVerifier(bus=bus, sender_ctx=sender, voter=voter)
        result = bv.verify(_hyp())
        voter.vote.assert_called_once()
        assert result.status == "inconclusive"

    def test_correlation_id_propagated_to_all_handlers(self) -> None:
        correlation_ids_seen: list[str] = []
        bus = MessageBus()
        for _ in range(3):
            ctx = _ctx("verifier")

            def make_cid_handler(c: ActorContext):
                def h(msg):
                    from reforge.runtime.agents.message import RuntimeMessage
                    correlation_ids_seen.append(msg.correlation_id)
                    hyp = HypothesisRecord.model_validate(msg.payload)
                    result = hyp.model_copy(update={"status": "confirmed", "confidence": 0.9})
                    return RuntimeMessage.create(
                        message_type="verify_result",
                        sender=c.actor_id,
                        recipient=msg.sender,
                        payload=result.model_dump(),
                        correlation_id=msg.correlation_id,
                    )
                return h

            bus.register(ctx, make_cid_handler(ctx))

        sender = _ctx("orchestrator")
        bv = BusVerifier(bus=bus, sender_ctx=sender)
        bv.verify(_hyp())

        assert len(correlation_ids_seen) == 3
        assert len(set(correlation_ids_seen)) == 1, "all handlers must share one correlation_id"


# ---------------------------------------------------------------------------
# 4. End-to-end: divergence resolution
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_three_independent_verifiers_reach_consensus(self) -> None:
        """Full fan-out: 2 confirmed + 1 rejected → confirmed by majority."""
        bus = MessageBus()
        for status in ("confirmed", "confirmed", "rejected"):
            ctx = _ctx("verifier")
            bus.register(ctx, make_verifier_handler(ctx, _stub_verifier(status, confidence=0.7)))

        sender = _ctx("orchestrator")
        bv = BusVerifier(bus=bus, sender_ctx=sender)

        hypothesis = HypothesisRecord(
            hypothesis="Increasing batch size reduces latency",
            verification_request="measure latency at batch 16 vs 32",
        )
        result = bv.verify(hypothesis)

        assert result.status == "confirmed"
        assert result.hypothesis_id == hypothesis.hypothesis_id
        assert result.confidence == pytest.approx(0.7)

    def test_fully_divergent_verifiers_give_inconclusive(self) -> None:
        """1 confirmed + 1 rejected + 1 inconclusive → inconclusive."""
        bus = MessageBus()
        for status in ("confirmed", "rejected", "inconclusive"):
            ctx = _ctx("verifier")
            bus.register(ctx, make_verifier_handler(ctx, _stub_verifier(status)))

        sender = _ctx("orchestrator")
        bv = BusVerifier(bus=bus, sender_ctx=sender)
        result = bv.verify(_hyp("Divergent hypothesis"))
        assert result.status == "inconclusive"

    def test_bus_verifier_injectable_into_research_session(self) -> None:
        """BusVerifier satisfies VerifierAgent; ResearchSession accepts it."""
        from reforge.runtime.research.session import ResearchSession

        bus = MessageBus()
        for status in ("confirmed", "confirmed"):
            ctx = _ctx("verifier")
            bus.register(ctx, make_verifier_handler(ctx, _stub_verifier(status)))

        sender = _ctx("orchestrator")
        bv = BusVerifier(bus=bus, sender_ctx=sender)

        # Just check it's accepted by ResearchSession without error
        session = ResearchSession(verifier=bv)
        assert isinstance(bv, VerifierAgent)
        assert session is not None

    def test_evidence_aggregated_across_all_verifiers(self) -> None:
        """Evidence strings from each verifier are merged in the consensus."""
        bus = MessageBus()
        for i, status in enumerate(("confirmed", "confirmed", "confirmed")):
            ctx = _ctx("verifier")
            bus.register(
                ctx,
                make_verifier_handler(
                    ctx, _stub_verifier(status, evidence=[f"unique-evidence-{i}"])
                ),
            )

        sender = _ctx("orchestrator")
        bv = BusVerifier(bus=bus, sender_ctx=sender)
        result = bv.verify(_hyp())

        evidence_text = " ".join(result.evidence)
        assert "unique-evidence-0" in evidence_text
        assert "unique-evidence-1" in evidence_text
        assert "unique-evidence-2" in evidence_text
