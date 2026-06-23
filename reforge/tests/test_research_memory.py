"""Tests for P15.2 — ResearchMemory cross-session pattern recall."""

from __future__ import annotations

from pathlib import Path

from reforge.runtime.research.memory import ResearchMemory
from reforge.runtime.research.models import HypothesisRecord, ResearchResult, ResearchRound
from reforge.runtime.research.store import ResearchStore


def _make_result(
    question: str,
    confirmed: list[str] | None = None,
    rejected: list[str] | None = None,
) -> ResearchResult:
    hypotheses = [
        HypothesisRecord(hypothesis=h, status="confirmed")  # type: ignore[arg-type]
        for h in (confirmed or [])
    ] + [
        HypothesisRecord(hypothesis=h, status="rejected")  # type: ignore[arg-type]
        for h in (rejected or [])
    ]
    return ResearchResult(
        question=question,
        rounds=[ResearchRound(round_number=1)],
        final_hypotheses=hypotheses,
        total_rounds=1,
    )


class TestResearchMemoryRecall:
    def test_returns_empty_when_no_history(self, tmp_path: Path) -> None:
        store = ResearchStore(path=tmp_path / "r.jsonl")
        memory = ResearchMemory(store=store)
        result = memory.recall_patterns("Why does data analysis fail?")
        assert result == ""

    def test_returns_empty_when_no_keyword_match(self, tmp_path: Path) -> None:
        store = ResearchStore(path=tmp_path / "r.jsonl")
        store.save(_make_result("Why does pandas fail?", confirmed=["Missing column"]))
        memory = ResearchMemory(store=store)
        result = memory.recall_patterns("Completely unrelated xyz question")
        assert result == ""

    def test_returns_confirmed_patterns_for_similar_question(self, tmp_path: Path) -> None:
        store = ResearchStore(path=tmp_path / "r.jsonl")
        store.save(_make_result(
            "Why does pandas dataframe fail?",
            confirmed=["Column 'price' is missing", "NaN values in index"],
        ))
        memory = ResearchMemory(store=store)
        result = memory.recall_patterns("Why does pandas dataframe analysis break?")
        assert "Column 'price' is missing" in result
        assert "Confirmed" in result

    def test_returns_rejected_patterns(self, tmp_path: Path) -> None:
        store = ResearchStore(path=tmp_path / "r.jsonl")
        store.save(_make_result(
            "Why does data processing fail?",
            confirmed=["Memory overflow"],
            rejected=["Network timeout"],
        ))
        memory = ResearchMemory(store=store)
        result = memory.recall_patterns("Why does data processing crash?")
        assert "Network timeout" in result
        assert "Rejected" in result

    def test_skips_result_with_no_confirmed_or_rejected(self, tmp_path: Path) -> None:
        store = ResearchStore(path=tmp_path / "r.jsonl")
        store.save(ResearchResult(
            question="Why does pandas fail?",
            final_hypotheses=[
                HypothesisRecord(hypothesis="H1", status="inconclusive"),  # type: ignore[arg-type]
            ],
            total_rounds=1,
        ))
        memory = ResearchMemory(store=store)
        result = memory.recall_patterns("Why does pandas analysis fail?")
        assert result == ""

    def test_multiple_similar_results_included(self, tmp_path: Path) -> None:
        store = ResearchStore(path=tmp_path / "r.jsonl")
        store.save(_make_result(
            "Why does pandas analysis fail?",
            confirmed=["Missing column X"],
        ))
        store.save(_make_result(
            "Why does pandas dataframe fail?",
            confirmed=["NaN in index"],
        ))
        memory = ResearchMemory(store=store)
        result = memory.recall_patterns("pandas fail analysis", limit=3)
        assert "Missing column X" in result
        assert "NaN in index" in result

    def test_output_includes_question_prefix(self, tmp_path: Path) -> None:
        store = ResearchStore(path=tmp_path / "r.jsonl")
        store.save(_make_result(
            "Why does data processing crash?",
            confirmed=["Heap overflow"],
        ))
        memory = ResearchMemory(store=store)
        result = memory.recall_patterns("Why does data processing crash?")
        assert "Similar:" in result


class TestResearchPlannerWithMemory:
    def test_planner_injects_memory_patterns(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock
        import json
        from reforge.runtime.research.planner import ResearchPlanner

        store = ResearchStore(path=tmp_path / "r.jsonl")
        store.save(_make_result(
            "Why does pandas dataframe fail?",
            confirmed=["Column index is wrong"],
        ))
        research_memory = ResearchMemory(store=store)

        mock_llm = MagicMock()
        mock_llm.chat.return_value = json.dumps({
            "hypotheses": [
                {"hypothesis": "H1", "rationale": "R", "verification_request": "V"}
            ],
            "reasoning": "test",
        })

        planner = ResearchPlanner(llm=mock_llm, research_memory=research_memory)
        planner.plan("Why does pandas dataframe break?")

        user_msg = mock_llm.chat.call_args[0][1]
        assert "Column index is wrong" in user_msg
        assert "Patterns from similar past research" in user_msg

    def test_planner_no_memory_no_injection(self) -> None:
        from unittest.mock import MagicMock
        import json
        from reforge.runtime.research.planner import ResearchPlanner

        mock_llm = MagicMock()
        mock_llm.chat.return_value = '{"hypotheses": [], "reasoning": "none"}'
        planner = ResearchPlanner(llm=mock_llm)
        planner.plan("Why does it fail?")
        user_msg = mock_llm.chat.call_args[0][1]
        assert "Patterns from similar past research" not in user_msg
