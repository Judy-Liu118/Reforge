"""Governor bypass — REFORGE_GOVERNOR_BYPASS ablation toggle.

When `REFORGE_GOVERNOR_BYPASS=1`, retry_decision_node replaces the typed
ExecutionGovernor pipeline with a naive while-retry baseline:

  exit_code != 0 + budget left → RETRY (no failure_mode, no intent)
  exit_code == 0                → ACCEPT (SUCCESS)
  exit_code != 0 + budget out   → STOP (FAILED)

This baseline is the controlled comparison the README's "ablation, not a
product race" claim references. Tests pin the behavioural contract so the
ablation stays meaningful as the production governor evolves.
"""

from __future__ import annotations

import pytest

from reforge.runtime.orchestration.graph.nodes.retry_decision import (
    _is_bypass_enabled,
    _naive_resolution,
    retry_decision_node,
)
from reforge.runtime.domain.state.models import (
    ExecutionState,
    RuntimeControlState,
    RuntimeState,
)
from reforge.runtime.policy.task_intent import TaskIntent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _state(*, exit_code: int | None, retry_count: int = 0) -> RuntimeState:
    return RuntimeState(
        user_request="run code",
        exec_state=ExecutionState(exit_code=exit_code),
        control_state=RuntimeControlState(retry_count=retry_count),
    )


# ---------------------------------------------------------------------------
# Flag parsing
# ---------------------------------------------------------------------------


class TestBypassFlag:
    def test_default_is_off(self, monkeypatch) -> None:
        monkeypatch.delenv("REFORGE_GOVERNOR_BYPASS", raising=False)
        assert _is_bypass_enabled() is False

    @pytest.mark.parametrize("val", ["1", "true", "True", "TRUE", "yes", "YES", "on", "ON"])
    def test_enabled_values(self, monkeypatch, val: str) -> None:
        monkeypatch.setenv("REFORGE_GOVERNOR_BYPASS", val)
        assert _is_bypass_enabled() is True

    @pytest.mark.parametrize("val", ["0", "false", "False", "", "no", "off", "maybe"])
    def test_disabled_values(self, monkeypatch, val: str) -> None:
        monkeypatch.setenv("REFORGE_GOVERNOR_BYPASS", val)
        assert _is_bypass_enabled() is False


# ---------------------------------------------------------------------------
# Naive resolution semantics
# ---------------------------------------------------------------------------


class TestNaiveResolution:
    def test_success_accepts(self) -> None:
        r = _naive_resolution(_state(exit_code=0))
        assert r.action == "ACCEPT"
        assert r.outcome == "SUCCESS"
        # No typed classification — that's the whole point of the baseline.
        assert r.failure_mode == ""
        assert r.task_intent == ""

    def test_failure_with_budget_retries(self) -> None:
        r = _naive_resolution(_state(exit_code=1, retry_count=0))
        assert r.action == "RETRY"
        assert r.retryable is True
        assert r.failure_mode == ""

    def test_failure_out_of_budget_stops(self, monkeypatch) -> None:
        # config.max_retry is the source of truth; pin retry_count = max_retry
        from reforge.config import config

        r = _naive_resolution(
            _state(exit_code=1, retry_count=config.max_retry)
        )
        assert r.action == "STOP"
        assert r.outcome == "FAILED"

    def test_missing_exit_code_treated_as_accept(self) -> None:
        # Pre-execution / cleared state — naive baseline doesn't fail blank.
        r = _naive_resolution(_state(exit_code=None))
        assert r.action == "ACCEPT"


# ---------------------------------------------------------------------------
# Node-level routing — env flag picks which engine runs
# ---------------------------------------------------------------------------


class TestNodeRouting:
    def test_off_uses_governor(self, monkeypatch) -> None:
        # Sanity: with the flag off, the production pipeline runs and produces
        # a typed task_intent + classification result that the naive baseline
        # would never set.
        #
        # IntentStage's classify_intent() reaches out to the LLM; we patch it
        # at the call site so the test stays offline. The downstream
        # capability/classify/policy stages are deterministic and need no mock.
        monkeypatch.delenv("REFORGE_GOVERNOR_BYPASS", raising=False)
        monkeypatch.setattr(
            "reforge.runtime.orchestration.governor.intent_stage.classify_intent",
            lambda _request: TaskIntent.NORMAL_EXECUTION,
        )
        result = retry_decision_node(_state(exit_code=1))
        assert "classification_result" in result
        assert "retry_decision" in result

    def test_on_uses_naive(self, monkeypatch) -> None:
        monkeypatch.setenv("REFORGE_GOVERNOR_BYPASS", "1")
        result = retry_decision_node(_state(exit_code=1, retry_count=0))
        decision = result["retry_decision"]
        assert decision["action"] == "RETRY"
        # Naive reason — pins the contract that the bypass really bypassed.
        assert "naive" in decision["reason"]
        # Classification still present (graph contract) but empty failure_mode.
        assert result["classification_result"]["failure_mode"] == ""
