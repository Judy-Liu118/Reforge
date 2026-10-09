"""Comma-trap bench — report.

Reads the driver's JSONL and prints the locked metric set. Statistics follow
the discipline carried from PHASE0_METRICS §3: compute each metric per
replicate, pair the two arms within a replicate, then build the 95% CI over
the per-replicate paired deltas. Never pool runs across replicates and call
the spread a confidence interval — that treats correlated runs as independent
and shrinks the interval for free.

    python scripts/report_comma_trap.py runs/comma_trap.jsonl

With fewer than 2 replicates the CI is undefined and is reported as such
rather than as a point estimate dressed up with brackets.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

# Two-sided t critical values at 95%, indexed by degrees of freedom. Small-n
# paired designs: the normal approximation (1.96) understates the interval
# badly at df ≤ 5, which is exactly the regime this bench runs in.
_T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
        6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228}


def load(path: Path) -> list[dict]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def ci95(deltas: list[float]) -> tuple[float, float, float] | None:
    """Mean and 95% CI of paired deltas; None when n < 2."""
    n = len(deltas)
    if n < 2:
        return None
    mean = statistics.fmean(deltas)
    sd = statistics.stdev(deltas)
    if sd == 0:
        return mean, mean, mean
    t = _T95.get(n - 1, 1.96)
    half = t * sd / (n ** 0.5)
    return mean, mean - half, mean + half


def rate(rows: list[dict], key: str) -> float:
    return sum(1 for r in rows if r[key]) / len(rows) if rows else 0.0


def metrics_for(rows: list[dict]) -> dict[str, float]:
    """The locked metric set for one (replicate, arm) cell."""
    post = [r for r in rows if r["order_index"] > 1]  # after the trap is met once
    return {
        "pass_rate": rate(rows, "passed"),
        "first_try_rate": rate(rows, "first_try_pass"),
        "first_try_rate_post": rate(post, "first_try_pass"),
        "avg_attempts": statistics.fmean([r["attempts"] for r in rows]) if rows else 0.0,
        "hint_rate": rate(rows, "repair_hint_seen"),
        "tokens_per_run": statistics.fmean(
            [r["tokens_prompt"] + r["tokens_completion"] for r in rows]
        ) if rows else 0.0,
    }


_HEADLINE = [
    ("first_try_rate_post", "first-try rate (tasks 2..N)", "pp", 100),
    ("first_try_rate", "first-try rate (all tasks)", "pp", 100),
    ("pass_rate", "gold pass rate", "pp", 100),
    ("avg_attempts", "avg attempts", "", 1),
    ("tokens_per_run", "tokens per run", "", 1),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", type=Path)
    args = parser.parse_args()

    records = load(args.records)
    if not records:
        raise SystemExit(f"no records in {args.records}")

    harness_errors = [r for r in records if r["outcome"] == "HARNESS_ERROR"]
    cells: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for r in records:
        if r["outcome"] != "HARNESS_ERROR":
            cells[(r["replicate"], r["arm"])].append(r)

    replicates = sorted({rep for rep, _ in cells})
    arms = sorted({arm for _, arm in cells})

    print(f"records: {len(records)}  replicates: {len(replicates)}  arms: {arms}")
    if harness_errors:
        print(f"harness errors excluded: {len(harness_errors)}")

    per_cell = {key: metrics_for(rows) for key, rows in cells.items()}

    print("\n--- per replicate ---")
    for rep in replicates:
        for arm in arms:
            m = per_cell.get((rep, arm))
            if not m:
                continue
            print(f"rep{rep} {arm:<6} pass={m['pass_rate']:.0%} "
                  f"first_try={m['first_try_rate']:.0%} "
                  f"first_try_post={m['first_try_rate_post']:.0%} "
                  f"attempts={m['avg_attempts']:.2f} "
                  f"hint={m['hint_rate']:.0%} "
                  f"tokens={m['tokens_per_run']:.0f}")

    if "mem" not in arms or "nomem" not in arms:
        print("\n(only one arm present — no paired deltas)")
        return

    print("\n--- paired delta (mem - nomem), 95% CI over replicates ---")
    for key, label, unit, scale in _HEADLINE:
        deltas = [
            (per_cell[(rep, "mem")][key] - per_cell[(rep, "nomem")][key]) * scale
            for rep in replicates
            if (rep, "mem") in per_cell and (rep, "nomem") in per_cell
        ]
        result = ci95(deltas)
        if result is None:
            shown = f"{deltas[0]:+.2f}{unit}" if deltas else "n/a"
            print(f"{label:<28} {shown:>10}   CI undefined (n={len(deltas)} replicate)")
            continue
        mean, lo, hi = result
        verdict = "SIGNIFICANT" if lo > 0 or hi < 0 else "consistent with noise"
        print(f"{label:<28} {mean:+8.2f}{unit}   "
              f"95% CI [{lo:+.2f}, {hi:+.2f}]   {verdict}")

    # Instrument check: a null is only interpretable if memory actually fired.
    mem_rows = [r for k, rows in cells.items() if k[1] == "mem" for r in rows]
    hint_rows = [r for r in mem_rows if r["repair_hint_seen"]]
    print(f"\ninstrument: mem arm saw a repair hint on {len(hint_rows)}/{len(mem_rows)} runs")
    if not hint_rows:
        print("  WARNING: memory never injected a hint — a null here measures the "
              "instrument, not the hypothesis")


if __name__ == "__main__":
    main()
