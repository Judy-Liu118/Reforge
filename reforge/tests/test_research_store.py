"""Tests for P14.2 — ResearchStore JSONL persistence and query."""

from __future__ import annotations

from pathlib import Path

from reforge.runtime.research.models import HypothesisRecord, ResearchResult, ResearchRound
from reforge.runtime.research.store import ResearchStore


def _make_result(question: str, n_confirmed: int = 1) -> ResearchResult:
    hypotheses = [
        HypothesisRecord(hypothesis=f"H{i}", status="confirmed", round_number=1)
        for i in range(n_confirmed)
    ]
    return ResearchResult(
        question=question,
        rounds=[ResearchRound(round_number=1)],
        final_hypotheses=hypotheses,
        conclusion=f"Findings for: {question}",
        total_rounds=1,
    )


def test_save_and_list_all(tmp_path: Path) -> None:
    store = ResearchStore(path=tmp_path / "r.jsonl")
    r1 = _make_result("Why does data analysis fail?")
    r2 = _make_result("What causes the KeyError?")
    store.save(r1)
    store.save(r2)

    results = store.list_all()
    assert len(results) == 2
    assert results[0].question == "Why does data analysis fail?"
    assert results[1].question == "What causes the KeyError?"


def test_list_all_empty_store(tmp_path: Path) -> None:
    store = ResearchStore(path=tmp_path / "empty.jsonl")
    assert store.list_all() == []


def test_list_all_nonexistent_file(tmp_path: Path) -> None:
    store = ResearchStore(path=tmp_path / "missing.jsonl")
    assert store.list_all() == []


def test_save_preserves_hypotheses(tmp_path: Path) -> None:
    store = ResearchStore(path=tmp_path / "r.jsonl")
    r = _make_result("Why fails?", n_confirmed=3)
    store.save(r)

    loaded = store.list_all()[0]
    assert len(loaded.final_hypotheses) == 3
    assert all(h.status == "confirmed" for h in loaded.final_hypotheses)


def test_find_by_question_keyword_match(tmp_path: Path) -> None:
    store = ResearchStore(path=tmp_path / "r.jsonl")
    store.save(_make_result("Why does pandas fail with KeyError?"))
    store.save(_make_result("How to fix NaN values in dataframe?"))
    store.save(_make_result("Unrelated topic about weather"))

    results = store.find_by_question("pandas KeyError dataframe")
    assert len(results) >= 1
    assert any("pandas" in r.question.lower() for r in results)


def test_find_by_question_no_match(tmp_path: Path) -> None:
    store = ResearchStore(path=tmp_path / "r.jsonl")
    store.save(_make_result("Why does pandas fail?"))
    results = store.find_by_question("completely unrelated xyz query")
    assert results == []


def test_find_by_question_empty_query(tmp_path: Path) -> None:
    store = ResearchStore(path=tmp_path / "r.jsonl")
    store.save(_make_result("Some question"))
    assert store.find_by_question("") == []


def test_find_by_question_respects_limit(tmp_path: Path) -> None:
    store = ResearchStore(path=tmp_path / "r.jsonl")
    for i in range(5):
        store.save(_make_result(f"Why does error {i} occur in data analysis?"))
    results = store.find_by_question("why error data analysis", limit=3)
    assert len(results) <= 3


def test_find_by_question_ranks_by_overlap(tmp_path: Path) -> None:
    store = ResearchStore(path=tmp_path / "r.jsonl")
    store.save(_make_result("Why does pandas fail?"))
    store.save(_make_result("Why does pandas dataframe column analysis fail?"))

    results = store.find_by_question("pandas dataframe column analysis")
    # Result with more word overlap should come first
    assert "dataframe" in results[0].question.lower() or "column" in results[0].question.lower()


def test_malformed_line_skipped(tmp_path: Path) -> None:
    path = tmp_path / "r.jsonl"
    path.write_text('{"invalid": "json"\n' + ResearchResult(question="Q").model_dump_json() + "\n")
    store = ResearchStore(path=path)
    results = store.list_all()
    assert len(results) == 1
    assert results[0].question == "Q"
