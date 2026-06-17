"""P18.0 — ActorContext identity + RuntimeMessage contract.

Test categories:
  1. Actor identity: actor_id uniqueness, role/scope binding, immutability
  2. Message contract: required fields, auto-fill, correlation_id traceability
  3. Scoped isolation: different session_scopes produce independent actors;
     no shared mutable state leaks between message instances
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from reforge.runtime.agents.identity import ActorContext
from reforge.runtime.agents.message import RuntimeMessage


# ---------------------------------------------------------------------------
# ActorContext
# ---------------------------------------------------------------------------


class TestActorContext:
    def test_create_sets_role_and_scope(self) -> None:
        ctx = ActorContext.create(actor_role="verifier", session_scope="sess-1")
        assert ctx.actor_role == "verifier"
        assert ctx.session_scope == "sess-1"

    def test_create_generates_non_empty_actor_id(self) -> None:
        ctx = ActorContext.create(actor_role="planner", session_scope="sess-1")
        assert ctx.actor_id
        assert len(ctx.actor_id) > 0

    def test_create_generates_valid_uuid(self) -> None:
        ctx = ActorContext.create(actor_role="planner", session_scope="s")
        # Must not raise
        parsed = uuid.UUID(ctx.actor_id)
        assert str(parsed) == ctx.actor_id

    def test_create_generates_unique_ids(self) -> None:
        a = ActorContext.create(actor_role="verifier", session_scope="s")
        b = ActorContext.create(actor_role="verifier", session_scope="s")
        assert a.actor_id != b.actor_id

    def test_create_many_ids_are_all_unique(self) -> None:
        ids = {ActorContext.create("verifier", "s").actor_id for _ in range(50)}
        assert len(ids) == 50

    def test_frozen_prevents_mutation(self) -> None:
        ctx = ActorContext.create(actor_role="planner", session_scope="s")
        with pytest.raises((AttributeError, TypeError)):
            ctx.actor_role = "synthesizer"  # type: ignore[misc]

    def test_direct_construction_respects_provided_id(self) -> None:
        fixed_id = str(uuid.uuid4())
        ctx = ActorContext(actor_id=fixed_id, actor_role="synthesizer", session_scope="s")
        assert ctx.actor_id == fixed_id

    def test_equality_based_on_field_values(self) -> None:
        fixed = str(uuid.uuid4())
        a = ActorContext(actor_id=fixed, actor_role="planner", session_scope="s")
        b = ActorContext(actor_id=fixed, actor_role="planner", session_scope="s")
        assert a == b

    def test_different_session_scopes_are_independent(self) -> None:
        a = ActorContext.create(actor_role="verifier", session_scope="session-A")
        b = ActorContext.create(actor_role="verifier", session_scope="session-B")
        assert a.session_scope != b.session_scope
        assert a.actor_id != b.actor_id

    def test_role_is_open_ended_string(self) -> None:
        ctx = ActorContext.create(actor_role="custom-evaluator", session_scope="s")
        assert ctx.actor_role == "custom-evaluator"


# ---------------------------------------------------------------------------
# RuntimeMessage
# ---------------------------------------------------------------------------


class TestRuntimeMessage:
    def test_create_sets_required_fields(self) -> None:
        msg = RuntimeMessage.create(
            message_type="verify_request",
            sender="actor-1",
            recipient="actor-2",
            payload={"hypothesis": "H1"},
        )
        assert msg.message_type == "verify_request"
        assert msg.sender == "actor-1"
        assert msg.recipient == "actor-2"
        assert msg.payload == {"hypothesis": "H1"}

    def test_create_auto_fills_correlation_id(self) -> None:
        msg = RuntimeMessage.create(
            message_type="plan_result", sender="a", recipient="b"
        )
        assert msg.correlation_id
        uuid.UUID(msg.correlation_id)  # Must be valid UUID

    def test_create_auto_fills_timestamp(self) -> None:
        before = datetime.now(timezone.utc)
        msg = RuntimeMessage.create(
            message_type="plan_result", sender="a", recipient="b"
        )
        after = datetime.now(timezone.utc)
        assert before <= msg.timestamp <= after

    def test_correlation_ids_are_unique_across_messages(self) -> None:
        msgs = [
            RuntimeMessage.create(message_type="t", sender="a", recipient="b")
            for _ in range(50)
        ]
        ids = {m.correlation_id for m in msgs}
        assert len(ids) == 50

    def test_explicit_correlation_id_is_preserved(self) -> None:
        cid = str(uuid.uuid4())
        msg = RuntimeMessage.create(
            message_type="t", sender="a", recipient="b", correlation_id=cid
        )
        assert msg.correlation_id == cid

    def test_payload_defaults_to_empty_dict(self) -> None:
        msg = RuntimeMessage.create(message_type="ping", sender="a", recipient="b")
        assert msg.payload == {}

    def test_direct_construction_accepts_all_fields(self) -> None:
        now = datetime.now(timezone.utc)
        cid = str(uuid.uuid4())
        msg = RuntimeMessage(
            message_type="synthesis_result",
            sender="s1",
            recipient="s2",
            payload={"conclusion": "ok"},
            correlation_id=cid,
            timestamp=now,
        )
        assert msg.correlation_id == cid
        assert msg.timestamp == now

    def test_frozen_prevents_field_reassignment(self) -> None:
        msg = RuntimeMessage.create(message_type="t", sender="a", recipient="b")
        with pytest.raises((TypeError, ValueError)):
            msg.message_type = "mutated"  # type: ignore[misc]

    def test_correlation_id_links_request_and_response(self) -> None:
        """Simulate request/response correlation via shared correlation_id."""
        cid = str(uuid.uuid4())
        request = RuntimeMessage.create(
            message_type="verify_request",
            sender="planner-1",
            recipient="verifier-1",
            payload={"hypothesis": "H1"},
            correlation_id=cid,
        )
        response = RuntimeMessage.create(
            message_type="verify_result",
            sender="verifier-1",
            recipient="planner-1",
            payload={"status": "confirmed"},
            correlation_id=cid,
        )
        assert request.correlation_id == response.correlation_id

    def test_recipient_can_be_role_string_for_broadcast(self) -> None:
        msg = RuntimeMessage.create(
            message_type="verify_request",
            sender="planner-1",
            recipient="verifier",  # role-based broadcast
            payload={"hypothesis": "H1"},
        )
        assert msg.recipient == "verifier"


# ---------------------------------------------------------------------------
# Scoped isolation
# ---------------------------------------------------------------------------


class TestScopedIsolation:
    def test_actors_in_different_scopes_have_no_shared_ids(self) -> None:
        scope_a_actors = [
            ActorContext.create("verifier", "scope-A") for _ in range(5)
        ]
        scope_b_actors = [
            ActorContext.create("verifier", "scope-B") for _ in range(5)
        ]
        ids_a = {a.actor_id for a in scope_a_actors}
        ids_b = {a.actor_id for a in scope_b_actors}
        assert ids_a.isdisjoint(ids_b)

    def test_message_payloads_are_independent_between_instances(self) -> None:
        payload_a = {"key": "value-a"}
        payload_b = {"key": "value-b"}
        msg_a = RuntimeMessage.create(message_type="t", sender="a", recipient="b", payload=payload_a)
        msg_b = RuntimeMessage.create(message_type="t", sender="a", recipient="b", payload=payload_b)
        assert msg_a.payload["key"] == "value-a"
        assert msg_b.payload["key"] == "value-b"

    def test_actor_context_used_as_message_sender_recipient(self) -> None:
        """ActorContext.actor_id flows correctly into RuntimeMessage fields."""
        planner = ActorContext.create("planner", "sess-1")
        verifier = ActorContext.create("verifier", "sess-1")

        msg = RuntimeMessage.create(
            message_type="plan_result",
            sender=planner.actor_id,
            recipient=verifier.actor_id,
            payload={"hypotheses": ["H1", "H2"]},
        )
        assert msg.sender == planner.actor_id
        assert msg.recipient == verifier.actor_id
        assert msg.sender != msg.recipient

    def test_two_sessions_produce_independent_actor_ids(self) -> None:
        """Same role in two different sessions must have different actor_ids."""
        v1 = ActorContext.create("verifier", "session-X")
        v2 = ActorContext.create("verifier", "session-Y")
        assert v1.actor_id != v2.actor_id
        assert v1.session_scope != v2.session_scope
