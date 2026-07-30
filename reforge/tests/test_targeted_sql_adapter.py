"""Prove the EvalTask protocol carries SQL/BIRD, not just the self-heal suite.

Hermetic: builds a throwaway SQLite DB and injects a fake loader, so the
adapter is exercised end to end without a real BIRD install. The load-bearing
assertion is that SQL's oracle — result-set equivalence, exit_code-agnostic —
rides the same `grade` seam as SelfHealTask's exact-stdout oracle.
"""

from __future__ import annotations

import sqlite3

from reforge.benchmark.targeted.selfheal_task import SelfHealTask
from reforge.benchmark.targeted.sql_task import SqlEvalTask
from reforge.benchmark.targeted.task import EvalTask
from reforge.runtime.sql.models import SqlCase


def _toy_db(tmp_path) -> str:
    db = tmp_path / "toy.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE t (n INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(3,), (1,), (4,)])
    conn.commit()
    conn.close()
    return str(db)


def _sum_case(db_path: str) -> SqlCase:
    return SqlCase(
        case_id="toy_sum",
        db_path=db_path,
        schema_ddl="CREATE TABLE t (n INTEGER);",
        question="What is the total of all n?",
        gold_sql="SELECT SUM(n) FROM t",
        difficulty="easy",
    )


def _rows_case(db_path: str) -> SqlCase:
    return SqlCase(
        case_id="toy_rows",
        db_path=db_path,
        schema_ddl="CREATE TABLE t (n INTEGER);",
        question="List every n.",
        gold_sql="SELECT n FROM t",
        difficulty="easy",
    )


def test_both_tasks_satisfy_the_protocol():
    # runtime_checkable: proves the abstraction is not SelfHeal-shaped —
    # a genuinely different implementation conforms to the same contract.
    assert isinstance(SelfHealTask(), EvalTask)
    assert isinstance(SqlEvalTask(), EvalTask)


def test_sql_adapter_grades_by_resultset(tmp_path):
    db = _toy_db(tmp_path)
    task = SqlEvalTask(case_ids=["toy_sum"], loader=lambda **_: [_sum_case(db)])
    cases = task.load_cases()
    assert [c.case_id for c in cases] == ["toy_sum"]
    case = cases[0]

    # Gold SUM(n) = 8. parse_rows yields the string "8"; the comparator
    # normalises "8" and 8 to the same value (proven in comparator.py).
    assert task.grade(case, "8", 0) is True
    assert task.grade(case, "9", 0) is False
    # Case metadata flows through the seam.
    assert case.bucket == "easy"
    assert "SELECT" not in task.build_prompt(case)  # prompt is NL+schema, not the gold SQL
    assert "total of all n" in task.build_prompt(case)


def test_sql_oracle_is_multiset_and_exit_code_agnostic(tmp_path):
    db = _toy_db(tmp_path)
    task = SqlEvalTask(case_ids=["toy_rows"], loader=lambda **_: [_rows_case(db)])
    case = task.load_cases()[0]

    # Order-insensitive (gold has no ORDER BY): rows in any order pass.
    assert task.grade(case, "1\n3\n4", 0) is True
    assert task.grade(case, "4\n1\n3", 0) is True
    # Wrong multiset fails.
    assert task.grade(case, "1\n3\n3", 0) is False

    # THE proof point: SQL correctness ignores exit_code — a nonzero exit
    # with correct rows still grades True, the opposite of SelfHealTask,
    # yet both ride the identical `grade(case, stdout, exit_code)` seam.
    assert task.grade(case, "1\n3\n4", 1) is True
    assert SelfHealTask().grade(
        SelfHealTask().load_cases()[0], "14", 1
    ) is False


def test_missing_pick_raises(tmp_path):
    db = _toy_db(tmp_path)
    task = SqlEvalTask(case_ids=["absent_id"], loader=lambda **_: [_sum_case(db)])
    try:
        task.load_cases()
    except RuntimeError as exc:
        assert "absent_id" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError for a missing pick")
