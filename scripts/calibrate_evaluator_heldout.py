"""Held-out evaluator calibration (KNOWN_LIMITATIONS L6 gating fix).

Protocol and results: docs/eval/EVALUATOR_CALIBRATION.md.

Population: the locked Phase 1 pool (dialect-fn filter + calibration-question
exclusion, per docs/eval/PHASE1_CORPUS.md) MINUS the 20 Phase 1 picks.
Sample: 300 questions, seed 20260711 (differs from the corpus seed 20260710).

For each held-out question the GOLD SQL is run and stdout is synthesized in
the exact format the runtime prompt contracts ("fields joined by ' | ', NULL
for None, nothing else"). A calibrated evaluator must accept that stdout —
it is correct by construction. The "before" arm reproduces pre-fix behavior
by disabling only the output-contract gate, the sole behavioral delta.

Negative controls per question: empty stdout, traceback stdout, exit_code=1
— all must stay rejected before AND after.

Run: python scripts/calibrate_evaluator_heldout.py
"""

from __future__ import annotations

import random
import re
from collections import Counter

from reforge.benchmark.phase1.corpus import (
    CALIBRATION_QUESTION_IDS,
    PHASE1_CASE_IDS,
    _DIALECT_FN,
)
from reforge.runtime.domain.state.models import (
    ExecutionState,
    RuntimeState,
    SemanticState,
)
from reforge.runtime.orchestration.evaluation.heuristics import HeuristicEvaluator
from reforge.runtime.sql.bird_loader import load_bird_dev
from reforge.runtime.sql.comparator import run_sql
from reforge.runtime.sql.prompt import build_prompt

SAMPLE_SEED = 20260711
SAMPLE_N = 300

TRACEBACK = (
    'Traceback (most recent call last):\n'
    '  File "gen.py", line 3, in <module>\n'
    "KeyError: 0\n"
)


class BeforeFixEvaluator(HeuristicEvaluator):
    """Pre-fix behavior: the output-contract gate never matches."""

    _OUTPUT_CONTRACT_RE = re.compile(r"(?!x)x")


def synthesize_stdout(rows: list[tuple]) -> str:
    return "\n".join(
        " | ".join("NULL" if v is None else str(v) for v in row) for row in rows
    ) + ("\n" if rows else "")


def verdict(evaluator: HeuristicEvaluator, prompt: str, stdout: str, exit_code: int = 0):
    state = RuntimeState(
        user_request=prompt,
        exec_state=ExecutionState(stdout=stdout, stderr="", exit_code=exit_code),
        semantic_state=SemanticState(),
    )
    return evaluator.evaluate(state)


def main() -> None:
    cases = {c.case_id: c for c in load_bird_dev()}
    pool = [
        c for c in cases.values()
        if not _DIALECT_FN.search(c.gold_sql)
        and int(c.case_id.split("_")[1]) not in CALIBRATION_QUESTION_IDS
        and c.case_id not in PHASE1_CASE_IDS
    ]
    pool.sort(key=lambda c: int(c.case_id.split("_")[1]))
    sample = random.Random(SAMPLE_SEED).sample(pool, SAMPLE_N)

    before, after = BeforeFixEvaluator(), HeuristicEvaluator()

    n_graded = n_empty_gold = sql_errors = 0
    fn_before = fn_after = 0
    after_residual_checks: Counter = Counter()
    integrity_fail: Counter = Counter()

    for case in sample:
        try:
            rows = run_sql(case.db_path, case.gold_sql)
        except Exception:
            sql_errors += 1
            continue
        prompt = build_prompt(case)
        stdout = synthesize_stdout(rows)
        if not stdout.strip():
            n_empty_gold += 1  # correct answer IS empty — separate bucket
            continue
        n_graded += 1
        if not verdict(before, prompt, stdout).passed:
            fn_before += 1
        r_after = verdict(after, prompt, stdout)
        if not r_after.passed:
            fn_after += 1
            after_residual_checks[
                tuple(sorted(c.name for c in r_after.checks if not c.passed))
            ] += 1
        for tag, (out, code) in {
            "empty": ("", 0), "traceback": (TRACEBACK, 0), "exit1": (stdout, 1),
        }.items():
            if verdict(before, prompt, out, code).passed:
                integrity_fail[f"before:{tag}"] += 1
            if verdict(after, prompt, out, code).passed:
                integrity_fail[f"after:{tag}"] += 1

    print(
        f"pool={len(pool)} sampled={SAMPLE_N} graded={n_graded} "
        f"empty_gold={n_empty_gold} sql_errors={sql_errors}"
    )
    print(f"FN before fix: {fn_before}/{n_graded} = {fn_before / n_graded:.1%}")
    print(f"FN after  fix: {fn_after}/{n_graded} = {fn_after / n_graded:.1%}")
    for combo, n in after_residual_checks.most_common():
        print(f"  residual {n:3d}  {list(combo)}")
    print("integrity failures (should be empty):", dict(integrity_fail) or "none")


if __name__ == "__main__":
    main()
