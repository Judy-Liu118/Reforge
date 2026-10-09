"""Instrument gate for the contract-augmented memory probe — cold arm only.

Before spending a warm/cold comparison, two things have to be true of the cold
arm, and the pre-registered pilot failed the first one:

    LOUD  — attempt 1 exits non-zero. A failure the runtime cannot see is one
            it cannot classify, retry, or store.
    SOLVE — the run eventually reaches the gold answer *by itself* (it reads
            the legend without any external hint). Without this there is no
            correct repair for a warm run to inherit, and both arms would sit
            on the floor.

Each seed case runs with memory, project state and global state isolated, so
"cold" means cold. Per attempt the script keeps the generated code, which is
what lets it answer "did the run consult the legend, or did it guess?".

    python scripts/build_memory_probe_data.py          # rebuild clean data
    python scripts/pilot_memory_probe_contract.py      # 3 cold runs

Verdict is printed per case and as a gate at the end. This spends 3 LLM runs
and decides whether the full cold/warm axis is worth running at all.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Iterator

from reforge.benchmark.memory_probe_contract import LEGEND_MARKERS, PAIRED_CASES_MPC
from reforge.memory.store import MemoryStore
from reforge.memory.substrate import CompositeMemorySubstrate

DATA_FILES = (
    "benchmark_data/mp_metrics.csv",
    "benchmark_data/mp_metrics_legend.csv",
    "benchmark_data/mp_warehouse.db",
    "benchmark_data/mp_d001.csv",
    "benchmark_data/mp_manifest.csv",
)


@contextlib.contextmanager
def _scoped_env(**pairs: str) -> Iterator[None]:
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


def run_cold(case, pair_id: str, root: Path) -> dict:
    """Run one case with every memory scope freshly isolated."""
    from reforge.runtime.orchestration.engine.runner import RuntimeRunner

    case_root = root / case.id
    substrate = CompositeMemorySubstrate(MemoryStore(base_dir=case_root / "sub"))
    (case_root / "proj").mkdir(parents=True, exist_ok=True)
    (case_root / "home").mkdir(parents=True, exist_ok=True)

    codes: list[str] = []
    final_state = None
    start = time.perf_counter()

    with _scoped_env(
        REFORGE_PROJECT_DIR=str(case_root / "proj"),
        REFORGE_HOME=str(case_root / "home"),
    ):
        runner = RuntimeRunner(memory_substrate=substrate)
        for _node, state in runner.stream(case.request):
            final_state = state
            code = state.generated_code or ""
            # One entry per distinct codegen output: the node re-runs per
            # attempt, and we want to know which attempt consulted the legend.
            if code and (not codes or codes[-1] != code):
                codes.append(code)

    duration = time.perf_counter() - start
    answer = (final_state.outcome_state.final_answer or "") if final_state else ""
    attempts_raw = final_state.attempts if final_state else []
    exit_codes = [
        int((a.get("exit_code") if isinstance(a, dict) else getattr(a, "exit_code", 0)) or 0)
        for a in attempts_raw
    ]
    marker = LEGEND_MARKERS.get(pair_id.rstrip("C"), "")
    gold = case.expected_keywords[0] if case.expected_keywords else ""

    # Why the run ended matters as much as that it ended: an early STOP on a
    # repeated fingerprint and an exhausted budget both show up as 2 attempts.
    semantic = final_state.semantic_state if final_state else None
    signatures = list(getattr(semantic, "failure_signature_history", []) or [])

    return {
        "case_id": case.id,
        "pair_id": pair_id,
        "gold": gold,
        "answer": answer.strip()[:120],
        "outcome_reason": (
            str(final_state.outcome_state.outcome_reason or "") if final_state else ""
        ),
        "policy_reason": (
            str(final_state.control_state.policy_reason or "") if final_state else ""
        ),
        "signature_history": signatures,
        "error_types": [
            (a.get("error_type") if isinstance(a, dict) else getattr(a, "error_type", ""))
            for a in attempts_raw
        ],
        "attempts": (final_state.control_state.retry_count + 1) if final_state else 0,
        "outcome": str(final_state.outcome_state.task_outcome or "") if final_state else "",
        "exit_codes": exit_codes,
        "loud_first": bool(exit_codes) and exit_codes[0] != 0,
        "legend_read": any(marker and marker in c for c in codes),
        "gold_hit": bool(gold) and gold in answer,
        "rows_line": "ROWS=" in answer,
        "duration_s": round(duration, 1),
        "n_codegen": len(codes),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="runs/mpc_pilot.jsonl")
    parser.add_argument("--pairs", default="", help="comma-separated pair ids, e.g. MP1C,MP3C")
    parser.add_argument("--keep-tmp", action="store_true")
    args = parser.parse_args()

    missing = [p for p in DATA_FILES if not Path(p).exists()]
    if missing:
        raise SystemExit(
            f"missing probe data {missing} — run "
            f"`python scripts/build_memory_probe_data.py` first"
        )

    wanted = {p.strip() for p in args.pairs.split(",") if p.strip()}
    pairs = [p for p in PAIRED_CASES_MPC if not wanted or p.pair_id in wanted]

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="reforge_mpc_"))
    print(f"isolation root: {root}")

    results = []
    try:
        with open(out_path, "a", encoding="utf-8") as fh:
            for pair in pairs:
                record = run_cold(pair.seed, pair.pair_id, root)
                results.append(record)
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                fh.flush()
                print(
                    f"  [{record['pair_id']}] "
                    f"loud={record['loud_first']} legend={record['legend_read']} "
                    f"gold={record['gold_hit']} attempts={record['attempts']} "
                    f"exit={record['exit_codes']} outcome={record['outcome']} "
                    f"answer={record['answer'][:40]!r} ({record['duration_s']}s)",
                    flush=True,
                )
    finally:
        if args.keep_tmp:
            print(f"isolation dirs kept at {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)

    loud = sum(1 for r in results if r["loud_first"])
    solved = sum(1 for r in results if r["gold_hit"])
    legend = sum(1 for r in results if r["legend_read"])
    print(f"\ngate: loud {loud}/{len(results)}  "
          f"legend-read {legend}/{len(results)}  gold-solved {solved}/{len(results)}")
    if loud and solved:
        print("→ GO: trap fires loudly and the cold arm can solve it unaided; "
              "a correct repair exists for a warm run to inherit.")
    elif loud and not solved:
        print("→ NO-GO (floor): the trap fires but the cold arm never solves it — "
              "nothing correct gets stored, both arms would sit at zero.")
    elif solved and not loud:
        print("→ NO-GO (too easy): solved without ever failing — no failure to "
              "remember, so memory has nothing to transfer.")
    else:
        print("→ NO-GO: neither loud nor solved; inspect the records before "
              "changing anything else.")
    print(f"records → {out_path}")


if __name__ == "__main__":
    main()
