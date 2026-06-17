"""P17.5 — integration tests for parallel ResearchSession.

End-to-end coverage of the parallel verification path:
    planner → ranker → orchestrator (parallel verify) → synthesizer → result.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

from reforge.runtime.agents import RunnerVerifier
from reforge.runtime.research.models import (
    HypothesisRecord,
    ResearchPlan,
    ResearchResult,
)
from reforge.runtime.research.planner import ResearchPlanner
from reforge.runtime.research.session import ResearchSession
from reforge.runtime.domain.state.models import ExecutionOutput, RuntimeState


def _state(stdout: str, exit_code: int = 0) -> RuntimeState:
    s = MagicMock(spec=RuntimeState)
    s.execution_output = ExecutionOutput(
        stdout=stdout, stderr="", exit_code=exit_code
    )
    return s


def _mock_planner(batches: list[list[dict]]) -> MagicMock:
    call_iter = iter(batches)

    def _plan(question: str, prior_findings=None, context: str = "") -> ResearchPlan:
        try:
            batch = next(call_iter)
        except StopIteration:
            return ResearchPlan(question=question, hypotheses=[])
        hyps = [
            HypothesisRecord(
                hypothesis=item["hypothesis"],
                verification_request=item["vr"],
                rationale=item.get("rationale", ""),
            )
            for item in batch
        ]
        return ResearchPlan(question=question, hypotheses=hyps)

    planner = MagicMock(spec=ResearchPlanner)
    planner.plan.side_effect = _plan
    return planner


class TestParallelMode:
    def test_session_runs_three_hypotheses_in_parallel(self) -> None:
        """All three hypotheses of a round should verify concurrently."""
        barrier = threading.Barrier(3, timeout=3)
        completed: list[str] = []
        lock = threading.Lock()

        class BarrierVerifier:
            def verify(self, hypothesis: HypothesisRecord) -> HypothesisRecord:
                barrier.wait()  # forces actual concurrency
                time.sleep(0.02)
                with lock:
                    completed.append(hypothesis.hypothesis)
                return hypothesis.model_copy(
                    update={
                        "status": "confirmed",
                        "confidence": 0.8,
                        "evidence": [f"verified {hypothesis.hypothesis}"],
                    }
                )

        batches = [[
            {"hypothesis": "H1", "vr": "check 1"},
            {"hypothesis": "H2", "vr": "check 2"},
            {"hypothesis": "H3", "vr": "check 3"},
        ]]

        session = ResearchSession(
            planner=_mock_planner(batches),
            verifier=BarrierVerifier(),
            max_rounds=1,
            parallel_verification=True,
            max_workers=3,
        )

        start = time.monotonic()
        result = session.run("Q")
        elapsed = time.monotonic() - start

        assert elapsed < 0.5, f"parallel run took {elapsed:.2f}s — likely serial"
        assert len(result.final_hypotheses) == 3
        assert {h.hypothesis for h in result.final_hypotheses} == {"H1", "H2", "H3"}
        assert all(h.status == "confirmed" for h in result.final_hypotheses)

    def test_parallel_result_preserves_round_metadata(self) -> None:
        """`round_number` and `total_rounds` must be correct in parallel mode."""
        class TrivialVerifier:
            def verify(self, hypothesis: HypothesisRecord) -> HypothesisRecord:
                return hypothesis.model_copy(
                    update={
                        "status": "confirmed",
                        "confidence": 0.8,
                        "evidence": ["ok"],
                    }
                )

        batches = [
            [{"hypothesis": "H1", "vr": "v1"}, {"hypothesis": "H2", "vr": "v2"}],
            [{"hypothesis": "H3", "vr": "v3"}, {"hypothesis": "H4", "vr": "v4"}],
        ]
        session = ResearchSession(
            planner=_mock_planner(batches),
            verifier=TrivialVerifier(),
            max_rounds=2,
            parallel_verification=True,
            confirmed_exit_threshold=1.1,  # disable adaptive exit
        )
        result = session.run("Q")

        assert result.total_rounds == 2
        round_numbers = {h.round_number for h in result.final_hypotheses}
        assert round_numbers == {1, 2}

    def test_serial_and_parallel_produce_same_final_hypotheses(self) -> None:
        """Output should be identical regardless of execution mode."""
        class DeterministicVerifier:
            def verify(self, hypothesis: HypothesisRecord) -> HypothesisRecord:
                return hypothesis.model_copy(
                    update={
                        "status": "confirmed",
                        "confidence": 0.8,
                        "evidence": [f"ev-{hypothesis.hypothesis}"],
                    }
                )

        batches = [[
            {"hypothesis": "A", "vr": "va"},
            {"hypothesis": "B", "vr": "vb"},
            {"hypothesis": "C", "vr": "vc"},
        ]]

        serial = ResearchSession(
            planner=_mock_planner([list(b) for b in batches]),
            verifier=DeterministicVerifier(),
            max_rounds=1,
        ).run("Q")
        parallel = ResearchSession(
            planner=_mock_planner([list(b) for b in batches]),
            verifier=DeterministicVerifier(),
            max_rounds=1,
            parallel_verification=True,
        ).run("Q")

        serial_set = {(h.hypothesis, h.status) for h in serial.final_hypotheses}
        parallel_set = {(h.hypothesis, h.status) for h in parallel.final_hypotheses}
        assert serial_set == parallel_set


class TestWorkerIsolationInSession:
    def test_runner_factory_yields_independent_runners(self) -> None:
        """When parallel + runner_factory: each verification gets a fresh runner."""
        runner_ids: list[int] = []
        lock = threading.Lock()

        def runner_factory():
            r = MagicMock()
            r.run.return_value = _state("data found 42 lines", exit_code=0)
            r.session_id = f"sid-{len(runner_ids)}"
            with lock:
                runner_ids.append(id(r))
            return r

        verifier = RunnerVerifier(runner_factory=runner_factory)
        batches = [[
            {"hypothesis": "H1", "vr": "v1"},
            {"hypothesis": "H2", "vr": "v2"},
            {"hypothesis": "H3", "vr": "v3"},
        ]]

        session = ResearchSession(
            planner=_mock_planner(batches),
            verifier=verifier,
            max_rounds=1,
            parallel_verification=True,
            max_workers=3,
        )
        result = session.run("Q")

        assert len(result.final_hypotheses) == 3
        assert len(runner_ids) == 3
        assert len(set(runner_ids)) == 3, "each worker must get a distinct runner"


class TestResearchResultIntegrity:
    def test_synthesizer_produces_conclusion_and_contradictions(self) -> None:
        """Final ResearchResult.conclusion / contradictions come from SynthesizerAgent."""

        class MixedVerifier:
            """First call confirms, second rejects with overlapping text."""

            def __init__(self) -> None:
                self._count = 0
                self._lock = threading.Lock()

            def verify(self, hypothesis: HypothesisRecord) -> HypothesisRecord:
                with self._lock:
                    self._count += 1
                    n = self._count
                if n == 1:
                    return hypothesis.model_copy(
                        update={"status": "confirmed", "confidence": 0.9, "evidence": ["ok"]}
                    )
                return hypothesis.model_copy(
                    update={"status": "rejected", "confidence": 0.1, "evidence": ["bad"]}
                )

        text = "the data csv file analysis shows high error"
        batches = [[
            {"hypothesis": text, "vr": "v1"},
            {"hypothesis": text, "vr": "v2"},
        ]]
        session = ResearchSession(
            planner=_mock_planner(batches),
            verifier=MixedVerifier(),
            max_rounds=1,
            parallel_verification=True,
        )
        result: ResearchResult = session.run("Why X?")

        assert "Why X?" in result.conclusion
        assert len(result.contradictions_detected) >= 1


class TestParallelModeIsOptional:
    def test_default_is_serial(self) -> None:
        """parallel_verification=False (default) routes through the serial path."""
        class CountingVerifier:
            def __init__(self) -> None:
                self.calls = 0

            def verify(self, hypothesis: HypothesisRecord) -> HypothesisRecord:
                self.calls += 1
                return hypothesis.model_copy(
                    update={"status": "confirmed", "confidence": 0.8, "evidence": ["ok"]}
                )

        v = CountingVerifier()
        session = ResearchSession(
            planner=_mock_planner([[{"hypothesis": "H1", "vr": "v1"}]]),
            verifier=v,
            max_rounds=1,
        )
        session.run("Q")

        assert v.calls == 1
        assert session._orchestrator is None
