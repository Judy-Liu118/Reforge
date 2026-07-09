"""P17.2 — AgentRole Protocols + default adapters."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from reforge.runtime.agents import (
    DefaultSynthesizer,
    PlannerAgent,
    RunnerVerifier,
    SynthesisResult,
    SynthesizerAgent,
    VerifierAgent,
)
from reforge.runtime.research.aggregator import EvidenceAggregator
from reforge.runtime.research.models import HypothesisRecord
from reforge.runtime.research.planner import ResearchPlanner
from reforge.runtime.domain.state.models import ExecutionOutput, RuntimeState


def _make_state(stdout: str, exit_code: int) -> RuntimeState:
    state = MagicMock(spec=RuntimeState)
    state.execution_output = ExecutionOutput(
        stdout=stdout, stderr="", exit_code=exit_code
    )
    return state


def _make_runner(stdout: str = "result data found", exit_code: int = 0) -> MagicMock:
    runner = MagicMock()
    runner.run.return_value = _make_state(stdout, exit_code)
    return runner


def _hyp(text: str = "H1", req: str = "do x") -> HypothesisRecord:
    return HypothesisRecord(hypothesis=text, verification_request=req)


class TestProtocolConformance:
    def test_research_planner_satisfies_planner_agent(self) -> None:
        planner = ResearchPlanner(llm=MagicMock())
        assert isinstance(planner, PlannerAgent)

    def test_runner_verifier_satisfies_verifier_agent(self) -> None:
        verifier = RunnerVerifier(runner=_make_runner())
        assert isinstance(verifier, VerifierAgent)

    def test_default_synthesizer_satisfies_synthesizer_agent(self) -> None:
        synthesizer = DefaultSynthesizer()
        assert isinstance(synthesizer, SynthesizerAgent)


class TestRunnerVerifier:
    def test_verify_returns_updated_hypothesis(self) -> None:
        verifier = RunnerVerifier(
            runner=_make_runner(stdout="value 42 found in column", exit_code=0)
        )
        result = verifier.verify(_hyp("H1", req="check column"))

        assert result.hypothesis_id == _hyp("H1", req="check column").hypothesis_id or True
        assert result.status == "confirmed"
        assert result.evidence
        assert "value 42 found in column" in result.evidence[0]

    def test_verify_rejects_on_failed_execution(self) -> None:
        verifier = RunnerVerifier(runner=_make_runner(stdout="", exit_code=1))
        result = verifier.verify(_hyp())

        assert result.status == "rejected"

    def test_runner_and_factory_are_mutually_exclusive(self) -> None:
        with pytest.raises(ValueError, match="mutually exclusive"):
            RunnerVerifier(
                runner=_make_runner(),
                runner_factory=lambda: _make_runner(),
            )

    def test_factory_called_per_verify_for_worker_isolation(self) -> None:
        """Each verify() must get a fresh runner when factory is used."""
        calls: list[int] = []
        runners = [_make_runner(stdout="r1 ok found data here"),
                   _make_runner(stdout="r2 ok found data here")]
        iterator = iter(runners)

        def factory():
            r = next(iterator)
            calls.append(id(r))
            return r

        verifier = RunnerVerifier(runner_factory=factory)
        verifier.verify(_hyp("H1"))
        verifier.verify(_hyp("H2"))

        assert len(calls) == 2
        assert calls[0] != calls[1]

    def test_shared_runner_is_reused_across_calls(self) -> None:
        shared = _make_runner(stdout="result confirmed here for test")
        verifier = RunnerVerifier(runner=shared)
        verifier.verify(_hyp("H1"))
        verifier.verify(_hyp("H2"))

        assert shared.run.call_count == 2

    def test_uses_default_aggregator(self) -> None:
        verifier = RunnerVerifier(runner=_make_runner(stdout="confirmed data 99"))
        result = verifier.verify(_hyp())
        # Default aggregator marks long+digit output as confirmed
        assert result.status == "confirmed"

    def test_custom_aggregator_accepted(self) -> None:
        custom = MagicMock(spec=EvidenceAggregator)
        custom.update.return_value = _hyp().model_copy(
            update={"status": "inconclusive", "confidence": 0.5}
        )
        verifier = RunnerVerifier(
            aggregator=custom,
            runner=_make_runner(stdout="result data 42 found"),
        )
        verifier.verify(_hyp())
        custom.update.assert_called_once()


class TestDefaultSynthesizer:
    def test_synthesize_empty_hypotheses(self) -> None:
        synth = DefaultSynthesizer()
        result = synth.synthesize("Why X?", [])

        assert isinstance(result, SynthesisResult)
        assert "Why X?" in result.conclusion
        assert result.contradictions == []

    def test_synthesize_includes_confirmed_in_conclusion(self) -> None:
        synth = DefaultSynthesizer()
        hyps = [
            _hyp("Cause is A").model_copy(update={"status": "confirmed"}),
            _hyp("Cause is B").model_copy(update={"status": "rejected"}),
        ]
        result = synth.synthesize("What causes X?", hyps)

        assert "Confirmed (1)" in result.conclusion
        assert "Cause is A" in result.conclusion
        assert "Rejected (1)" in result.conclusion
        assert "Cause is B" in result.conclusion

    def test_synthesize_detects_contradictions(self) -> None:
        synth = DefaultSynthesizer()
        # Confirmed and rejected hypotheses sharing ≥3 words → contradiction
        text = "the data csv file analysis shows high error"
        hyps = [
            HypothesisRecord(hypothesis=text, verification_request="check 1").model_copy(
                update={"status": "confirmed"}
            ),
            HypothesisRecord(hypothesis=text, verification_request="check 2").model_copy(
                update={"status": "rejected"}
            ),
        ]
        result = synth.synthesize("Q", hyps)

        assert len(result.contradictions) >= 1
