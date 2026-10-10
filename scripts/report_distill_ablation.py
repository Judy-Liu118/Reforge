"""Report for run_distill_ablation.py — paired arm contrasts with t-based 95% CI.

Pairing unit is the replicate: for each replicate, the success rate over
transfer tasks in arm A minus arm B in the same replicate (same shuffled
order, same tasks). CI uses the t distribution (df = reps − 1).

Contrasts: within/cross/placebo vs cold, and within/cross vs placebo — the
last two separate "distilled content transfers" from "any card primes care".
If `<src>.labels.jsonl` exists (label_distill_failures.py), failed warm runs
are split into plan_missed_trap vs plan_hit_exec_failed.

    python scripts/report_distill_ablation.py runs/distill_full.jsonl
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from report_comma_trap import ci95, load  # noqa: E402  (t-table CI, shared)

ARMS = ("seed", "cold_seed", "cold_transfer", "warm_seed", "warm_within", "warm_cross", "warm_placebo")
WARM_T = ("warm_within", "warm_cross", "warm_placebo")
CONTRASTS = (
    ("warm_within", "cold_transfer", "within-pair:  Warm − Cold"),
    ("warm_cross", "cold_transfer", "cross-pair:   Warm − Cold"),
    ("warm_placebo", "cold_transfer", "placebo:      Placebo − Cold"),
    ("warm_within", "warm_placebo", "content:      Within − Placebo"),
    ("warm_cross", "warm_placebo", "content:      Cross − Placebo"),
)


def _fmt_ci(ci) -> str:
    if ci is None:
        return "n<2, no CI"
    m, lo, hi = ci
    return f"{m:+.3f}  95% CI [{lo:+.3f}, {hi:+.3f}]"


def main() -> None:
    src = Path(sys.argv[1])
    rows = load(src)
    lab_path = src.with_suffix(".labels.jsonl")
    labels = ({(x["replicate"], x["arm"], x["case_id"]): x for x in load(lab_path)}
              if lab_path.exists() else {})

    by = defaultdict(dict)        # (rep, pair) -> arm -> row
    for r in rows:
        by[(r["replicate"], r["pair_id"])][r["arm"]] = r
    reps = sorted({k[0] for k in by})
    pairs = sorted({k[1] for k in by})

    print(f"records={len(rows)} replicates={len(reps)} pairs={len(pairs)}\n")
    print("== success rate by arm ==")
    for arm in ARMS:
        xs = [r for r in rows if r["arm"] == arm]
        if xs:
            print(f"  {arm:14s} {sum(r['passed'] for r in xs)}/{len(xs)}")

    cards = [r for r in rows if r["arm"] in ("warm_seed", "seed")]
    print(f"\n== cards == produced {sum(bool(r['card']) for r in cards)}/{len(cards)}; "
          f"polarity success={sum(r['card_polarity'] == 'success' for r in cards)} "
          f"failure={sum(r['card_polarity'] == 'failure' for r in cards)}; "
          f"leak-violations={sum(bool(r['card'].get('violations')) for r in cards if r['card'])}")
    warm_t = [r for r in rows if r["arm"] in WARM_T]
    print(f"   injected into planner: {sum(r['card_injected'] for r in warm_t)}/{len(warm_t)} warm transfer runs")

    for a, b, label in CONTRASTS:
        deltas = []
        for rep in reps:
            d = [by[(rep, p)][a]["passed"] - by[(rep, p)][b]["passed"]
                 for p in pairs if a in by[(rep, p)] and b in by[(rep, p)]]
            if d:
                deltas.append(sum(d) / len(d))
        print(f"\n== {label} (transfer success rate, per-replicate paired) ==")
        print(f"  per-rep deltas: {[round(x, 3) for x in deltas]}")
        print(f"  {_fmt_ci(ci95(deltas))}")

    # Robustness: pair as the unit (n = pairs). Replicates of a near-
    # deterministic agent are not independent draws, so the per-replicate CI
    # above can be far too narrow; this one carries between-pair variance.
    fired = [p for p in pairs
             if not all(by[(rep, p)]["cold_transfer"]["passed"]
                        for rep in reps if "cold_transfer" in by[(rep, p)])]
    for subset, tag in ((pairs, "all pairs"), (fired, "trap-fired pairs only")):
        print(f"\n== pair-level paired CI ({tag}, n={len(subset)}) ==")
        for a, b, label in CONTRASTS:
            deltas = []
            for p in subset:
                d = [by[(rep, p)][a]["passed"] - by[(rep, p)][b]["passed"]
                     for rep in reps if a in by[(rep, p)] and b in by[(rep, p)]]
                if d:
                    deltas.append(sum(d) / len(d))
            print(f"  {label:32s} {_fmt_ci(ci95(deltas))}")

    print("\n== per pair (passes / runs present) ==")
    print(f"  {'pair':5s} " + " ".join(f"{a:>13s}" for a in ARMS))
    for p in pairs:
        cells = []
        for a in ARMS:
            rs = [by[(rep, p)][a] for rep in reps if a in by[(rep, p)]]
            cells.append(f"{sum(r['passed'] for r in rs)}/{len(rs)}")
        print(f"  {p:5s} " + " ".join(f"{c:>13s}" for c in cells))

    print("\n== Cold also right (trap did not fire) ==")
    for r in rows:
        if r["arm"] == "cold_transfer" and r["passed"]:
            print(f"  rep{r['replicate']} {r['case_id']} printed={r['printed']} gold={r['gold']}")

    print("\n== Warm still wrong (not rescued) ==")
    for r in rows:
        if r["arm"] in WARM_T and not r["passed"]:
            lab = labels.get((r["replicate"], r["arm"], r["case_id"]), {})
            print(f"  rep{r['replicate']} {r['arm']:12s} {r['case_id']:28s} printed={r['printed']} "
                  f"gold={r['gold']} card={r['card_source']}:{r['card_polarity']} "
                  f"cause={lab.get('cause', '?')}")

    if labels:
        print("\n== causal labels (LLM judge vs TRUE defect; pass/fail is gold) ==")
        print(f"  {'arm':13s} plan_hits_trap code_hits_trap plan_from_card | failed: missed / hit_exec_failed")
        for arm in ("cold_transfer",) + WARM_T:
            ls = [v for k, v in labels.items() if k[1] == arm and "error" not in v]
            if not ls:
                continue
            fails = [v for v in ls if not v["passed"]]
            card = [v for v in ls if v.get("plan_reflects_card") is not None]
            print(f"  {arm:13s} "
                  f"{sum(bool(v.get('plan_addresses_trap')) for v in ls):>6}/{len(ls):<7}"
                  f"{sum(bool(v.get('code_addresses_trap')) for v in ls):>6}/{len(ls):<7}"
                  f"{sum(bool(v.get('plan_reflects_card')) for v in card):>6}/{len(card):<7}"
                  f" | {sum(v['cause'] == 'plan_missed_trap' for v in fails)} / "
                  f"{sum(v['cause'] == 'plan_hit_exec_failed' for v in fails)}")
        errs = sum("error" in v for v in labels.values())
        if errs:
            print(f"  (judge parse errors: {errs})")


if __name__ == "__main__":
    main()
