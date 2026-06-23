"""P18.1 — AgentRegistry: role/variant lookup + actor creation.

Test categories:
  1. Basic operations: register, get, has, get_or_none, registered_roles/variants
  2. Variant management: multi-variant per role, overwrite, isolation
  3. Error cases: missing role, missing variant, empty registry
  4. create_actor: P18.0→P18.1 bridge (ActorContext + agent)
  5. Integration: real agent types (RunnerVerifier, DefaultSynthesizer)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from reforge.runtime.agents.identity import ActorContext
from reforge.runtime.agents.registry import AgentRegistry, RegistryKeyError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakePlanner:
    def plan(self, question, prior_findings=None, context=""):
        return MagicMock()


class _FakeVerifier:
    def verify(self, hypothesis):
        return hypothesis


class _FakeSynthesizer:
    def synthesize(self, question, hypotheses):
        return MagicMock()


# ---------------------------------------------------------------------------
# Basic operations
# ---------------------------------------------------------------------------


class TestBasicOperations:
    def test_register_then_get_returns_same_instance(self) -> None:
        reg = AgentRegistry()
        agent = _FakeVerifier()
        reg.register("verifier", agent)
        assert reg.get("verifier") is agent

    def test_has_true_after_register(self) -> None:
        reg = AgentRegistry()
        reg.register("planner", _FakePlanner())
        assert reg.has("planner") is True

    def test_has_false_before_register(self) -> None:
        reg = AgentRegistry()
        assert reg.has("planner") is False

    def test_get_or_none_returns_agent_when_registered(self) -> None:
        reg = AgentRegistry()
        agent = _FakeVerifier()
        reg.register("verifier", agent)
        assert reg.get_or_none("verifier") is agent

    def test_get_or_none_returns_none_when_absent(self) -> None:
        reg = AgentRegistry()
        assert reg.get_or_none("verifier") is None

    def test_registered_roles_lists_all_unique_roles(self) -> None:
        reg = AgentRegistry()
        reg.register("planner", _FakePlanner())
        reg.register("verifier", _FakeVerifier())
        reg.register("synthesizer", _FakeSynthesizer())
        assert sorted(reg.registered_roles()) == ["planner", "synthesizer", "verifier"]

    def test_registered_roles_deduplicates_across_variants(self) -> None:
        reg = AgentRegistry()
        reg.register("verifier", _FakeVerifier(), variant="default")
        reg.register("verifier", _FakeVerifier(), variant="mock")
        assert reg.registered_roles() == ["verifier"]

    def test_registered_variants_for_role(self) -> None:
        reg = AgentRegistry()
        reg.register("verifier", _FakeVerifier(), variant="default")
        reg.register("verifier", _FakeVerifier(), variant="experimental")
        reg.register("verifier", _FakeVerifier(), variant="mock")
        assert reg.registered_variants("verifier") == [
            "default", "experimental", "mock"
        ]

    def test_registered_variants_empty_for_unknown_role(self) -> None:
        reg = AgentRegistry()
        assert reg.registered_variants("unknown") == []

    def test_len_counts_total_registrations(self) -> None:
        reg = AgentRegistry()
        reg.register("verifier", _FakeVerifier())
        reg.register("verifier", _FakeVerifier(), variant="mock")
        reg.register("planner", _FakePlanner())
        assert len(reg) == 3

    def test_empty_registry_len_is_zero(self) -> None:
        reg = AgentRegistry()
        assert len(reg) == 0


# ---------------------------------------------------------------------------
# Variant management
# ---------------------------------------------------------------------------


class TestVariantManagement:
    def test_default_variant_implicit(self) -> None:
        reg = AgentRegistry()
        agent = _FakeVerifier()
        reg.register("verifier", agent)
        assert reg.get("verifier", variant="default") is agent

    def test_multiple_variants_are_independent(self) -> None:
        reg = AgentRegistry()
        default_v = _FakeVerifier()
        mock_v = _FakeVerifier()
        reg.register("verifier", default_v, variant="default")
        reg.register("verifier", mock_v, variant="mock")
        assert reg.get("verifier") is default_v
        assert reg.get("verifier", variant="mock") is mock_v

    def test_experimental_variant(self) -> None:
        reg = AgentRegistry()
        exp = _FakeVerifier()
        reg.register("verifier", exp, variant="experimental")
        assert reg.get("verifier", variant="experimental") is exp

    def test_overwrite_replaces_previous_registration(self) -> None:
        reg = AgentRegistry()
        old = _FakeVerifier()
        new = _FakeVerifier()
        reg.register("verifier", old)
        reg.register("verifier", new)
        assert reg.get("verifier") is new

    def test_overwrite_does_not_affect_other_variants(self) -> None:
        reg = AgentRegistry()
        mock_v = _FakeVerifier()
        reg.register("verifier", _FakeVerifier(), variant="default")
        reg.register("verifier", mock_v, variant="mock")
        reg.register("verifier", _FakeVerifier(), variant="default")
        assert reg.get("verifier", variant="mock") is mock_v

    def test_custom_variant_string_accepted(self) -> None:
        reg = AgentRegistry()
        agent = _FakeVerifier()
        reg.register("verifier", agent, variant="canary-v2")
        assert reg.get("verifier", variant="canary-v2") is agent

    def test_has_respects_variant(self) -> None:
        reg = AgentRegistry()
        reg.register("verifier", _FakeVerifier(), variant="default")
        assert reg.has("verifier", variant="default") is True
        assert reg.has("verifier", variant="mock") is False


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestErrorCases:
    def test_get_missing_role_raises_registry_key_error(self) -> None:
        reg = AgentRegistry()
        with pytest.raises(RegistryKeyError):
            reg.get("verifier")

    def test_get_missing_variant_raises_registry_key_error(self) -> None:
        reg = AgentRegistry()
        reg.register("verifier", _FakeVerifier())
        with pytest.raises(RegistryKeyError):
            reg.get("verifier", variant="nonexistent")

    def test_error_message_includes_role_and_variant(self) -> None:
        reg = AgentRegistry()
        with pytest.raises(RegistryKeyError, match="role='missing'"):
            reg.get("missing")

    def test_error_message_on_empty_registry(self) -> None:
        reg = AgentRegistry()
        with pytest.raises(RegistryKeyError, match="empty"):
            reg.get("verifier")

    def test_error_message_shows_registered_keys(self) -> None:
        reg = AgentRegistry()
        reg.register("planner", _FakePlanner())
        with pytest.raises(RegistryKeyError, match="planner"):
            reg.get("verifier")

    def test_registry_key_error_is_subclass_of_key_error(self) -> None:
        with pytest.raises(KeyError):
            raise RegistryKeyError("test")

    def test_create_actor_raises_for_unregistered_role(self) -> None:
        reg = AgentRegistry()
        with pytest.raises(RegistryKeyError):
            reg.create_actor("verifier", session_scope="sess-1")


# ---------------------------------------------------------------------------
# create_actor (P18.0 bridge)
# ---------------------------------------------------------------------------


class TestCreateActor:
    def test_returns_actor_context_and_agent(self) -> None:
        reg = AgentRegistry()
        agent = _FakeVerifier()
        reg.register("verifier", agent)

        ctx, returned_agent = reg.create_actor("verifier", session_scope="sess-1")

        assert isinstance(ctx, ActorContext)
        assert returned_agent is agent

    def test_actor_context_has_correct_role(self) -> None:
        reg = AgentRegistry()
        reg.register("planner", _FakePlanner())

        ctx, _ = reg.create_actor("planner", session_scope="sess-1")
        assert ctx.actor_role == "planner"

    def test_actor_context_has_correct_session_scope(self) -> None:
        reg = AgentRegistry()
        reg.register("verifier", _FakeVerifier())

        ctx, _ = reg.create_actor("verifier", session_scope="research-session-42")
        assert ctx.session_scope == "research-session-42"

    def test_actor_context_actor_id_is_unique_per_call(self) -> None:
        reg = AgentRegistry()
        reg.register("verifier", _FakeVerifier())

        ctx_a, _ = reg.create_actor("verifier", session_scope="s")
        ctx_b, _ = reg.create_actor("verifier", session_scope="s")
        assert ctx_a.actor_id != ctx_b.actor_id

    def test_create_actor_uses_correct_variant(self) -> None:
        reg = AgentRegistry()
        default_v = _FakeVerifier()
        mock_v = _FakeVerifier()
        reg.register("verifier", default_v, variant="default")
        reg.register("verifier", mock_v, variant="mock")

        _, agent = reg.create_actor("verifier", session_scope="s", variant="mock")
        assert agent is mock_v

    def test_multiple_create_actor_calls_yield_independent_contexts(self) -> None:
        reg = AgentRegistry()
        reg.register("verifier", _FakeVerifier())

        contexts = [
            reg.create_actor("verifier", session_scope=f"sess-{i}")[0]
            for i in range(10)
        ]
        ids = {ctx.actor_id for ctx in contexts}
        assert len(ids) == 10, "each create_actor call must yield a unique actor_id"


# ---------------------------------------------------------------------------
# Integration with real agent types
# ---------------------------------------------------------------------------


class TestIntegrationWithRealAgents:
    def test_register_runner_verifier(self) -> None:
        from reforge.runtime.agents.verifier import RunnerVerifier

        reg = AgentRegistry()
        verifier = RunnerVerifier(runner=MagicMock())
        reg.register("verifier", verifier)

        assert reg.get("verifier") is verifier

    def test_register_default_synthesizer(self) -> None:
        from reforge.runtime.agents.synthesizer import DefaultSynthesizer

        reg = AgentRegistry()
        synth = DefaultSynthesizer()
        reg.register("synthesizer", synth)

        assert reg.get("synthesizer") is synth

    def test_create_actor_with_runner_verifier_returns_context(self) -> None:
        from reforge.runtime.agents.verifier import RunnerVerifier

        reg = AgentRegistry()
        reg.register("verifier", RunnerVerifier(runner=MagicMock()))

        ctx, agent = reg.create_actor("verifier", session_scope="integration-test")
        assert ctx.actor_role == "verifier"
        assert ctx.session_scope == "integration-test"
        assert isinstance(agent, RunnerVerifier)

    def test_registry_repr_shows_registered_entries(self) -> None:
        reg = AgentRegistry()
        reg.register("planner", _FakePlanner())
        reg.register("verifier", _FakeVerifier())
        r = repr(reg)
        assert "planner" in r
        assert "verifier" in r
