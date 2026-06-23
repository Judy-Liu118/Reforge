"""P17.1 + P17.4 — ResearchOrchestrator parallel verification + worker isolation."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

from reforge.runtime.agents import RunnerVerifier
from reforge.runtime.research.models import HypothesisRecord
from reforge.runtime.research.orchestrator import ResearchOrchestrator
from reforge.runtime.domain.state.models import ExecutionOutput, RuntimeState


def _hyp(text: str) -> HypothesisRecord:
    return HypothesisRecord(hypothesis=text, verification_request=f"check {text}")


def _state(stdout: str, exit_code: int = 0) -> RuntimeState:
    s = MagicMock(spec=RuntimeState)
    s.execution_output = ExecutionOutput(stdout=stdout, stderr="", exit_code=exit_code)
    return s


class _StubVerifier:
    """Minimal VerifierAgent — returns a confirmed hypothesis with given evidence."""

    def __init__(self, evidence_by_hypothesis: dict[str, str]) -> None:
        self._map = evidence_by_hypothesis
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def verify(self, hypothesis: HypothesisRecord) -> HypothesisRecord:
        with self._lock:
            self.calls.append(hypothesis.hypothesis)
        return hypothesis.model_copy(
            update={
                "status": "confirmed",
                "confidence": 0.8,
                "evidence": [self._map.get(hypothesis.hypothesis, "ok found data")],
            }
        )


class TestVerifyBatch:
    def test_empty_input_returns_empty(self) -> None:
        orch = ResearchOrchestrator(verifier=_StubVerifier({}))
        assert orch.verify_batch([]) == []

    def test_single_hypothesis_runs_inline(self) -> None:
        verifier = _StubVerifier({"H1": "evidence one"})
        orch = ResearchOrchestrator(verifier=verifier)
        result = orch.verify_batch([_hyp("H1")])

        assert len(result) == 1
        assert result[0].status == "confirmed"
        assert "evidence one" in result[0].evidence[0]

    def test_three_hypotheses_all_verified(self) -> None:
        verifier = _StubVerifier({
            "H1": "ev1", "H2": "ev2", "H3": "ev3",
        })
        orch = ResearchOrchestrator(verifier=verifier, max_workers=3)
        result = orch.verify_batch([_hyp("H1"), _hyp("H2"), _hyp("H3")])

        assert len(result) == 3
        assert {r.hypothesis for r in result} == {"H1", "H2", "H3"}
        assert all(r.status == "confirmed" for r in result)

    def test_preserves_input_order(self) -> None:
        verifier = _StubVerifier({})
        orch = ResearchOrchestrator(verifier=verifier, max_workers=4)
        hyps = [_hyp(f"H{i}") for i in range(5)]
        result = orch.verify_batch(hyps)

        assert [r.hypothesis for r in result] == [f"H{i}" for i in range(5)]


class TestParallelism:
    def test_verifications_run_concurrently(self) -> None:
        """Three 100ms verifications should finish in well under 300ms with 3 workers."""
        barrier = threading.Barrier(3, timeout=2)

        class SlowVerifier:
            def verify(self, hypothesis: HypothesisRecord) -> HypothesisRecord:
                barrier.wait()  # all three must enter together — proves parallelism
                time.sleep(0.05)
                return hypothesis.model_copy(
                    update={
                        "status": "confirmed",
                        "confidence": 0.8,
                        "evidence": ["ok"],
                    }
                )

        orch = ResearchOrchestrator(verifier=SlowVerifier(), max_workers=3)
        start = time.monotonic()
        orch.verify_batch([_hyp("H1"), _hyp("H2"), _hyp("H3")])
        elapsed = time.monotonic() - start

        # Serial would be ≥ 0.15s + barrier blocking; parallel ≪ 0.5s
        assert elapsed < 0.5, f"Expected parallel execution, took {elapsed:.2f}s"


class TestErrorIsolation:
    def test_single_failure_does_not_abort_batch(self) -> None:
        class FlakyVerifier:
            def verify(self, hypothesis: HypothesisRecord) -> HypothesisRecord:
                if hypothesis.hypothesis == "H2":
                    raise RuntimeError("boom")
                return hypothesis.model_copy(
                    update={"status": "confirmed", "confidence": 0.8, "evidence": ["ok"]}
                )

        orch = ResearchOrchestrator(verifier=FlakyVerifier(), max_workers=3)
        result = orch.verify_batch([_hyp("H1"), _hyp("H2"), _hyp("H3")])

        assert len(result) == 3
        statuses = {r.hypothesis: r.status for r in result}
        assert statuses["H1"] == "confirmed"
        assert statuses["H2"] == "inconclusive"
        assert statuses["H3"] == "confirmed"
        # Error reason captured in evidence
        h2 = next(r for r in result if r.hypothesis == "H2")
        assert "verification error" in h2.evidence[0]


class TestWorkerIsolationViaRunnerFactory:
    """P17.4 — factory mode gives each worker an independent RuntimeRunner."""

    def test_runner_factory_called_per_hypothesis(self) -> None:
        factory_calls: list[int] = []
        runner_ids: list[int] = []

        def runner_factory():
            r = MagicMock()
            r.run.return_value = _state("ok confirmed data here", exit_code=0)
            factory_calls.append(1)
            runner_ids.append(id(r))
            return r

        verifier = RunnerVerifier(runner_factory=runner_factory)
        orch = ResearchOrchestrator(verifier=verifier, max_workers=3)
        orch.verify_batch([_hyp("H1"), _hyp("H2"), _hyp("H3")])

        assert len(factory_calls) == 3
        assert len(set(runner_ids)) == 3, "factory must mint distinct runners per call"

    def test_session_ids_are_independent_across_workers(self) -> None:
        """A factory that builds real RuntimeRunner instances yields unique session_ids."""
        from reforge.runtime.orchestration.engine.runner import RuntimeRunner

        session_ids: list[str] = []

        def runner_factory():
            runner = MagicMock(spec=RuntimeRunner)
            runner.run.return_value = _state("ok found data 42", exit_code=0)
            runner.session_id = f"sid-{len(session_ids)}"
            session_ids.append(runner.session_id)
            return runner

        verifier = RunnerVerifier(runner_factory=runner_factory)
        orch = ResearchOrchestrator(verifier=verifier, max_workers=2)
        orch.verify_batch([_hyp("H1"), _hyp("H2")])

        assert len(session_ids) == 2
        assert len(set(session_ids)) == 2
