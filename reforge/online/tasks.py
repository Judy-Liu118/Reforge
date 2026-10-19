"""Task sources for the online loop — TaskSpec (control) vs GoldRef (shadow).

The loop's hard rule is that gold never enters the control path. Every source
therefore splits a task in two at load time:

    TaskSpec  what the agent and the verifier may see: request + data builder
    GoldRef   the reference answer, opaque; only shadow.py calls `_score`

test_online_gold_isolation enforces the split statically (no control module
touches `_score` / `.gold`) and at runtime (a poisoned GoldRef that raises on
use must not change a single decision).

Sources yield (TaskSpec, GoldRef | None) pairs in a deterministic order, so a
resumed run sees the same stream and can skip what is already committed.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import sqlite3
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class TaskSpec:
    id: str                     # globally unique, stable across runs: "<source>:<local id>"
    source: str                 # infiagent | trap | csv | bird
    request: str
    tables: tuple[str, ...]     # source tables the task reads — the unit of the canary split
    build: Callable[[Path], Path] = field(compare=False, repr=False)   # data_dir → data_dir


class GoldRef:
    """Opaque reference answer. Holds a scorer, never exposes the value."""

    __slots__ = ("_fn",)

    def __init__(self, fn: Callable[[str], dict]) -> None:
        self._fn = fn

    def _score(self, stdout: str) -> dict:
        """{"correct": bool, "answer": float | None, "gold": ...} — shadow.py only."""
        return self._fn(stdout)

    def __repr__(self) -> str:
        return "GoldRef(<hidden>)"


Pair = tuple[TaskSpec, "GoldRef | None"]

_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def last_number(stdout: str) -> float | None:
    """Last number printed (same rule as the frozen verifier ladder)."""
    for raw in reversed(_NUMBER_RE.findall(stdout or "")):
        try:
            return float(raw.replace(",", ""))
        except ValueError:
            continue
    return None


def numeric_gold(get_gold: Callable[[], float], tol: float,
                 extract: Callable[[str], float | None] = last_number) -> GoldRef:
    """GoldRef for a single-number answer. *get_gold* is lazy: trap cases only
    know their gold after build()."""
    def score(stdout: str) -> dict:
        ans, gold = extract(stdout), get_gold()
        return {"correct": ans is not None and abs(ans - gold) <= tol, "answer": ans, "gold": gold}
    return GoldRef(score)


# ---------------------------------------------------------------------------
# InfiAgent-DABench (natural questions, single numeric label)
# ---------------------------------------------------------------------------

def _num(s) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def infiagent_tasks(data_dir: Path, *, levels: Iterable[str] | None = None) -> list[Pair]:
    """Single-numeric-answer subset; request text identical to run_verifier_infiagent.DACase."""
    data_dir = Path(data_dir)
    read = lambda name: [json.loads(x) for x in (data_dir / name).read_text(encoding="utf-8").splitlines()  # noqa: E731
                         if x.strip()]
    labels = {j["id"]: j["common_answers"] for j in read("da-dev-labels.jsonl")}
    want = set(levels) if levels else None
    out: list[Pair] = []
    for q in read("da-dev-questions.jsonl"):
        ans = labels.get(q["id"], [])
        table = data_dir / "da-dev-tables" / q["file_name"]
        if len(ans) != 1 or _num(ans[0][1]) is None or not table.exists():
            continue
        if want and q.get("level") not in want:
            continue
        name, raw = ans[0]
        request = (f"Read the file {q['file_name']} in the current directory and answer:\n{q['question']}\n"
                   f"Constraints: {q['constraints']}\n"
                   f"The LAST line of output must be exactly in this format: {q['format']}")

        def build(d: Path, _t=table) -> Path:
            d.mkdir(parents=True, exist_ok=True)
            shutil.copy(_t, d / _t.name)
            return d

        def extract(stdout: str, _n=name) -> float | None:
            m = re.findall(rf"@{re.escape(_n)}\[([^\]]*)\]", stdout or "")
            return _num(m[-1].strip()) if m else None

        spec = TaskSpec(id=f"infiagent:da{q['id']:04d}", source="infiagent", request=request,
                        tables=(q["file_name"],), build=build)
        # benchmark's own rule: |pred − label| < 1e-6 on the value inside @name[...]
        out.append((spec, numeric_gold(lambda _g=float(raw): _g, 1e-6, extract)))
    return out


# ---------------------------------------------------------------------------
# Trap-injected cases (duck-typed scripts/learning_loop_cases.LoopCase)
# ---------------------------------------------------------------------------

def trap_tasks(cases: Iterable) -> list[Pair]:
    """Needs c.id / c.request / c.table / c.build(str) / c.gold / c.tolerance.

    `gold` is set by build(); the GoldRef reads it lazily at scoring time.
    Admission screening (LoopCase.validate) is offline corpus construction and
    is never called from here.
    """
    out: list[Pair] = []
    for c in cases:
        def build(d: Path, _c=c) -> Path:
            _c.build(str(d))
            return d
        spec = TaskSpec(id=f"trap:{c.id}", source="trap", request=c.request, tables=(c.table,), build=build)
        out.append((spec, numeric_gold(lambda _c=c: _c.gold, c.tolerance)))
    return out


# ---------------------------------------------------------------------------
# Local CSV directory: <root>/tasks.jsonl + the files it names
# ---------------------------------------------------------------------------

def csv_dir_tasks(root: Path) -> list[Pair]:
    """Each line: {"id", "request", "files": [...], "gold"?: float, "tolerance"?: float}.

    gold is optional — a local task without one simply has no shadow row.
    """
    root = Path(root)
    out: list[Pair] = []
    for line in (root / "tasks.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        t = json.loads(line)
        files = [root / f for f in t["files"]]

        def build(d: Path, _fs=files) -> Path:
            d.mkdir(parents=True, exist_ok=True)
            for f in _fs:
                shutil.copy(f, d / f.name)
            return d

        spec = TaskSpec(id=f"csv:{t['id']}", source="csv", request=t["request"],
                        tables=tuple(Path(f).name for f in t["files"]), build=build)
        ref = numeric_gold(lambda _g=float(t["gold"]): _g, float(t.get("tolerance", 1e-6))) \
            if t.get("gold") is not None else None
        out.append((spec, ref))
    return out


# ---------------------------------------------------------------------------
# BIRD dev (phase 1: adapter + smoke only)
# ---------------------------------------------------------------------------

def _scalar(db: Path, sql: str) -> float | None:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute(sql).fetchmany(2)
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return _num(rows[0][0]) if len(rows) == 1 and len(rows[0]) == 1 else None


def bird_tasks(root: Path, *, limit: int = 5, db_ids: Iterable[str] | None = None) -> list[Pair]:
    """BIRD questions whose gold SQL returns ONE numeric scalar, posed to the
    data-analysis agent (sqlite file in cwd, answer = last number printed).

    Selecting by answer *type* runs the gold SQL here, in the source — the
    same kind of offline curation as InfiAgent's single-numeric subset. The
    frozen verifier's probes only read CSVs, so these tasks reach the critic
    with a schema profile instead (see scripts/online_adapters.py).
    """
    root = Path(root)
    wanted = set(db_ids) if db_ids else None
    out: list[Pair] = []
    for item in json.loads((root / "dev.json").read_text(encoding="utf-8")):
        if len(out) >= limit:
            break
        db_id = item["db_id"]
        db = root / "dev_databases" / db_id / f"{db_id}.sqlite"
        if (wanted and db_id not in wanted) or not db.exists():
            continue
        gold = _scalar(db, item["SQL"])
        if gold is None:
            continue
        request = (f"当前目录下有 SQLite 数据库文件 {db.name}。用 Python(sqlite3 或 pandas)查询它来回答:\n"
                   f"{item['question']}\n"
                   + (f"提示: {item['evidence']}\n" if item.get("evidence") else "")
                   + "最后一行只打印答案数值。")

        def build(d: Path, _db=db) -> Path:
            d.mkdir(parents=True, exist_ok=True)
            dst = d / _db.name
            try:
                os.link(_db, dst)          # databases are large and opened read-only
            except OSError:
                shutil.copy(_db, dst)
            return d

        spec = TaskSpec(id=f"bird:{item.get('question_id', len(out))}", source="bird", request=request,
                        tables=(db_id,), build=build)
        out.append((spec, numeric_gold(lambda _g=gold: _g, 1e-4 * max(1.0, abs(gold)))))
    return out


# ---------------------------------------------------------------------------
# Stream assembly
# ---------------------------------------------------------------------------

def split_by_table(pairs: Iterable[Pair], holdout: set[str]) -> tuple[list[Pair], list[Pair]]:
    """(stream, held) — a task touching any held-out table goes to `held`."""
    stream, held = [], []
    for p in pairs:
        (held if set(p[0].tables) & holdout else stream).append(p)
    return stream, held


def mix(sources: dict[str, list[Pair]], weights: dict[str, float], seed: int = 0) -> Iterator[Pair]:
    """Deterministic weighted interleave: same inputs + seed → same order.

    Each source keeps its own order; at every step the next source is drawn
    with probability ∝ weight among those not yet exhausted.
    """
    rng = random.Random(seed)
    queues = {k: list(v) for k, v in sources.items() if v and weights.get(k, 0) > 0}
    pos = dict.fromkeys(queues, 0)
    while queues:
        names = sorted(queues)
        k = rng.choices(names, weights=[weights[n] for n in names])[0]
        yield queues[k][pos[k]]
        pos[k] += 1
        if pos[k] >= len(queues[k]):
            del queues[k]
