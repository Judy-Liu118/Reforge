"""SQL benchmark — unit tests using a mock RuntimeRunner + an in-memory SQLite.

We don't hit a real LLM here; the live integration is exercised by the
`docs/sql_*.md` sample reports produced separately. These tests pin:

  - result comparator semantics (order-insensitive multiset by default,
    numeric tolerance, NULL canonicalisation)
  - prompt assembly and stdout parsing round-trip
  - SqlBenchSession status mapping (correct / recovered / wrong / error)
  - reporter Markdown structure
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from reforge.runtime.sql import (
    SqlBenchReport,
    SqlBenchSession,
    SqlCase,
    SqlRun,
    compare_results,
    normalise_row,
    render_markdown,
)
from reforge.runtime.sql.comparator import run_sql
from reforge.runtime.sql.prompt import build_prompt, parse_rows


# ---------------------------------------------------------------------------
# Tiny in-test SQLite fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def school_db(tmp_path: Path) -> Path:
    db = tmp_path / "school.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE students(id INTEGER, name TEXT, grade INTEGER);
        CREATE TABLE courses(id INTEGER, title TEXT, credits INTEGER);
        INSERT INTO students VALUES (1, 'alice', 90), (2, 'bob', 75), (3, 'carol', 85);
        INSERT INTO courses VALUES (10, 'algebra', 3), (11, 'history', 2);
        """
    )
    conn.commit()
    conn.close()
    return db


# ---------------------------------------------------------------------------
# Dirty-data benchmark (only runs once `prepare_sql_dirty.py` has been built)
# ---------------------------------------------------------------------------


class TestDirtyDataBenchmark:
    """Pin the dirty-data toy benchmark.

    These tests live alongside the regular SQL benchmark and validate that:
      1. The prepare script regenerates the DB + cases on demand (idempotent)
      2. Every gold SQL produces exactly the expected ground-truth answer,
         so a future schema/data change is caught by CI long before a real
         LLM run sees it.

    The expected answers below double as documentation of *what* each dirty
    pattern is testing — see `scripts/prepare_sql_dirty.py` for the data
    quality issues each case probes.
    """

    @pytest.fixture
    def dirty_artifacts(self, tmp_path: Path):
        """Run the prepare script into a tmp dir so we never pollute repo state."""
        from scripts import prepare_sql_dirty as dirty_mod
        from unittest.mock import patch

        out_dir = tmp_path / "sql_bench"
        db_path = out_dir / "dirty.db"
        cases_path = out_dir / "dirty_cases.json"
        with patch.object(dirty_mod, "OUT_DIR", out_dir), \
             patch.object(dirty_mod, "DB_PATH", db_path), \
             patch.object(dirty_mod, "CASES_PATH", cases_path):
            dirty_mod.main()
        return SimpleNamespace(db=db_path, cases=cases_path)

    def test_prepare_script_generates_artifacts(self, dirty_artifacts):
        import json

        assert dirty_artifacts.db.exists()
        assert dirty_artifacts.cases.exists()
        data = json.loads(dirty_artifacts.cases.read_text(encoding="utf-8"))
        assert len(data) == 5
        assert {c["case_id"] for c in data} == {
            "d01_count_gold_case_insensitive",
            "d02_customers_missing_email",
            "d03_avg_completed_order_amount",
            "d04_total_spend_excluding_refunds",
            "d05_orphan_orders",
        }
        # Evidence is the agent's escape hatch for dirty patterns — must be
        # populated on every case or the benchmark loses its teaching signal.
        assert all(c["evidence"] for c in data)

    @pytest.mark.parametrize(
        "case_id, expected_rows",
        [
            # 4 Gold-tier customers: rows 1,2,3,8 (lower/Gold/GOLD/Gold).
            ("d01_count_gold_case_insensitive", [(4,)]),
            # 2 customers (id=2, id=5) have NULL email.
            ("d02_customers_missing_email",     [(2,)]),
            # Completed-status rows (including the two refunds, which were
            # status="completed"): 120.50, -45, 80, 220, 65, 150, 300, -10,
            # 55, 25 → sum 960.50, avg 96.05.
            ("d03_avg_completed_order_amount",  [(96.05,)]),
            # Positive-spend totals, customers that appear in orders only:
            # 1=120.50, 2=80, 3=220, 4=100, 5=150, 6=20, 8=300.
            (
                "d04_total_spend_excluding_refunds",
                {
                    ("Alice Chen", 120.50),
                    ("Bob Khan", 80.0),
                    ("Carol Davis", 220.0),
                    ("Dan Park", 100.0),
                    ("Eve Liu", 150.0),
                    ("Quinn", 20.0),
                    ("Sara Vega", 300.0),
                },
            ),
            # Orphan customer_ids: 99, 88 → 2 orphan orders.
            ("d05_orphan_orders", [(2,)]),
        ],
    )
    def test_gold_sql_produces_ground_truth(self, dirty_artifacts, case_id, expected_rows):
        import json

        cases = json.loads(dirty_artifacts.cases.read_text(encoding="utf-8"))
        case = next(c for c in cases if c["case_id"] == case_id)
        rows = run_sql(str(dirty_artifacts.db), case["gold_sql"])

        if isinstance(expected_rows, set):
            # multi-row, order-insensitive — used for d04 which lists customers.
            normalised = {tuple(r) for r in rows}
            assert normalised == expected_rows
        else:
            assert rows == expected_rows


# ---------------------------------------------------------------------------
# Comparator
# ---------------------------------------------------------------------------


class TestComparator:
    def test_identical_results_match(self) -> None:
        assert compare_results([(1, "alice")], [(1, "alice")])

    def test_row_order_does_not_matter_by_default(self) -> None:
        a = [(1, "alice"), (2, "bob")]
        b = [(2, "bob"), (1, "alice")]
        assert compare_results(a, b)

    def test_order_sensitive_mode(self) -> None:
        a = [(1,), (2,)]
        b = [(2,), (1,)]
        assert not compare_results(a, b, order_sensitive=True)
        assert compare_results(a, a, order_sensitive=True)

    def test_row_count_mismatch_fails(self) -> None:
        assert not compare_results([(1,)], [(1,), (2,)])

    def test_column_count_mismatch_fails(self) -> None:
        assert not compare_results([(1, 2)], [(1,)])

    def test_int_vs_float_treated_as_equal(self) -> None:
        assert compare_results([(1,)], [(1.0,)])

    def test_string_numeric_normalises(self) -> None:
        assert compare_results([("1",)], [(1,)])

    def test_null_variants_collapse_to_none(self) -> None:
        assert normalise_row((None,)) == normalise_row(("NULL",)) == normalise_row(("",))

    def test_whitespace_trimmed(self) -> None:
        assert normalise_row(("alice  ",)) == normalise_row(("alice",))


# ---------------------------------------------------------------------------
# SQL execution
# ---------------------------------------------------------------------------


class TestRunSql:
    def test_basic_select(self, school_db: Path) -> None:
        rows = run_sql(school_db, "SELECT name FROM students ORDER BY id")
        assert rows == [("alice",), ("bob",), ("carol",)]

    def test_aggregation(self, school_db: Path) -> None:
        rows = run_sql(school_db, "SELECT COUNT(*) FROM students")
        assert rows == [(3,)]

    def test_invalid_sql_raises(self, school_db: Path) -> None:
        with pytest.raises(sqlite3.OperationalError):
            run_sql(school_db, "SELECT * FROM nonexistent")


# ---------------------------------------------------------------------------
# Prompt + parsing round trip
# ---------------------------------------------------------------------------


class TestPrompt:
    def test_prompt_includes_required_pieces(self, school_db: Path) -> None:
        case = SqlCase(
            case_id="c1",
            db_path=str(school_db),
            schema_ddl="CREATE TABLE students(...)",
            question="how many students?",
            gold_sql="SELECT COUNT(*) FROM students",
        )
        prompt = build_prompt(case)
        assert str(school_db).replace("\\", "/") in prompt
        assert "CREATE TABLE students" in prompt
        assert "how many students?" in prompt
        assert " | " in prompt  # output format mentioned

    def test_evidence_block_appears(self, school_db: Path) -> None:
        case = SqlCase(
            case_id="c1",
            db_path=str(school_db),
            schema_ddl="X",
            question="q",
            gold_sql="SELECT 1",
            evidence="use the grades column",
        )
        prompt = build_prompt(case)
        assert "use the grades column" in prompt

    def test_parse_rows_skips_banners(self) -> None:
        stdout = (
            "# header\n"
            "---\n"
            "alice | 90\n"
            "bob | 75\n"
            "\n"
            "carol | 85"
        )
        assert parse_rows(stdout) == [
            ("alice", "90"), ("bob", "75"), ("carol", "85"),
        ]

    def test_parse_rows_single_column(self) -> None:
        # When there is no separator we still capture the line if it has digits
        stdout = "3\n"
        assert parse_rows(stdout) == [("3",)]


# ---------------------------------------------------------------------------
# Session status mapping
# ---------------------------------------------------------------------------


def _state(
    *,
    outcome: str = "SUCCESS",
    retry_count: int = 0,
    final_answer: str = "",
    stderr: str = "",
    score: float = 1.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        outcome_state=SimpleNamespace(task_outcome=outcome, final_answer=final_answer),
        control_state=SimpleNamespace(retry_count=retry_count),
        semantic_state=SimpleNamespace(evaluation_result=SimpleNamespace(score=score)),
        exec_state=SimpleNamespace(stderr=stderr),
    )


class _ScriptedRunner:
    def __init__(self, queue: list) -> None:
        self._q = list(queue)
        self.prompts: list[str] = []

    def run(self, prompt: str):
        self.prompts.append(prompt)
        return self._q.pop(0) if self._q else _state()


class TestSessionGrading:
    def _case(self, db: Path) -> SqlCase:
        return SqlCase(
            case_id="count_students",
            db_path=str(db),
            schema_ddl="students(id, name, grade)",
            question="how many students?",
            gold_sql="SELECT COUNT(*) FROM students",
        )

    def test_correct_first_shot(self, school_db: Path) -> None:
        runner = _ScriptedRunner([_state(final_answer="3", outcome="SUCCESS")])
        sess = SqlBenchSession(runner_factory=lambda: runner)
        report = sess.run([self._case(school_db)])
        assert report.total == 1
        assert report.runs[0].status == "correct"
        assert report.execution_accuracy == 1.0
        assert report.first_shot_accuracy == 1.0

    def test_recovered_when_retry_then_match(self, school_db: Path) -> None:
        runner = _ScriptedRunner([
            _state(final_answer="3", outcome="RECOVERED", retry_count=1),
        ])
        sess = SqlBenchSession(runner_factory=lambda: runner)
        report = sess.run([self._case(school_db)])
        assert report.runs[0].status == "recovered"
        assert report.recovered_count == 1
        assert report.first_shot_accuracy == 0.0
        assert report.execution_accuracy == 1.0

    def test_wrong_when_result_mismatches(self, school_db: Path) -> None:
        runner = _ScriptedRunner([_state(final_answer="999", outcome="SUCCESS")])
        sess = SqlBenchSession(runner_factory=lambda: runner)
        report = sess.run([self._case(school_db)])
        assert report.runs[0].status == "wrong"
        assert report.execution_accuracy == 0.0

    def test_runtime_failed_outcome_marked_error(self, school_db: Path) -> None:
        runner = _ScriptedRunner([
            _state(outcome="FAILED", stderr="OperationalError: no such table"),
        ])
        sess = SqlBenchSession(runner_factory=lambda: runner)
        report = sess.run([self._case(school_db)])
        assert report.runs[0].status == "error"
        assert "OperationalError" in report.runs[0].error

    def test_multi_row_compare(self, school_db: Path) -> None:
        case = SqlCase(
            case_id="all_students",
            db_path=str(school_db),
            schema_ddl="students(id, name, grade)",
            question="list students",
            gold_sql="SELECT name, grade FROM students",
        )
        runner = _ScriptedRunner([_state(
            final_answer="carol | 85\nalice | 90\nbob | 75",
            outcome="SUCCESS",
        )])
        report = SqlBenchSession(runner_factory=lambda: runner).run([case])
        # Order-insensitive: all 3 rows present is enough
        assert report.runs[0].status == "correct"

    def test_parallel_run_preserves_order_and_correctness(self, school_db: Path) -> None:
        import time as _t

        cases = [
            SqlCase(
                case_id=f"c{i}",
                db_path=str(school_db),
                schema_ddl="students(id, name, grade)",
                question="how many students?",
                gold_sql="SELECT COUNT(*) FROM students",
            )
            for i in range(4)
        ]

        class _SlowRunner:
            def run(self, prompt: str):
                _t.sleep(0.05)
                return _state(final_answer="3", outcome="SUCCESS")

        sess = SqlBenchSession(runner_factory=lambda: _SlowRunner(), max_workers=4)
        started = _t.perf_counter()
        report = sess.run(cases)
        elapsed = _t.perf_counter() - started

        # All 4 cases correct.
        assert [r.status for r in report.runs] == ["correct"] * 4
        # Report ordering matches case ordering, regardless of worker completion order.
        assert [r.case_id for r in report.runs] == ["c0", "c1", "c2", "c3"]
        # Parallel must finish in under ~3x of a single sleep (sequential would be ~0.2s).
        assert elapsed < 0.15, f"parallel run took {elapsed:.3f}s — not actually parallel"

    def test_max_workers_equals_one_is_sequential(self, school_db: Path) -> None:
        runner = _ScriptedRunner([
            _state(final_answer="3", outcome="SUCCESS"),
            _state(final_answer="3", outcome="SUCCESS"),
        ])
        case = self._case(school_db)
        sess = SqlBenchSession(runner_factory=lambda: runner, max_workers=1)
        report = sess.run([case, case])
        assert all(r.status == "correct" for r in report.runs)
        assert len(runner.prompts) == 2

    def test_gold_sql_failure_marked_error(self, school_db: Path) -> None:
        bad = SqlCase(
            case_id="broken_gold",
            db_path=str(school_db),
            schema_ddl="x",
            question="q",
            gold_sql="SELECT * FROM nonexistent_table",
        )
        runner = _ScriptedRunner([_state(final_answer="3", outcome="SUCCESS")])
        report = SqlBenchSession(runner_factory=lambda: runner).run([bad])
        assert report.runs[0].status == "error"
        assert "gold SQL failed" in report.runs[0].error


# ---------------------------------------------------------------------------
# Reporter
# ---------------------------------------------------------------------------


class TestReporter:
    def test_markdown_has_summary_and_table(self) -> None:
        rep = SqlBenchReport(
            runs=[
                SqlRun(
                    case_id="a", difficulty="easy", status="correct",
                    attempts=1, duration_ms=200, eval_score=1.0,
                    predicted_output="3", expected_output="3",
                ),
                SqlRun(
                    case_id="b", difficulty="medium", status="recovered",
                    attempts=2, duration_ms=400, eval_score=1.0,
                    predicted_output="alice | 90", expected_output="alice | 90",
                ),
                SqlRun(
                    case_id="c", difficulty="hard", status="wrong",
                    attempts=3, duration_ms=600, eval_score=0.5,
                    predicted_output="???", expected_output="ok",
                ),
            ],
            total_duration_ms=1200,
        )
        md = render_markdown(rep)
        assert "Execution accuracy" in md
        assert "66.7%" in md  # 2 of 3 passed
        assert "Per case" in md
        assert "OK" in md and "RECOVERED" in md and "WRONG" in md
        assert "**Predicted:**" in md
        assert "**Expected:**" in md
