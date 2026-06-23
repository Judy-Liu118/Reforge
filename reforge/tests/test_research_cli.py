"""Tests for P14.1/P14.3 — research question detection, run_research, history."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from reforge.cli.research import handle_research_history, is_research_question, run_research
from reforge.runtime.research.models import HypothesisRecord, ResearchResult, ResearchRound
from reforge.runtime.research.store import ResearchStore


class TestIsResearchQuestion:
    def test_english_why(self) -> None:
        assert is_research_question("Why does the data analysis fail?")

    def test_english_what_causes(self) -> None:
        assert is_research_question("What causes the KeyError in pandas?")

    def test_english_investigate(self) -> None:
        assert is_research_question("Investigate the performance bottleneck")

    def test_english_how_does(self) -> None:
        assert is_research_question("How does batch size affect training loss?")

    def test_chinese_why(self) -> None:
        assert is_research_question("为什么这段代码会报错？")

    def test_chinese_research(self) -> None:
        assert is_research_question("研究一下内存泄漏的原因")

    def test_chinese_investigate(self) -> None:
        assert is_research_question("调查系统崩溃的根本原因")

    def test_normal_task_false(self) -> None:
        assert not is_research_question("Read the CSV file and calculate mean")

    def test_multistep_task_false(self) -> None:
        assert not is_research_question("Step 1: load data. Step 2: plot results.")

    def test_case_insensitive(self) -> None:
        assert is_research_question("WHY DOES THIS FAIL")


class TestRunResearch:
    def test_run_research_calls_stream_and_saves(
        self, tmp_path: Path, capsys
    ) -> None:
        confirmed_hyp = HypothesisRecord(
            hypothesis="Data has NaN values",
            status="confirmed",
            evidence=["NaN count: 5"],
            round_number=1,
        )

        mock_session = MagicMock()
        mock_session._max_rounds = 3
        mock_session.stream.return_value = iter([(1, confirmed_hyp, confirmed_hyp)])

        store = ResearchStore(path=tmp_path / "r.jsonl")

        with patch("reforge.cli.research.ResearchSession", return_value=mock_session), \
             patch("reforge.cli.research.ResearchStore", return_value=store), \
             patch("reforge.cli.research.TrajectoryStore"):
            run_research("Why does the analysis fail?")

        mock_session.stream.assert_called_once_with("Why does the analysis fail?")
        results = store.list_all()
        assert len(results) == 1
        assert results[0].question == "Why does the analysis fail?"
        assert len(results[0].final_hypotheses) == 1

    def test_run_research_no_hypotheses_prints_message(
        self, tmp_path: Path, capsys
    ) -> None:
        mock_session = MagicMock()
        mock_session._max_rounds = 3
        mock_session.stream.return_value = iter([])

        with patch("reforge.cli.research.ResearchSession", return_value=mock_session), \
             patch("reforge.cli.research.TrajectoryStore"):
            run_research("Why does it fail?")

        captured = capsys.readouterr()
        assert "No hypotheses" in captured.out

    def test_run_research_output_shows_hypothesis_status(
        self, tmp_path: Path, capsys
    ) -> None:
        hyp = HypothesisRecord(
            hypothesis="Column is missing",
            status="confirmed",
            evidence=["column: price not found"],
            round_number=1,
        )
        mock_session = MagicMock()
        mock_session._max_rounds = 3
        mock_session.stream.return_value = iter([(1, hyp, hyp)])

        store = ResearchStore(path=tmp_path / "r.jsonl")
        with patch("reforge.cli.research.ResearchSession", return_value=mock_session), \
             patch("reforge.cli.research.ResearchStore", return_value=store), \
             patch("reforge.cli.research.TrajectoryStore"):
            run_research("Why does analysis fail?")

        captured = capsys.readouterr()
        assert "Column is missing" in captured.out
        assert "✓" in captured.out  # confirmed icon


class TestHandleResearchHistory:
    def test_empty_history_shows_message(self, tmp_path: Path, capsys) -> None:
        store = ResearchStore(path=tmp_path / "r.jsonl")
        with patch("reforge.cli.research.ResearchStore", return_value=store):
            handle_research_history()
        captured = capsys.readouterr()
        assert "No research history" in captured.out

    def test_history_shows_question(self, tmp_path: Path, capsys) -> None:
        store = ResearchStore(path=tmp_path / "r.jsonl")
        store.save(ResearchResult(
            question="Why does the data fail?",
            rounds=[ResearchRound(round_number=1)],
            final_hypotheses=[HypothesisRecord(status="confirmed")],  # type: ignore[arg-type]
            total_rounds=1,
        ))
        with patch("reforge.cli.research.ResearchStore", return_value=store):
            handle_research_history()
        captured = capsys.readouterr()
        assert "Why does the data fail?" in captured.out
