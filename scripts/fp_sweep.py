"""FP sweep — REAL LLM, burns tokens. Scale N to catch WRONG answers.

False positives only exist where the model produces a wrong answer that the
no-gold evaluator waves through. On tiny-N smoke a strong model got 100%
pass@1, so there were zero wrong answers and thus zero FP opportunity. This
runs enough tasks to hit the ones it gets wrong, then reports what the
evaluator did on exactly those.

Governor arm only × 1 seed × N tasks. DURABLE: every case is appended to a
JSONL as it finishes; re-running resumes and skips completed case_ids, so you
can Ctrl-C and continue, or run in batches. Prep + run from the repo root:

    python scripts/fetch_mbpp.py        # or scripts/fetch_humaneval.py
    python scripts/fp_sweep.py

Switch DATASET / N below. Delete fp_sweep_<dataset>.jsonl to start fresh.
Result JSONLs land in the repo root and are gitignored (run artifacts).
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from reforge.benchmark.targeted.driver import TargetedRecord, run_one_case
from reforge.benchmark.targeted.humaneval_task import HumanEvalTask
from reforge.benchmark.targeted.mbpp_task import MbppTask
from reforge.runtime.orchestration.engine.runner import RuntimeRunner

DATASET = "mbpp"     # "mbpp" | "humaneval"
N = 100
SEED = 0

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RECORDS = _REPO_ROOT / f"fp_sweep_{DATASET}.jsonl"


def _load_task():
    return MbppTask(limit=N) if DATASET == "mbpp" else HumanEvalTask(limit=N)


def _resume() -> dict[str, TargetedRecord]:
    done: dict[str, TargetedRecord] = {}
    if _RECORDS.exists():
        for line in _RECORDS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = TargetedRecord.from_json(line)
                done[r.case_id] = r
    return done


def main() -> None:
    task = _load_task()
    cases = task.load_cases()
    done = _resume()
    print(f"FP sweep [{DATASET}]: {len(cases)} tasks, governor arm, seed={SEED} "
          f"({len(done)} already done, resuming)\n")

    for i, case in enumerate(cases, 1):
        if case.case_id in done:
            r = done[case.case_id]
            print(f"[{i}/{len(cases)}] {case.case_id} (cached) gold_pass={r.passed}")
            continue
        rec = run_one_case(task, case, mode="governor", seed=SEED,
                           runner_factory=RuntimeRunner)
        with _RECORDS.open("a", encoding="utf-8") as fh:
            fh.write(rec.to_json() + "\n")
        done[case.case_id] = rec
        flag = "" if rec.passed else "   <-- GOLD WRONG"
        print(f"[{i}/{len(cases)}] {case.case_id} gold_pass={rec.passed} "
              f"att={rec.attempts} outcome={rec.runtime_outcome}{flag}")

    _report([done[c.case_id] for c in cases if c.case_id in done])


def _report(records: list[TargetedRecord]) -> None:
    cells: Counter[str] = Counter()
    fp_hits: list[tuple[str, int, float, str]] = []
    for rec in records:
        for obs in rec.attempt_observations:
            if obs.eval_passed and obs.oracle_correct:
                cells["TP"] += 1
            elif obs.eval_passed and not obs.oracle_correct:
                cells["FP"] += 1
                fp_hits.append((rec.case_id, obs.attempt, obs.eval_score, obs.stdout_head))
            elif not obs.eval_passed and obs.oracle_correct:
                cells["FN"] += 1
            else:
                cells["TN"] += 1

    tp, fp, fn, tn = (cells[k] for k in ("TP", "FP", "FN", "TN"))
    total = tp + fp + fn + tn
    eval_pos = tp + fp
    gold_pass = sum(1 for r in records if r.passed)
    gold_wrong = [r for r in records if not r.passed]

    print("\n================ CONFUSION (all attempts) ================")
    print(f"  tasks={len(records)}  attempts graded={total}")
    print(f"  TP={tp}  FP={fp}  FN={fn}  TN={tn}")
    if eval_pos:
        print(f"  FP rate  P(gold-wrong | eval-passed) = {fp}/{eval_pos} = {fp/eval_pos:.1%}"
              "   <-- the no-gold false-positive wall (cf. Reflexion Table 2)")
    if (fn + tp):
        print(f"  FN rate  P(eval-rejected | gold-correct) = {fn}/{fn+tp} = {fn/(fn+tp):.1%}")
    print(f"  pass@1 (gold) = {gold_pass}/{len(records)} = {gold_pass/len(records):.1%}")

    # The load-bearing view: the tasks the model got WRONG, and how the
    # evaluator ruled on them. A FP is a wrong task the evaluator accepted.
    print(f"\n---- GOLD-WRONG tasks ({len(gold_wrong)}) — the only place FP can live ----")
    for r in gold_wrong:
        last = r.attempt_observations[-1] if r.attempt_observations else None
        verdict = ("eval_passed" if last and last.eval_passed else "eval_rejected") if last else "n/a"
        print(f"  {r.case_id:12s} outcome={r.runtime_outcome:9s} att={r.attempts} "
              f"final_eval={verdict}")
    if fp_hits:
        print("\n---- FALSE POSITIVES (eval passed a gold-wrong attempt) ----")
        for cid, att, score, head in fp_hits:
            print(f"  {cid:12s} a{att} eval_score={score:.2f} out={head!r}")
    else:
        print("\n  No false positives: the evaluator rejected every wrong answer.")

    print("\nSweep done. Report the confusion + GOLD-WRONG list.")


if __name__ == "__main__":
    main()
