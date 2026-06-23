"""Tests for P16.2/P16.3 — export_research and question_context."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from reforge.runtime.research.models import HypothesisRecord, ResearchResult, ResearchRound
from reforge.runtime.research.store import ResearchStore


def _make_result(question: str = "Why?") -> ResearchResult:
    return ResearchResult(
        research_id="test1234",
        question=question,
        rounds=[ResearchRound(round_number=1)],
        final_hypotheses=[
            HypothesisRecord(hypothesis="H1", status="confirmed"),  # type: ignore[arg-type]
        ],
        conclusion=f"Research: {question}",
        total_rounds=1,
    )


class TestResearchStoreWithId:
    def test_save_auto_assigns_timestamp(self, tmp_path: Path) -> None:
        store = ResearchStore(path=tmp_path / "r.jsonl")
        result = ResearchResult(question="Q")
        assert result.timestamp == ""
        store.save(result)
        loaded = store.list_all()[0]
        assert loaded.timestamp != ""

    def test_find_by_id_returns_matching(self, tmp_path: Path) -> None:
        store = ResearchStore(path=tmp_path / "r.jsonl")
        r1 = _make_result("Question A")
        r2 = ResearchResult(research_id="other999", question="Question B")
        store.save(r1)
        store.save(r2)
        found = store.find_by_id("test1234")
        assert found is not None
        assert found.question == "Question A"

    def test_find_by_id_returns_none_for_missing(self, tmp_path: Path) -> None:
        store = ResearchStore(path=tmp_path / "r.jsonl")
        store.save(_make_result())
        assert store.find_by_id("nonexistent") is None

    def test_research_id_persists_through_save_load(self, tmp_path: Path) -> None:
        store = ResearchStore(path=tmp_path / "r.jsonl")
        result = ResearchResult(research_id="fixed1234", question="Q")
        store.save(result)
        loaded = store.list_all()[0]
        assert loaded.research_id == "fixed1234"


class TestExportResearch:
    def test_export_creates_markdown_file(self, tmp_path: Path) -> None:
        from reforge.cli.research import export_research

        store = ResearchStore(path=tmp_path / "r.jsonl")
        store.save(_make_result("Why does analysis fail?"))

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("reforge.cli.research.ResearchStore", lambda: store)
            export_research("test1234", output_dir=tmp_path)

        md_file = tmp_path / "research_test1234.md"
        assert md_file.exists()
        content = md_file.read_text(encoding="utf-8")
        assert "Why does analysis fail?" in content
        assert "# Research Report" in content

    def test_export_fallback_to_keyword_search(self, tmp_path: Path) -> None:
        from reforge.cli.research import export_research

        store = ResearchStore(path=tmp_path / "r.jsonl")
        r = ResearchResult(
            research_id="keyword99",
            question="Why does pandas analysis fail?",
            total_rounds=1,
        )
        store.save(r)

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("reforge.cli.research.ResearchStore", lambda: store)
            export_research("pandas analysis fail", output_dir=tmp_path)

        md_file = tmp_path / "research_keyword99.md"
        assert md_file.exists()

    def test_export_unknown_id_exits(self, tmp_path: Path, capsys) -> None:
        from reforge.cli.research import export_research

        store = ResearchStore(path=tmp_path / "r.jsonl")
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("reforge.cli.research.ResearchStore", lambda: store)
            with pytest.raises(SystemExit):
                export_research("nonexistent_id_xyz", output_dir=tmp_path)


class TestQuestionContext:
    def test_context_injected_into_planner_prompt(self) -> None:
        from reforge.runtime.research.planner import ResearchPlanner

        mock_llm = MagicMock()
        mock_llm.chat.return_value = json.dumps({
            "hypotheses": [
                {"hypothesis": "H1", "rationale": "R", "verification_request": "V"}
            ],
            "reasoning": "test",
        })
        planner = ResearchPlanner(llm=mock_llm)
        planner.plan("Why does it fail?", context="Dataset has 1M rows loaded from S3")

        user_msg = mock_llm.chat.call_args[0][1]
        assert "Dataset has 1M rows loaded from S3" in user_msg
        assert "Background context" in user_msg

    def test_empty_context_not_injected(self) -> None:
        from reforge.runtime.research.planner import ResearchPlanner

        mock_llm = MagicMock()
        mock_llm.chat.return_value = '{"hypotheses": [], "reasoning": "none"}'
        planner = ResearchPlanner(llm=mock_llm)
        planner.plan("Why does it fail?", context="")

        user_msg = mock_llm.chat.call_args[0][1]
        assert "Background context" not in user_msg

    def test_session_run_passes_context_to_planner(self) -> None:
        from reforge.runtime.research.session import ResearchSession
        from reforge.runtime.research.models import ResearchPlan

        mock_planner = MagicMock()
        mock_planner.plan.return_value = ResearchPlan(question="Q", hypotheses=[])

        session = ResearchSession(
            planner=mock_planner,
            runner=MagicMock(),
            max_rounds=1,
        )
        session.run("Why fails?", question_context="Context: prod env")

        call_kwargs = mock_planner.plan.call_args[1]
        assert call_kwargs.get("context") == "Context: prod env"

    def test_session_stream_passes_context_to_planner(self) -> None:
        from reforge.runtime.research.session import ResearchSession
        from reforge.runtime.research.models import ResearchPlan

        mock_planner = MagicMock()
        mock_planner.plan.return_value = ResearchPlan(question="Q", hypotheses=[])

        session = ResearchSession(
            planner=mock_planner,
            runner=MagicMock(),
            max_rounds=1,
        )
        list(session.stream("Why fails?", question_context="Context: staging env"))

        call_kwargs = mock_planner.plan.call_args[1]
        assert call_kwargs.get("context") == "Context: staging env"
