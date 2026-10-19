"""Online loop task sources + shadow log."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from reforge.online import shadow
from reforge.online.tasks import (
    GoldRef, csv_dir_tasks, bird_tasks, infiagent_tasks, last_number, mix, numeric_gold,
    split_by_table, trap_tasks,
)


def _infiagent_fixture(root: Path) -> Path:
    (root / "da-dev-tables").mkdir(parents=True)
    (root / "da-dev-tables" / "a.csv").write_text("x\n1\n2\n", encoding="utf-8")
    (root / "da-dev-tables" / "b.csv").write_text("y\n3\n", encoding="utf-8")
    qs = [
        {"id": 1, "file_name": "a.csv", "question": "mean x?", "constraints": "c", "format": "@m[v]", "level": "easy"},
        {"id": 2, "file_name": "b.csv", "question": "two answers", "constraints": "c", "format": "@a[v] @b[w]",
         "level": "hard"},
        {"id": 3, "file_name": "missing.csv", "question": "q", "constraints": "c", "format": "@m[v]", "level": "easy"},
        {"id": 4, "file_name": "b.csv", "question": "text", "constraints": "c", "format": "@t[v]", "level": "easy"},
    ]
    labels = [{"id": 1, "common_answers": [["m", "1.5"]]},
              {"id": 2, "common_answers": [["a", "1"], ["b", "2"]]},
              {"id": 3, "common_answers": [["m", "1"]]},
              {"id": 4, "common_answers": [["t", "yes"]]}]
    (root / "da-dev-questions.jsonl").write_text("\n".join(json.dumps(q) for q in qs), encoding="utf-8")
    (root / "da-dev-labels.jsonl").write_text("\n".join(json.dumps(x) for x in labels), encoding="utf-8")
    return root


def test_goldref_hides_value():
    ref = numeric_gold(lambda: 42.0, 0.01)
    assert "42" not in repr(ref)
    assert not hasattr(ref, "__dict__")
    assert ref._score("answer 42.001") == {"correct": True, "answer": 42.001, "gold": 42.0}
    assert ref._score("nothing")["correct"] is False


def test_last_number_handles_separators():
    assert last_number("a 1,234.5 then 7") == 7.0
    assert last_number("total 1,234.5") == 1234.5
    assert last_number("") is None


def test_infiagent_single_numeric_subset(tmp_path):
    pairs = infiagent_tasks(_infiagent_fixture(tmp_path))
    assert [s.id for s, _ in pairs] == ["infiagent:da0001"]
    spec, ref = pairs[0]
    assert spec.tables == ("a.csv",) and "a.csv" in spec.request and "@m[v]" in spec.request
    d = spec.build(tmp_path / "w")
    assert (d / "a.csv").exists()
    assert ref._score("@m[1.5]")["correct"] and not ref._score("1.5")["correct"]   # benchmark's @name rule
    assert infiagent_tasks(tmp_path, levels={"hard"}) == []


def test_trap_gold_is_lazy(tmp_path):
    case = SimpleNamespace(id="footer__t", request="sum", table="t.csv", tolerance=0.01, gold=0.0)

    def build(d):
        Path(d).mkdir(parents=True, exist_ok=True)
        case.gold = 9.5                      # LoopCase sets gold inside build()
    case.build = build
    (spec, ref), = trap_tasks([case])
    assert spec.id == "trap:footer__t" and spec.source == "trap"
    spec.build(tmp_path / "w")
    assert ref._score("9.5")["correct"]


def test_csv_dir_gold_optional(tmp_path):
    (tmp_path / "s.csv").write_text("v\n1\n", encoding="utf-8")
    (tmp_path / "tasks.jsonl").write_text(
        json.dumps({"id": "a", "request": "r", "files": ["s.csv"], "gold": 1}) + "\n"
        + json.dumps({"id": "b", "request": "r", "files": ["s.csv"]}) + "\n", encoding="utf-8")
    (a, ra), (b, rb) = csv_dir_tasks(tmp_path)
    assert a.id == "csv:a" and ra is not None and rb is None
    assert (b.build(tmp_path / "w") / "s.csv").exists()


def test_bird_numeric_scalar_only(tmp_path):
    db = tmp_path / "dev_databases" / "toy" / "toy.sqlite"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    conn.executescript("CREATE TABLE t(v INT); INSERT INTO t VALUES (1),(2),(3);")
    conn.commit()
    conn.close()
    items = [{"question_id": 0, "db_id": "toy", "question": "count", "SQL": "SELECT COUNT(*) FROM t"},
             {"question_id": 1, "db_id": "toy", "question": "rows", "SQL": "SELECT v FROM t"},
             {"question_id": 2, "db_id": "toy", "question": "bad", "SQL": "SELECT nope"}]
    (tmp_path / "dev.json").write_text(json.dumps(items), encoding="utf-8")
    pairs = bird_tasks(tmp_path)
    assert [s.id for s, _ in pairs] == ["bird:0"]
    spec, ref = pairs[0]
    assert (spec.build(tmp_path / "w") / "toy.sqlite").exists()
    assert ref._score("答案\n3")["correct"]


def _pair(i, table):
    from reforge.online.tasks import TaskSpec
    return TaskSpec(id=f"s:{i}", source="s", request="r", tables=(table,), build=lambda d: d), None


def test_split_by_table_is_disjoint():
    pairs = [_pair(1, "a"), _pair(2, "b"), _pair(3, "a")]
    stream, held = split_by_table(pairs, {"a"})
    assert [p[0].id for p in held] == ["s:1", "s:3"] and [p[0].id for p in stream] == ["s:2"]


def test_mix_deterministic_and_complete():
    src = {"x": [_pair(i, "t") for i in range(5)], "y": [_pair(10 + i, "t") for i in range(3)]}
    a = [p[0].id for p in mix(src, {"x": 1, "y": 1}, seed=7)]
    b = [p[0].id for p in mix(src, {"x": 1, "y": 1}, seed=7)]
    assert a == b and sorted(a) == sorted(p[0].id for v in src.values() for p in v)
    ys = {"s:10", "s:11", "s:12"}
    assert [i for i in a if i in ys] == ["s:10", "s:11", "s:12"]                 # per-source order kept
    assert not ys & {p[0].id for p in mix(src, {"x": 1, "y": 0})}


def test_shadow_rows_and_dedupe(tmp_path):
    log = shadow.ShadowLog(tmp_path / "shadow.jsonl")
    ref = numeric_gold(lambda: 2.0, 0.01)
    v = {"label": "fail", "confidence": 0.95, "tier": "L1b", "critic_verdict": "ok", "critic_conf": 0.8}
    row = log.record(task_id="t", try_id="x", attempt=1, source="trap", gold_ref=ref, stdout="3", verdict=v)
    assert row["answer_correct"] is False and row["label_correct"] is True
    assert row["critic_verdict_correct"] is False
    assert log.record(task_id="t", try_id="x", attempt=1, source="trap", gold_ref=ref, stdout="3", verdict=v) is None
    assert log.record(task_id="u", try_id="x", attempt=1, source="csv", gold_ref=None, stdout="3", verdict=v) is None
    assert shadow.ShadowLog(tmp_path / "shadow.jsonl").has("t", "x", 1)            # reload keeps keys
    unv = log.record(task_id="t2", try_id="y", attempt=1, source="trap", gold_ref=ref, stdout="2",
                     verdict={"label": "unverified", "confidence": 0.4})
    assert unv["label_correct"] is None and unv["answer_correct"] is True


def test_shadow_scorer_crash_is_recorded(tmp_path):
    def boom(_):
        raise ValueError("x")
    row = shadow.ShadowLog(tmp_path / "s.jsonl").record(
        task_id="t", try_id="x", attempt=1, source="s", gold_ref=GoldRef(boom), stdout="", verdict={})
    assert row["answer_correct"] is None and "ValueError" in row["score_error"]


def test_calibration_bins():
    rows = [{"critic_conf": 0.9, "critic_verdict_correct": True},
            {"critic_conf": 0.9, "critic_verdict_correct": False},
            {"critic_conf": 0.3, "critic_verdict_correct": True},
            {"critic_conf": None, "critic_verdict_correct": True}]
    bins = {(b["lo"], b["n"]): b["accuracy"] for b in shadow.calibration(rows)}
    assert bins[(0.85, 2)] == 0.5 and bins[(0.0, 1)] == 1.0
