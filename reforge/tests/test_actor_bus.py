"""P18.2 — MessageBus: routing, dispatch, isolation, integration.

Test categories:
  1. Registration: register, has_handler, registered_actors, re-register
  2. send (single dispatch): actor_id routing, role routing, error cases
  3. send_all (broadcast): all handlers for role, ordering, fallback to actor_id
  4. Routing errors: missing recipient, empty bus
  5. Bus isolation: correlation_id preserved, handlers independent
  6. Integration: planner → verifier → synthesizer mini-pipeline via bus
"""

from __future__ import annotations

import uuid

import pytest

from reforge.runtime.agents.bus import BusRoutingError, MessageBus
from reforge.runtime.agents.identity import ActorContext
from reforge.runtime.agents.message import RuntimeMessage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(role: str, scope: str = "sess-1") -> ActorContext:
    return ActorContext.create(actor_role=role, session_scope=scope)


def _msg(
    message_type: str = "ping",
    sender: str = "s",
    recipient: str = "r",
    payload: dict | None = None,
    correlation_id: str | None = None,
) -> RuntimeMessage:
    return RuntimeMessage.create(
        message_type=message_type,
        sender=sender,
        recipient=recipient,
        payload=payload or {},
        correlation_id=correlation_id,
    )


def _echo_handler(msg: RuntimeMessage) -> RuntimeMessage:
    """Returns a pong carrying the same correlation_id."""
    return RuntimeMessage.create(
        message_type="pong",
        sender=msg.recipient,
        recipient=msg.sender,
        payload={"echo": msg.payload},
        correlation_id=msg.correlation_id,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_register_adds_handler(self) -> None:
        bus = MessageBus()
        ctx = _ctx("verifier")
        bus.register(ctx, _echo_handler)
        assert bus.has_handler(ctx.actor_id)

    def test_has_handler_true_by_actor_id(self) -> None:
        bus = MessageBus()
        ctx = _ctx("verifier")
        bus.register(ctx, _echo_handler)
        assert bus.has_handler(ctx.actor_id) is True

    def test_has_handler_true_by_role(self) -> None:
        bus = MessageBus()
        ctx = _ctx("verifier")
        bus.register(ctx, _echo_handler)
        assert bus.has_handler("verifier") is True

    def test_has_handler_false_before_register(self) -> None:
        bus = MessageBus()
        assert bus.has_handler("verifier") is False

    def test_registered_actors_returns_all_contexts(self) -> None:
        bus = MessageBus()
        p = _ctx("planner")
        v = _ctx("verifier")
        bus.register(p, _echo_handler)
        bus.register(v, _echo_handler)
        ids = {a.actor_id for a in bus.registered_actors()}
        assert p.actor_id in ids
        assert v.actor_id in ids

    def test_len_counts_registered_handlers(self) -> None:
        bus = MessageBus()
        bus.register(_ctx("planner"), _echo_handler)
        bus.register(_ctx("verifier"), _echo_handler)
        assert len(bus) == 2

    def test_re_register_same_actor_replaces_handler(self) -> None:
        bus = MessageBus()
        ctx = _ctx("verifier")
        calls: list[str] = []

        def handler_v1(msg: RuntimeMessage) -> RuntimeMessage:
            calls.append("v1")
            return _echo_handler(msg)

        def handler_v2(msg: RuntimeMessage) -> RuntimeMessage:
            calls.append("v2")
            return _echo_handler(msg)

        bus.register(ctx, handler_v1)
        bus.register(ctx, handler_v2)
        bus.send(_msg(recipient=ctx.actor_id))
        assert calls == ["v2"]

    def test_re_register_does_not_duplicate_role_index(self) -> None:
        bus = MessageBus()
        ctx = _ctx("verifier")
        bus.register(ctx, _echo_handler)
        bus.register(ctx, _echo_handler)
        responses = bus.send_all(_msg(recipient="verifier"))
        assert len(responses) == 1


# ---------------------------------------------------------------------------
# send — single dispatch
# ---------------------------------------------------------------------------


class TestSend:
    def test_send_by_exact_actor_id(self) -> None:
        bus = MessageBus()
        ctx = _ctx("verifier")
        bus.register(ctx, _echo_handler)
        response = bus.send(_msg(recipient=ctx.actor_id))
        assert response.message_type == "pong"

    def test_send_by_role_dispatches_to_first_registered(self) -> None:
        bus = MessageBus()
        ctx_a = _ctx("verifier")
        ctx_b = _ctx("verifier")
        calls: list[str] = []

        def handler_a(msg: RuntimeMessage) -> RuntimeMessage:
            calls.append("a")
            return _echo_handler(msg)

        def handler_b(msg: RuntimeMessage) -> RuntimeMessage:
            calls.append("b")
            return _echo_handler(msg)

        bus.register(ctx_a, handler_a)
        bus.register(ctx_b, handler_b)
        bus.send(_msg(recipient="verifier"))
        assert calls == ["a"]

    def test_send_returns_handler_response(self) -> None:
        bus = MessageBus()
        ctx = _ctx("synthesizer")

        def handler(msg: RuntimeMessage) -> RuntimeMessage:
            return RuntimeMessage.create(
                message_type="synthesis_result",
                sender=ctx.actor_id,
                recipient=msg.sender,
                payload={"conclusion": "done"},
                correlation_id=msg.correlation_id,
            )

        bus.register(ctx, handler)
        response = bus.send(_msg(recipient=ctx.actor_id))
        assert response.payload["conclusion"] == "done"

    def test_send_raises_bus_routing_error_for_unknown_recipient(self) -> None:
        bus = MessageBus()
        with pytest.raises(BusRoutingError):
            bus.send(_msg(recipient="ghost"))

    def test_send_raises_bus_routing_error_on_empty_bus(self) -> None:
        bus = MessageBus()
        with pytest.raises(BusRoutingError):
            bus.send(_msg(recipient="verifier"))

    def test_bus_routing_error_is_subclass_of_key_error(self) -> None:
        with pytest.raises(KeyError):
            raise BusRoutingError("test")

    def test_error_message_mentions_recipient(self) -> None:
        bus = MessageBus()
        with pytest.raises(BusRoutingError, match="ghost"):
            bus.send(_msg(recipient="ghost"))


# ---------------------------------------------------------------------------
# send_all — broadcast
# ---------------------------------------------------------------------------


class TestSendAll:
    def test_send_all_by_role_reaches_all_handlers(self) -> None:
        bus = MessageBus()
        calls: list[str] = []

        for label in ("v1", "v2", "v3"):
            ctx = _ctx("verifier")

            def make_handler(lbl: str):
                def h(msg: RuntimeMessage) -> RuntimeMessage:
                    calls.append(lbl)
                    return _echo_handler(msg)
                return h

            bus.register(ctx, make_handler(label))

        responses = bus.send_all(_msg(recipient="verifier"))
        assert len(responses) == 3
        assert set(calls) == {"v1", "v2", "v3"}

    def test_send_all_preserves_registration_order(self) -> None:
        bus = MessageBus()
        order: list[int] = []

        for i in range(3):
            ctx = _ctx("verifier")

            def make_handler(n: int):
                def h(msg: RuntimeMessage) -> RuntimeMessage:
                    order.append(n)
                    return _echo_handler(msg)
                return h

            bus.register(ctx, make_handler(i))

        bus.send_all(_msg(recipient="verifier"))
        assert order == [0, 1, 2]

    def test_send_all_returns_list(self) -> None:
        bus = MessageBus()
        ctx = _ctx("verifier")
        bus.register(ctx, _echo_handler)
        result = bus.send_all(_msg(recipient="verifier"))
        assert isinstance(result, list)
        assert len(result) == 1

    def test_send_all_fallback_to_actor_id(self) -> None:
        bus = MessageBus()
        ctx = _ctx("verifier")
        bus.register(ctx, _echo_handler)
        result = bus.send_all(_msg(recipient=ctx.actor_id))
        assert len(result) == 1
        assert result[0].message_type == "pong"

    def test_send_all_raises_for_unknown_role(self) -> None:
        bus = MessageBus()
        with pytest.raises(BusRoutingError):
            bus.send_all(_msg(recipient="ghost-role"))

    def test_send_all_single_role_one_handler(self) -> None:
        bus = MessageBus()
        ctx = _ctx("synthesizer")
        bus.register(ctx, _echo_handler)
        responses = bus.send_all(_msg(recipient="synthesizer"))
        assert len(responses) == 1


# ---------------------------------------------------------------------------
# Bus isolation
# ---------------------------------------------------------------------------


class TestBusIsolation:
    def test_correlation_id_preserved_through_handler(self) -> None:
        bus = MessageBus()
        ctx = _ctx("verifier")
        bus.register(ctx, _echo_handler)

        cid = str(uuid.uuid4())
        request = _msg(recipient=ctx.actor_id, correlation_id=cid)
        response = bus.send(request)

        assert response.correlation_id == cid

    def test_correlation_id_preserved_in_send_all(self) -> None:
        bus = MessageBus()
        for _ in range(3):
            bus.register(_ctx("verifier"), _echo_handler)

        cid = str(uuid.uuid4())
        responses = bus.send_all(_msg(recipient="verifier", correlation_id=cid))
        assert all(r.correlation_id == cid for r in responses)

    def test_handlers_do_not_share_mutable_state(self) -> None:
        bus = MessageBus()
        state_a: list[int] = []
        state_b: list[int] = []

        def handler_a(msg: RuntimeMessage) -> RuntimeMessage:
            state_a.append(1)
            return _echo_handler(msg)

        def handler_b(msg: RuntimeMessage) -> RuntimeMessage:
            state_b.append(2)
            return _echo_handler(msg)

        ctx_a = _ctx("verifier")
        ctx_b = _ctx("verifier")
        bus.register(ctx_a, handler_a)
        bus.register(ctx_b, handler_b)

        bus.send_all(_msg(recipient="verifier"))

        assert state_a == [1]
        assert state_b == [2]
        assert state_a is not state_b

    def test_bus_repr_shows_role_counts(self) -> None:
        bus = MessageBus()
        bus.register(_ctx("planner"), _echo_handler)
        bus.register(_ctx("verifier"), _echo_handler)
        bus.register(_ctx("verifier"), _echo_handler)
        r = repr(bus)
        assert "planner" in r
        assert "verifier" in r


# ---------------------------------------------------------------------------
# Integration: planner → verifier → synthesizer via bus
# ---------------------------------------------------------------------------


class TestBusIntegration:
    def test_planner_to_verifier_round_trip(self) -> None:
        """Verifier receives a verify_request and returns a verify_result."""
        bus = MessageBus()
        planner_ctx = _ctx("planner")
        verifier_ctx = _ctx("verifier")

        def verifier_handler(msg: RuntimeMessage) -> RuntimeMessage:
            assert msg.message_type == "verify_request"
            return RuntimeMessage.create(
                message_type="verify_result",
                sender=verifier_ctx.actor_id,
                recipient=msg.sender,
                payload={"status": "confirmed", "evidence": ["data found"]},
                correlation_id=msg.correlation_id,
            )

        bus.register(verifier_ctx, verifier_handler)

        request = RuntimeMessage.create(
            message_type="verify_request",
            sender=planner_ctx.actor_id,
            recipient=verifier_ctx.actor_id,
            payload={"hypothesis": "X causes Y", "verification_request": "check X"},
        )
        response = bus.send(request)

        assert response.payload["status"] == "confirmed"
        assert response.correlation_id == request.correlation_id

    def test_verifier_to_synthesizer_round_trip(self) -> None:
        """Synthesizer receives aggregated results and returns conclusion."""
        bus = MessageBus()
        verifier_ctx = _ctx("verifier")
        synthesizer_ctx = _ctx("synthesizer")

        def synthesizer_handler(msg: RuntimeMessage) -> RuntimeMessage:
            assert msg.message_type == "synthesize_request"
            hypotheses = msg.payload.get("hypotheses", [])
            return RuntimeMessage.create(
                message_type="synthesize_result",
                sender=synthesizer_ctx.actor_id,
                recipient=msg.sender,
                payload={"conclusion": f"Based on {len(hypotheses)} hypotheses"},
                correlation_id=msg.correlation_id,
            )

        bus.register(synthesizer_ctx, synthesizer_handler)

        request = RuntimeMessage.create(
            message_type="synthesize_request",
            sender=verifier_ctx.actor_id,
            recipient="synthesizer",
            payload={"question": "Why X?", "hypotheses": ["H1", "H2"]},
        )
        response = bus.send(request)

        assert "2 hypotheses" in response.payload["conclusion"]
        assert response.correlation_id == request.correlation_id

    def test_full_pipeline_correlation_chain(self) -> None:
        """A single correlation_id links planner→verifier→synthesizer messages."""
        bus = MessageBus()
        planner_ctx = _ctx("planner")
        verifier_ctx = _ctx("verifier")
        synthesizer_ctx = _ctx("synthesizer")

        correlation_ids_seen: list[str] = []

        def verifier_handler(msg: RuntimeMessage) -> RuntimeMessage:
            correlation_ids_seen.append(msg.correlation_id)
            return RuntimeMessage.create(
                message_type="verify_result",
                sender=verifier_ctx.actor_id,
                recipient=msg.sender,
                payload={"status": "confirmed"},
                correlation_id=msg.correlation_id,
            )

        def synthesizer_handler(msg: RuntimeMessage) -> RuntimeMessage:
            correlation_ids_seen.append(msg.correlation_id)
            return RuntimeMessage.create(
                message_type="synthesize_result",
                sender=synthesizer_ctx.actor_id,
                recipient=msg.sender,
                payload={"conclusion": "ok"},
                correlation_id=msg.correlation_id,
            )

        bus.register(verifier_ctx, verifier_handler)
        bus.register(synthesizer_ctx, synthesizer_handler)

        cid = str(uuid.uuid4())

        bus.send(RuntimeMessage.create(
            message_type="verify_request",
            sender=planner_ctx.actor_id,
            recipient=verifier_ctx.actor_id,
            payload={"hypothesis": "H1"},
            correlation_id=cid,
        ))
        bus.send(RuntimeMessage.create(
            message_type="synthesize_request",
            sender=planner_ctx.actor_id,
            recipient="synthesizer",
            payload={"results": ["confirmed"]},
            correlation_id=cid,
        ))

        assert all(cid_seen == cid for cid_seen in correlation_ids_seen)
        assert len(correlation_ids_seen) == 2

    def test_role_routing_enables_multi_verifier_foundation(self) -> None:
        """send_all to 'verifier' role reaches every registered verifier."""
        bus = MessageBus()
        planner_ctx = _ctx("planner")
        statuses: list[str] = []

        for outcome in ("confirmed", "rejected", "inconclusive"):
            ctx = _ctx("verifier")

            def make_handler(status: str):
                def h(msg: RuntimeMessage) -> RuntimeMessage:
                    statuses.append(status)
                    return RuntimeMessage.create(
                        message_type="verify_result",
                        sender=ctx.actor_id,
                        recipient=msg.sender,
                        payload={"status": status},
                        correlation_id=msg.correlation_id,
                    )
                return h

            bus.register(ctx, make_handler(outcome))

        responses = bus.send_all(RuntimeMessage.create(
            message_type="verify_request",
            sender=planner_ctx.actor_id,
            recipient="verifier",
            payload={"hypothesis": "H1"},
        ))

        assert len(responses) == 3
        result_statuses = {r.payload["status"] for r in responses}
        assert result_statuses == {"confirmed", "rejected", "inconclusive"}
