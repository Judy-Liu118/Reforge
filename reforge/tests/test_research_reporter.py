"""Tests for P16.1 — ResearchReporter Markdown rendering."""

from __future__ import annotations

from reforge.runtime.research.models import (
    HypothesisRecord,
    ResearchResult,
    ResearchRound,
)
from reforge.runtime.research.reporter import ResearchReporter


def _make_result(
    question: str = "Why does analysis fail?",
    confirmed: list[str] | None = None,
    rejected: list[str] | None = None,
    inconclusive: list[str] | None = None,
    contradictions: list[str] | None = None,
    rounds: int = 1,
) -> ResearchResult:
    hyps = []
    for h in confirmed or []:
        hyps.append(HypothesisRecord(
            hypothesis=h, status="confirmed",  # type: ignore[arg-type]
            evidence=[f"Evidence for {h[:20]}"], round_number=1,
        ))
    for h in rejected or []:
        hyps.append(HypothesisRecord(
            hypothesis=h, status="rejected", round_number=1,  # type: ignore[arg-type]
        ))
    for h in inconclusive or []:
        hyps.append(HypothesisRecord(
            hypothesis=h, status="inconclusive", round_number=1,  # type: ignore[arg-type]
        ))
    return ResearchResult(
        research_id="abc12345",
        timestamp="2026-06-10T12:00:00+00:00",
        question=question,
        rounds=[ResearchRound(round_number=i + 1) for i in range(rounds)],
        final_hypotheses=hyps,
        conclusion=f"Research question: {question}",
        contradictions_detected=contradictions or [],
        total_rounds=rounds,
    )


class TestResearchReporterHeader:
    def test_includes_research_id(self) -> None:
        result = _make_result()
        md = ResearchReporter().render(result)
        assert "abc12345" in md

    def test_includes_date(self) -> None:
        result = _make_result()
        md = ResearchReporter().render(result)
        assert "2026-06-10" in md

    def test_includes_question(self) -> None:
        result = _make_result(question="Why does data processing fail?")
        md = ResearchReporter().render(result)
        assert "Why does data processing fail?" in md

    def test_has_markdown_heading(self) -> None:
        result = _make_result()
        md = ResearchReporter().render(result)
        assert "# Research Report" in md


class TestResearchReporterSummaryTable:
    def test_shows_total_rounds(self) -> None:
        result = _make_result(rounds=3)
        md = ResearchReporter().render(result)
        assert "3" in md

    def test_counts_confirmed(self) -> None:
        result = _make_result(confirmed=["H1", "H2"])
        md = ResearchReporter().render(result)
        assert "| Confirmed | 2 |" in md

    def test_counts_rejected(self) -> None:
        result = _make_result(rejected=["H1"])
        md = ResearchReporter().render(result)
        assert "| Rejected | 1 |" in md

    def test_counts_contradictions(self) -> None:
        result = _make_result(contradictions=["C1 contradicts C2"])
        md = ResearchReporter().render(result)
        assert "| Contradictions | 1 |" in md


class TestResearchReporterHypothesisTable:
    def test_confirmed_icon(self) -> None:
        result = _make_result(confirmed=["Column missing"])
        md = ResearchReporter().render(result)
        assert "✓" in md

    def test_rejected_icon(self) -> None:
        result = _make_result(rejected=["Wrong dtype"])
        md = ResearchReporter().render(result)
        assert "✗" in md

    def test_inconclusive_icon(self) -> None:
        result = _make_result(inconclusive=["Memory leak"])
        md = ResearchReporter().render(result)
        assert "?" in md

    def test_evidence_included(self) -> None:
        result = _make_result(confirmed=["Missing column"])
        md = ResearchReporter().render(result)
        assert "Evidence for" in md

    def test_empty_hypotheses_no_table(self) -> None:
        result = _make_result()
        md = ResearchReporter().render(result)
        assert "## Hypotheses" not in md


class TestResearchReporterContradictions:
    def test_no_contradictions_message(self) -> None:
        result = _make_result()
        md = ResearchReporter().render(result)
        assert "none detected" in md

    def test_contradiction_text_included(self) -> None:
        result = _make_result(contradictions=["Confirmed X contradicts rejected X"])
        md = ResearchReporter().render(result)
        assert "Confirmed X contradicts rejected X" in md


class TestResearchReporterConclusion:
    def test_conclusion_section_present(self) -> None:
        result = _make_result(confirmed=["H1"])
        md = ResearchReporter().render(result)
        assert "## Conclusion" in md
        assert "Why does analysis fail?" in md

    def test_no_conclusion_section_if_empty(self) -> None:
        result = ResearchResult(question="Q", conclusion="")
        md = ResearchReporter().render(result)
        assert "## Conclusion" not in md
