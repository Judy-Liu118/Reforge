"""Tests for P13.1/P13.2 — HypothesisRecord and research models."""

from __future__ import annotations

from reforge.runtime.research.models import (
    HypothesisRecord,
    ResearchPlan,
    ResearchResult,
    ResearchRound,
)


def test_hypothesis_record_defaults() -> None:
    h = HypothesisRecord()
    assert h.status == "pending"
    assert h.confidence == 0.0
    assert h.evidence == []
    assert h.round_number == 0
    assert h.hypothesis_id  # auto-generated


def test_hypothesis_record_auto_id_unique() -> None:
    h1, h2 = HypothesisRecord(), HypothesisRecord()
    assert h1.hypothesis_id != h2.hypothesis_id


def test_hypothesis_record_all_status_values() -> None:
    for status in ("pending", "confirmed", "rejected", "inconclusive"):
        h = HypothesisRecord(status=status)  # type: ignore[arg-type]
        assert h.status == status


def test_hypothesis_record_model_copy_update() -> None:
    h = HypothesisRecord(hypothesis="X is true", status="pending")
    updated = h.model_copy(update={"status": "confirmed", "confidence": 0.9})
    assert updated.status == "confirmed"
    assert updated.confidence == 0.9
    assert updated.hypothesis == "X is true"  # unchanged


def test_research_plan_empty_hypotheses() -> None:
    plan = ResearchPlan(question="Why?")
    assert plan.hypotheses == []
    assert plan.reasoning == ""


def test_research_plan_with_hypotheses() -> None:
    h = HypothesisRecord(hypothesis="Data has NaN", verification_request="check null")
    plan = ResearchPlan(question="Why does the analysis fail?", hypotheses=[h])
    assert len(plan.hypotheses) == 1
    assert plan.hypotheses[0].hypothesis == "Data has NaN"


def test_research_round_defaults() -> None:
    r = ResearchRound(round_number=1)
    assert r.hypotheses_tested == []
    assert r.new_findings == []
    assert r.contradictions == []


def test_research_result_total_rounds() -> None:
    rounds = [ResearchRound(round_number=i) for i in range(1, 4)]
    result = ResearchResult(question="Q", rounds=rounds, total_rounds=3)
    assert result.total_rounds == 3
    assert len(result.rounds) == 3


def test_research_result_contradictions_list() -> None:
    result = ResearchResult(
        question="Q",
        contradictions_detected=["Confirmed X contradicts rejected X"],
    )
    assert len(result.contradictions_detected) == 1
