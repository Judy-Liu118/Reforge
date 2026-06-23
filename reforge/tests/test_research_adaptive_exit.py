"""Tests for P15.4 — adaptive exit and ranker integration in ResearchSession."""

from __future__ import annotations

from unittest.mock import MagicMock

from reforge.runtime.research.models import HypothesisRecord, ResearchPlan
from reforge.runtime.research.session import ResearchSession, _should_exit
from reforge.runtime.domain.state.models import ExecutionOutput, RuntimeState


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

    planner = MagicMock()
    planner.plan.side_effect = _plan
    return planner


def _mock_runner(results: list[tuple[str, int]]) -> MagicMock:
    call_iter = iter(results)

    def _run(request: str) -> RuntimeState:
        stdout, exit_code = next(call_iter, ("output ok here", 0))
        state = MagicMock(spec=RuntimeState)
        state.execution_output = ExecutionOutput(
            stdout=stdout, stderr="", exit_code=exit_code
        )
        return state

    runner = MagicMock()
    runner.run.side_effect = _run
    return runner


class TestShouldExit:
    def test_exit_when_no_unresolved(self) -> None:
        hyps = [
            HypothesisRecord(hypothesis="H1", status="confirmed"),  # type: ignore[arg-type]
            HypothesisRecord(hypothesis="H2", status="rejected"),   # type: ignore[arg-type]
        ]
        assert _should_exit(hyps, 0.7) is True

    def test_no_exit_when_unresolved_and_below_threshold(self) -> None:
        hyps = [
            HypothesisRecord(hypothesis="H1", status="confirmed"),    # type: ignore[arg-type]
            HypothesisRecord(hypothesis="H2", status="inconclusive"), # type: ignore[arg-type]
            HypothesisRecord(hypothesis="H3", status="inconclusive"), # type: ignore[arg-type]
            HypothesisRecord(hypothesis="H4", status="inconclusive"), # type: ignore[arg-type]
        ]
        # 1/4 = 0.25 < 0.7
        assert _should_exit(hyps, 0.7) is False

    def test_exit_when_confirmed_ratio_meets_threshold(self) -> None:
        hyps = [
            HypothesisRecord(hypothesis="H1", status="confirmed"),    # type: ignore[arg-type]
            HypothesisRecord(hypothesis="H2", status="confirmed"),    # type: ignore[arg-type]
            HypothesisRecord(hypothesis="H3", status="confirmed"),    # type: ignore[arg-type]
            HypothesisRecord(hypothesis="H4", status="inconclusive"), # type: ignore[arg-type]
        ]
        # 3/4 = 0.75 >= 0.7
        assert _should_exit(hyps, 0.7) is True

    def test_no_exit_on_empty_list(self) -> None:
        assert _should_exit([], 0.7) is False

    def test_threshold_exactly_met(self) -> None:
        hyps = [
            HypothesisRecord(hypothesis=f"H{i}", status="confirmed")  # type: ignore[arg-type]
            for i in range(7)
        ] + [
            HypothesisRecord(hypothesis="H8", status="inconclusive"),  # type: ignore[arg-type]
            HypothesisRecord(hypothesis="H9", status="inconclusive"),  # type: ignore[arg-type]
            HypothesisRecord(hypothesis="H10", status="inconclusive"), # type: ignore[arg-type]
        ]
        # 7/10 = 0.7 exactly meets threshold
        assert _should_exit(hyps, 0.7) is True


class TestAdaptiveExitInSession:
    def test_exits_early_when_confirmed_ratio_exceeds_threshold(self) -> None:
        # Round 1: 2 confirmed → ratio 2/2 = 1.0 ≥ 0.7, but exit only triggers after round 2
        # Round 2: more confirmed → ratio ≥ 0.7 → exit
        batches = [
            [
                {"hypothesis": "H1", "vr": "check h1"},
                {"hypothesis": "H2", "vr": "check h2"},
            ],
            [
                {"hypothesis": "H3", "vr": "check h3"},
                {"hypothesis": "H4", "vr": "check h4"},
            ],
            [
                {"hypothesis": "H5 should not run", "vr": "check h5"},
            ],
        ]
        # Round 1: 2 confirmed; Round 2: first confirmed (3/4 ≥ 0.7), but we run all in round 2 first
        run_results = [
            ("output confirmed data here", 0),  # round 1 h1: confirmed
            ("output confirmed data here", 0),  # round 1 h2: confirmed
            ("output confirmed data here", 0),  # round 2 h3: confirmed
            ("", 0),                             # round 2 h4: inconclusive (3/4=0.75 ≥ 0.7)
        ]
        session = ResearchSession(
            planner=_mock_planner(batches),
            runner=_mock_runner(run_results),
            max_rounds=3,
            confirmed_exit_threshold=0.7,
        )
        result = session.run("Q")
        assert result.total_rounds == 2
        assert len(result.final_hypotheses) == 4

    def test_custom_threshold_zero_exits_after_any_confirmed(self) -> None:
        # threshold=0.0 means any confirmed → exit after round 2
        batches = [
            [{"hypothesis": "H1", "vr": "check"}],
            [{"hypothesis": "H2", "vr": "check"}],
            [{"hypothesis": "H3 not run", "vr": "check"}],
        ]
        run_results = [("output result here", 0), ("output result here", 0)]
        session = ResearchSession(
            planner=_mock_planner(batches),
            runner=_mock_runner(run_results),
            max_rounds=3,
            confirmed_exit_threshold=0.0,
        )
        result = session.run("Q")
        assert result.total_rounds == 2

    def test_threshold_one_runs_all_rounds_unless_all_resolved(self) -> None:
        # threshold=1.0 means must ALL be confirmed to exit (except if all resolved)
        batches = [
            [{"hypothesis": "H1", "vr": "check"}],
            [{"hypothesis": "H2", "vr": "check"}],
        ]
        run_results = [
            ("output here", 0),  # H1: confirmed
            ("", 0),             # H2: inconclusive → 0.5 < 1.0 → no exit
        ]
        session = ResearchSession(
            planner=_mock_planner(batches),
            runner=_mock_runner(run_results),
            max_rounds=2,
            confirmed_exit_threshold=1.0,
        )
        result = session.run("Q")
        assert result.total_rounds == 2


class TestRankerInSession:
    def test_session_uses_ranker_to_order_hypotheses(self) -> None:
        """Hypotheses with rationale should be ranked higher (rationale bonus)."""
        from reforge.runtime.research.ranker import HypothesisRanker

        ranked_order: list[str] = []

        class RecordingRanker(HypothesisRanker):
            def rank(self, candidates, prior_confirmed=None):
                result = super().rank(candidates, prior_confirmed)
                ranked_order.extend(h.hypothesis for h in result)
                return result

        batches = [[
            {"hypothesis": "H1 no rationale", "vr": "check h1"},
            {"hypothesis": "H2 with rationale", "vr": "check h2", "rationale": "Very relevant"},
        ]]
        session = ResearchSession(
            planner=_mock_planner(batches),
            runner=_mock_runner([("output result", 0), ("output result", 0)]),
            ranker=RecordingRanker(),
            max_rounds=1,
        )
        session.run("Q")
        assert len(ranked_order) == 2
        # H2 should rank first because of rationale bonus
        assert ranked_order[0] == "H2 with rationale"

    def test_stream_also_uses_ranker(self) -> None:
        from reforge.runtime.research.ranker import HypothesisRanker

        calls: list[int] = []

        class CountingRanker(HypothesisRanker):
            def rank(self, candidates, prior_confirmed=None):
                calls.append(len(candidates))
                return super().rank(candidates, prior_confirmed)

        batches = [[{"hypothesis": "H1", "vr": "check"}, {"hypothesis": "H2", "vr": "check"}]]
        session = ResearchSession(
            planner=_mock_planner(batches),
            runner=_mock_runner([("output result", 0), ("output result", 0)]),
            ranker=CountingRanker(),
            max_rounds=1,
        )
        list(session.stream("Q"))
        assert len(calls) == 1  # rank called once per round
        assert calls[0] == 2   # 2 candidates ranked
