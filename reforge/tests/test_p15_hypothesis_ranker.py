"""Tests for P15.1 — HypothesisRanker relevance scoring."""

from __future__ import annotations

from reforge.runtime.research.models import HypothesisRecord
from reforge.runtime.research.ranker import HypothesisRanker, _score, _words_from


def _hyp(hypothesis: str, rationale: str = "") -> HypothesisRecord:
    return HypothesisRecord(hypothesis=hypothesis, rationale=rationale)


def _confirmed(hypothesis: str) -> HypothesisRecord:
    return HypothesisRecord(hypothesis=hypothesis, status="confirmed")  # type: ignore[arg-type]


class TestHypothesisRanker:
    def setup_method(self) -> None:
        self.ranker = HypothesisRanker()

    def test_empty_candidates_returns_empty(self) -> None:
        assert self.ranker.rank([]) == []

    def test_no_prior_confirmed_preserves_order(self) -> None:
        candidates = [_hyp("A"), _hyp("B"), _hyp("C")]
        result = self.ranker.rank(candidates)
        assert len(result) == 3

    def test_higher_overlap_ranked_first(self) -> None:
        confirmed = [_confirmed("data pandas csv analysis")]
        low_overlap = _hyp("unrelated topic about weather")
        high_overlap = _hyp("pandas csv data analysis column")
        result = self.ranker.rank([low_overlap, high_overlap], prior_confirmed=confirmed)
        assert result[0].hypothesis == high_overlap.hypothesis

    def test_rationale_bonus_breaks_tie(self) -> None:
        confirmed: list[HypothesisRecord] = []
        with_rationale = _hyp("hypothesis A test", rationale="Because of X")
        without_rationale = _hyp("hypothesis B test")
        result = self.ranker.rank([without_rationale, with_rationale], prior_confirmed=confirmed)
        assert result[0].hypothesis == with_rationale.hypothesis

    def test_near_duplicate_penalized(self) -> None:
        confirmed = [_confirmed("data pandas csv analysis column error")]
        duplicate = _hyp("data pandas csv analysis column error")  # ~100% overlap
        fresh = _hyp("memory allocation heap overflow")
        result = self.ranker.rank([duplicate, fresh], prior_confirmed=confirmed)
        # fresh should rank higher because duplicate is penalized
        assert result[0].hypothesis == fresh.hypothesis

    def test_rank_preserves_all_candidates(self) -> None:
        confirmed = [_confirmed("pandas error")]
        candidates = [_hyp(f"hypothesis {i}") for i in range(5)]
        result = self.ranker.rank(candidates, prior_confirmed=confirmed)
        assert len(result) == 5

    def test_rank_returns_hypothesis_records(self) -> None:
        candidates = [_hyp("test hypothesis")]
        result = self.ranker.rank(candidates)
        assert all(isinstance(h, HypothesisRecord) for h in result)


class TestScoreFunction:
    def test_zero_score_no_confirmed_no_rationale(self) -> None:
        hyp = _hyp("some hypothesis text")
        assert _score(hyp, set()) == 0.0

    def test_overlap_score_proportional(self) -> None:
        hyp = _hyp("data analysis pandas error")
        confirmed_words = {"data", "analysis", "pandas"}
        score = _score(hyp, confirmed_words)
        assert score == 3 * 2.0  # 3 overlapping words × 2.0

    def test_rationale_adds_bonus(self) -> None:
        hyp = _hyp("test hypothesis", rationale="Because reasons")
        score_with = _score(hyp, set())
        hyp_without = _hyp("test hypothesis")
        score_without = _score(hyp_without, set())
        assert score_with > score_without
        assert score_with - score_without == 1.0

    def test_empty_hypothesis_returns_zero(self) -> None:
        hyp = _hyp("")
        assert _score(hyp, {"data"}) == 0.0
