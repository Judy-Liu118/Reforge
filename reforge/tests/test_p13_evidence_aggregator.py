"""Tests for P13.3 — EvidenceAggregator status update and contradiction detection."""

from __future__ import annotations

from reforge.runtime.research.aggregator import EvidenceAggregator
from reforge.runtime.research.models import HypothesisRecord


def _hyp(hypothesis: str = "test hypothesis", status: str = "pending") -> HypothesisRecord:
    return HypothesisRecord(hypothesis=hypothesis, status=status)  # type: ignore[arg-type]


class TestEvidenceUpdate:
    def setup_method(self) -> None:
        self.agg = EvidenceAggregator()

    def test_confirmed_on_clean_output(self) -> None:
        result = self.agg.update(_hyp(), "The mean value is 42.5 rows processed", 0)
        assert result.status == "confirmed"
        assert result.confidence == 0.8

    def test_rejected_on_nonzero_exit_code(self) -> None:
        result = self.agg.update(_hyp(), "some output", 1)
        assert result.status == "rejected"
        assert result.confidence == 0.1

    def test_inconclusive_on_empty_stdout(self) -> None:
        result = self.agg.update(_hyp(), "", 0)
        assert result.status == "inconclusive"
        assert result.confidence == 0.3

    def test_inconclusive_on_whitespace_only(self) -> None:
        result = self.agg.update(_hyp(), "   \n  ", 0)
        assert result.status == "inconclusive"

    def test_inconclusive_on_error_keyword_in_output(self) -> None:
        result = self.agg.update(_hyp(), "KeyError: missing column 'price'", 0)
        assert result.status == "inconclusive"
        assert result.confidence == 0.4

    def test_evidence_stored_from_stdout(self) -> None:
        stdout = "Output: 12345 records found"
        result = self.agg.update(_hyp(), stdout, 0)
        assert result.evidence == [stdout[:300]]

    def test_evidence_truncated_at_300_chars(self) -> None:
        long_stdout = "x" * 500
        result = self.agg.update(_hyp(), long_stdout, 0)
        assert len(result.evidence[0]) == 300

    def test_no_evidence_on_empty_stdout(self) -> None:
        result = self.agg.update(_hyp(), "", 0)
        assert result.evidence == []

    def test_hypothesis_fields_preserved(self) -> None:
        h = HypothesisRecord(hypothesis="H", rationale="R", round_number=2)
        result = self.agg.update(h, "data found", 0)
        assert result.hypothesis == "H"
        assert result.rationale == "R"
        assert result.round_number == 2


class TestContradictionDetection:
    def setup_method(self) -> None:
        self.agg = EvidenceAggregator()

    def _make(self, hypothesis: str, status: str) -> HypothesisRecord:
        return HypothesisRecord(hypothesis=hypothesis, status=status)  # type: ignore[arg-type]

    def test_contradiction_detected_on_shared_words(self) -> None:
        confirmed = self._make("the data analysis shows high error rate", "confirmed")
        rejected = self._make("the data analysis shows high error rate", "rejected")
        result = self.agg.detect_contradictions([confirmed, rejected])
        assert len(result) == 1
        assert "contradicts" in result[0]

    def test_no_contradiction_when_all_confirmed(self) -> None:
        hyps = [self._make("hypothesis A value is high", "confirmed") for _ in range(3)]
        assert self.agg.detect_contradictions(hyps) == []

    def test_no_contradiction_when_no_overlap(self) -> None:
        confirmed = self._make("sky is blue", "confirmed")
        rejected = self._make("ocean floor temperature measurement", "rejected")
        assert self.agg.detect_contradictions([confirmed, rejected]) == []

    def test_no_contradiction_below_word_threshold(self) -> None:
        # Only 2 shared words — below threshold of 3
        confirmed = self._make("data analysis", "confirmed")
        rejected = self._make("data analysis", "rejected")
        assert self.agg.detect_contradictions([confirmed, rejected]) == []

    def test_multiple_contradictions_detected(self) -> None:
        c1 = self._make("pandas dataframe column analysis shows missing values", "confirmed")
        c2 = self._make("numpy array computation shows memory error problem", "confirmed")
        r1 = self._make("pandas dataframe column analysis shows missing values", "rejected")
        r2 = self._make("numpy array computation shows memory error problem", "rejected")
        result = self.agg.detect_contradictions([c1, c2, r1, r2])
        assert len(result) == 2
