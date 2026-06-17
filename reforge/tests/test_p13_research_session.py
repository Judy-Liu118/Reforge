"""Tests for P13.4 — ResearchSession multi-round investigation loop."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from reforge.runtime.research.aggregator import EvidenceAggregator
from reforge.runtime.research.models import HypothesisRecord, ResearchPlan
from reforge.runtime.research.planner import ResearchPlanner
from reforge.runtime.research.session import ResearchSession
from reforge.runtime.domain.state.models import ExecutionOutput, RuntimeState


def _mock_planner(hypotheses_per_call: list[list[dict]]) -> ResearchPlanner:
    """Build a ResearchPlanner that returns preset hypotheses for each successive call."""
    call_iter = iter(hypotheses_per_call)

    def _plan(question: str, prior_findings=None, context: str = "") -> ResearchPlan:
        try:
            batch = next(call_iter)
        except StopIteration:
            return ResearchPlan(question=question, hypotheses=[])
        hyps = [
            HypothesisRecord(
                hypothesis=item["hypothesis"],
                rationale=item.get("rationale", ""),
                verification_request=item["verification_request"],
            )
            for item in batch
        ]
        return ResearchPlan(question=question, hypotheses=hyps)

    planner = MagicMock(spec=ResearchPlanner)
    planner.plan.side_effect = _plan
    return planner


def _mock_runner(results: list[tuple[str, int]]) -> MagicMock:
    """Build a RuntimeRunner mock returning preset (stdout, exit_code) pairs."""
    call_iter = iter(results)

    def _run(request: str) -> RuntimeState:
        stdout, exit_code = next(call_iter, ("output ok", 0))
        state = MagicMock(spec=RuntimeState)
        state.execution_output = ExecutionOutput(
            stdout=stdout, stderr="", exit_code=exit_code
        )
        return state

    runner = MagicMock()
    runner.run.side_effect = _run
    return runner


class TestResearchSessionSingleRound:
    def _build_session(self, hyp_batches, run_results) -> ResearchSession:
        return ResearchSession(
            planner=_mock_planner(hyp_batches),
            runner=_mock_runner(run_results),
            max_rounds=3,
        )

    def test_single_round_two_hypotheses(self) -> None:
        session = self._build_session(
            hyp_batches=[[
                {"hypothesis": "H1", "verification_request": "check h1"},
                {"hypothesis": "H2", "verification_request": "check h2"},
            ]],
            run_results=[("result A found", 0), ("result B found", 0)],
        )
        result = session.run("Why does the analysis fail?")

        assert result.total_rounds == 1
        assert len(result.final_hypotheses) == 2
        assert all(h.status == "confirmed" for h in result.final_hypotheses)

    def test_rejected_hypothesis_on_failed_execution(self) -> None:
        session = self._build_session(
            hyp_batches=[[
                {"hypothesis": "H1", "verification_request": "run bad code"},
            ]],
            run_results=[("error output", 1)],
        )
        result = session.run("Q")
        assert result.final_hypotheses[0].status == "rejected"

    def test_inconclusive_on_empty_output(self) -> None:
        session = self._build_session(
            hyp_batches=[[{"hypothesis": "H", "verification_request": "run"}]],
            run_results=[("", 0)],
        )
        result = session.run("Q")
        assert result.final_hypotheses[0].status == "inconclusive"


class TestResearchSessionMultiRound:
    def test_three_rounds_accumulate_hypotheses(self) -> None:
        # Round 2 returns inconclusive (empty output) → unresolved → forces round 3
        batches = [
            [{"hypothesis": f"Round{i} hypothesis", "verification_request": f"check {i}"}]
            for i in range(1, 4)
        ]
        run_results = [
            ("data found 1", 0),  # round 1: confirmed
            ("", 0),              # round 2: inconclusive → triggers round 3
            ("data found 3", 0),  # round 3: confirmed
        ]
        session = ResearchSession(
            planner=_mock_planner(batches),
            runner=_mock_runner(run_results),
            max_rounds=3,
        )
        result = session.run("Q")
        assert result.total_rounds == 3
        assert len(result.final_hypotheses) == 3

    def test_prior_findings_passed_to_planner(self) -> None:
        planner = _mock_planner([
            [{"hypothesis": "H1", "verification_request": "check"}],
            [{"hypothesis": "H2", "verification_request": "check again"}],
        ])
        session = ResearchSession(
            planner=planner,
            runner=_mock_runner([("confirmed data output found", 0), ("more data output found", 0)]),
            max_rounds=2,
        )
        session.run("Q")

        # Second call to plan() must have received prior_findings
        second_call_kwargs = planner.plan.call_args_list[1][1]
        prior = second_call_kwargs.get("prior_findings") or planner.plan.call_args_list[1][0][1]
        assert prior  # must be non-empty

    def test_stops_early_when_all_resolved_after_round2(self) -> None:
        # Round 1: confirmed; Round 2: confirmed → all resolved → stop before round 3
        batches = [
            [{"hypothesis": "H1", "verification_request": "check"}],
            [{"hypothesis": "H2", "verification_request": "check"}],
            [{"hypothesis": "H3 should not run", "verification_request": "check"}],
        ]
        session = ResearchSession(
            planner=_mock_planner(batches),
            runner=_mock_runner([("output A found here", 0), ("output B found here", 0)]),
            max_rounds=3,
        )
        result = session.run("Q")
        assert result.total_rounds == 2
        assert len(result.final_hypotheses) == 2

    def test_stops_when_planner_returns_empty(self) -> None:
        batches = [
            [{"hypothesis": "H1", "verification_request": "check"}],
            [],  # planner returns empty on round 2
        ]
        session = ResearchSession(
            planner=_mock_planner(batches),
            runner=_mock_runner([("result found here", 0)]),
            max_rounds=3,
        )
        result = session.run("Q")
        assert result.total_rounds == 1


class TestResearchSessionContradiction:
    def test_contradiction_detected_in_round(self) -> None:
        # Two hypotheses with same 3 words but one confirmed, one rejected
        batches = [[
            {
                "hypothesis": "the data csv file analysis shows high error rate",
                "verification_request": "check h1",
            },
            {
                "hypothesis": "the data csv file analysis shows high error rate",
                "verification_request": "check h2",
            },
        ]]
        session = ResearchSession(
            planner=_mock_planner(batches),
            runner=_mock_runner([("data ok result here", 0), ("", 1)]),
            max_rounds=1,
        )
        result = session.run("Q")
        assert len(result.contradictions_detected) >= 1

    def test_contradictions_empty_when_none(self) -> None:
        batches = [[{"hypothesis": "H1", "verification_request": "check"}]]
        session = ResearchSession(
            planner=_mock_planner(batches),
            runner=_mock_runner([("clean result", 0)]),
            max_rounds=1,
        )
        result = session.run("Q")
        assert result.contradictions_detected == []


class TestResearchSessionStream:
    def test_stream_yields_tuples(self) -> None:
        batches = [[
            {"hypothesis": "H1", "verification_request": "check h1"},
            {"hypothesis": "H2", "verification_request": "check h2"},
        ]]
        session = ResearchSession(
            planner=_mock_planner(batches),
            runner=_mock_runner([("output 1 result found", 0), ("output 2 result found", 0)]),
            max_rounds=1,
        )
        items = list(session.stream("Q"))
        assert len(items) == 2
        for round_num, original, updated in items:
            assert round_num == 1
            assert isinstance(original, HypothesisRecord)
            assert isinstance(updated, HypothesisRecord)
            assert updated.status in ("confirmed", "rejected", "inconclusive")

    def test_stream_stops_when_planner_empty(self) -> None:
        session = ResearchSession(
            planner=_mock_planner([[]]),
            runner=_mock_runner([]),
            max_rounds=3,
        )
        assert list(session.stream("Q")) == []


class TestResearchResultConclusion:
    def test_conclusion_includes_question(self) -> None:
        batches = [[{"hypothesis": "H1 test case", "verification_request": "check"}]]
        session = ResearchSession(
            planner=_mock_planner(batches),
            runner=_mock_runner([("result", 0)]),
            max_rounds=1,
        )
        result = session.run("Why does data processing fail?")
        assert "Why does data processing fail?" in result.conclusion

    def test_conclusion_lists_confirmed_hypotheses(self) -> None:
        batches = [[{"hypothesis": "Missing column", "verification_request": "check"}]]
        session = ResearchSession(
            planner=_mock_planner(batches),
            runner=_mock_runner([("column 'price' not found output", 0)]),
            max_rounds=1,
        )
        result = session.run("Q")
        assert "Missing column" in result.conclusion
