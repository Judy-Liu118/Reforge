"""Comma-trap sequential memory bench — driver.

Question: after the runtime has hit the thousands-separator trap once, does
cross-session memory make the *next* tasks go better?

Two arms, same tasks, same order, one difference:

    nomem — every task gets a fresh memory substrate, a fresh project dir and
            a fresh global dir. Task N cannot see anything task N-1 learned.
    mem   — all tasks in a replicate share one substrate / project dir /
            global dir, so each task inherits every earlier lesson.

Both arms run the full governor pipeline; this bench isolates *memory*, not
the decision layer (that one is `REFORGE_GOVERNOR_BYPASS`, Phase 1).

Isolation covers three stores, because memory lives in three places:

    REFORGE_PROJECT_DIR  → execution_memory.jsonl (repair memory) + events
    REFORGE_HOME         → trajectories.jsonl (recurring-pattern counts)
    substrate factory    → MemoryStore (planner / reflection experience)

Isolating only the first two (as the Experience bench does) leaves the
trajectory store shared across arms — a leak this driver closes.

Grading is program-graded against the gold sum computed from the same rows
the CSV was generated from. No heuristic decides whether an answer looks
plausible: the number is right or it is not.

    # smoke: 2 tasks × 1 replicate × 2 arms = 4 runs
    python scripts/run_comma_trap.py --tasks 2 --replicates 1 --out runs/ct_smoke.jsonl

    # full: 20 tasks × 3 replicates × 2 arms = 120 runs
    python scripts/run_comma_trap.py --tasks 20 --replicates 3 --out runs/ct_full.jsonl

Records append to the JSONL as they complete, so an interrupted run keeps
everything it already measured.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import random
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator

from reforge.benchmark.comma_trap_cases import (
    CASES,
    SPEC_BY_ID,
    gold_value,
    grade_answer,
)
from reforge.benchmark.runner import _CountingSubstrate
from reforge.memory.store import MemoryStore
from reforge.memory.substrate import CompositeMemorySubstrate
from reforge.observability.llm_events import token_accounting

@dataclass
class RunRecord:
    """One task run in one arm of one replicate."""

    replicate: int
    arm: str
    order_index: int          # 1-based position in this replicate's order
    task_id: str
    passed: bool              # gold-matched
    first_try_pass: bool
    attempts: int
    outcome: str
    answer: str
    gold: float
    parsed_values: list[float] = field(default_factory=list)
    attempt_exit_codes: list[int] = field(default_factory=list)
    repair_hint_seen: bool = False
    repair_hint: str = ""
    memory_recalls: int = 0
    duration_ms: float = 0.0
    tokens_prompt: int = 0
    tokens_completion: int = 0
    tokens_unknown: bool = False
    error: str = ""


# ---------------------------------------------------------------------------
# Environment scoping


@contextlib.contextmanager
def _scoped_env(**pairs: str) -> Iterator[None]:
    """Temporarily set env vars, restoring previous values on exit."""
    previous = {k: os.environ.get(k) for k in pairs}
    os.environ.update(pairs)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# ---------------------------------------------------------------------------
# Single run


def run_one(
    case,
    *,
    substrate,
    project_dir: Path,
    home_dir: Path,
    replicate: int,
    arm: str,
    order_index: int,
) -> RunRecord:
    from reforge.runtime.orchestration.engine.runner import RuntimeRunner

    spec = SPEC_BY_ID[case.id]
    gold = gold_value(spec)
    project_dir.mkdir(parents=True, exist_ok=True)
    home_dir.mkdir(parents=True, exist_ok=True)

    counting = _CountingSubstrate(substrate)
    start = time.perf_counter()
    hint_seen = ""
    final_state = None
    error = ""

    with _scoped_env(
        REFORGE_PROJECT_DIR=str(project_dir),
        REFORGE_HOME=str(home_dir),
    ):
        runner = RuntimeRunner(memory_substrate=counting)
        try:
            with token_accounting(case_id=case.id, seed=replicate) as ledger:
                # stream(), not run(): repair_hint is per-attempt state and is
                # cleared when an attempt recalls nothing, so the final state
                # alone cannot tell us whether memory ever fired.
                for _node, state in runner.stream(case.request):
                    final_state = state
                    hint = (state.semantic_state.repair_hint or "").strip()
                    if hint and not hint_seen:
                        hint_seen = hint
        except Exception as exc:  # harness-level failure, not a task failure
            error = f"{type(exc).__name__}: {exc}"
            ledger = None

    duration_ms = (time.perf_counter() - start) * 1000

    if final_state is None:
        return RunRecord(
            replicate=replicate, arm=arm, order_index=order_index, task_id=case.id,
            passed=False, first_try_pass=False, attempts=0, outcome="HARNESS_ERROR",
            answer="", gold=gold, duration_ms=round(duration_ms, 2), error=error,
        )

    answer = final_state.outcome_state.final_answer or ""
    attempts = final_state.control_state.retry_count + 1
    passed, parsed = grade_answer(answer, gold)
    # attempts entries are AttemptRecord models in the shipped runtime, but the
    # execution node writes them as plain dicts — accept either shape.
    exit_codes = [
        int((a.get("exit_code") if isinstance(a, dict) else getattr(a, "exit_code", 0)) or 0)
        for a in final_state.attempts
    ]

    return RunRecord(
        replicate=replicate,
        arm=arm,
        order_index=order_index,
        task_id=case.id,
        passed=passed,
        first_try_pass=passed and attempts == 1,
        attempts=attempts,
        outcome=str(final_state.outcome_state.task_outcome or "UNKNOWN"),
        answer=answer[:200],
        gold=gold,
        parsed_values=parsed[:5],
        attempt_exit_codes=exit_codes,
        repair_hint_seen=bool(hint_seen),
        repair_hint=hint_seen[:200],
        memory_recalls=counting.recall_count,
        duration_ms=round(duration_ms, 2),
        tokens_prompt=getattr(ledger, "prompt_tokens", 0) if ledger else 0,
        tokens_completion=getattr(ledger, "completion_tokens", 0) if ledger else 0,
        tokens_unknown=bool(getattr(ledger, "unknown", False)) if ledger else True,
        error=error,
    )


# ---------------------------------------------------------------------------
# Arms


def run_arm(
    arm: str,
    cases: list,
    *,
    replicate: int,
    root: Path,
    out_fh,
) -> list[RunRecord]:
    """Run every case in order under one arm's isolation regime."""
    arm_root = root / f"rep{replicate}" / arm
    records: list[RunRecord] = []

    # mem: one store for the whole replicate. nomem: rebuilt per task below.
    shared_store = arm_root / "shared"
    if arm == "mem":
        shared_substrate = CompositeMemorySubstrate(MemoryStore(base_dir=shared_store / "sub"))

    for order_index, case in enumerate(cases, start=1):
        if arm == "mem":
            substrate = shared_substrate
            project_dir = shared_store / "proj"
            home_dir = shared_store / "home"
        else:
            task_root = arm_root / f"task{order_index:02d}"
            substrate = CompositeMemorySubstrate(MemoryStore(base_dir=task_root / "sub"))
            project_dir = task_root / "proj"
            home_dir = task_root / "home"

        record = run_one(
            case,
            substrate=substrate,
            project_dir=project_dir,
            home_dir=home_dir,
            replicate=replicate,
            arm=arm,
            order_index=order_index,
        )
        records.append(record)
        out_fh.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
        out_fh.flush()

        flag = "PASS" if record.passed else "FAIL"
        hint = " hint" if record.repair_hint_seen else ""
        print(
            f"  [{arm} rep{replicate} #{order_index:02d} {record.task_id}] {flag} "
            f"attempts={record.attempts} outcome={record.outcome}{hint} "
            f"({record.duration_ms / 1000:.1f}s)",
            flush=True,
        )
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=int, default=len(CASES))
    parser.add_argument("--replicates", type=int, default=3)
    parser.add_argument("--out", default="runs/comma_trap.jsonl")
    parser.add_argument("--arms", default="nomem,mem")
    parser.add_argument("--keep-tmp", action="store_true",
                        help="keep the per-run isolation dirs for forensics")
    args = parser.parse_args()

    cases = list(CASES[: args.tasks])
    data_missing = [
        c.id for c in cases if not Path(SPEC_BY_ID[c.id].csv_path).exists()
    ]
    if data_missing:
        sys.exit(
            f"missing data for {data_missing} — run "
            f"`python scripts/build_comma_trap_data.py --tasks {args.tasks}` first"
        )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]

    root = Path(tempfile.mkdtemp(prefix="reforge_ct_"))
    print(f"isolation root: {root}")
    try:
        with open(out_path, "a", encoding="utf-8") as fh:
            for replicate in range(args.replicates):
                # Same shuffled order for both arms of a replicate: the arms
                # stay paired, while order effects (which task is first, and
                # therefore pays for discovering the trap) vary across
                # replicates instead of being baked into the corpus order.
                ordered = list(cases)
                random.Random(1000 + replicate).shuffle(ordered)
                print(f"\nreplicate {replicate}: "
                      f"{' → '.join(c.id for c in ordered)}")
                for arm in arms:
                    run_arm(arm, ordered, replicate=replicate, root=root, out_fh=fh)
        print(f"\nrecords → {out_path}")
        print(f"report:  python scripts/report_comma_trap.py {out_path}")
    finally:
        if args.keep_tmp:
            print(f"isolation dirs kept at {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
