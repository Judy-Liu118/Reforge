"""Report for run_learning_loop.py.

Pass rates are scored against gold for every arm. Contrasts are paired at the
TABLE level (one value per table = mean over replicates), t-interval over
tables: replicates of one table are near-deterministic for this agent, so
treating them as independent would make the interval too narrow.

    python scripts/report_learning_loop.py runs/learning_loop.jsonl
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev

ARMS = ("cold", "warm_gold", "warm_critic", "warm_gold_diag")
CONTRASTS = (("lift: warm_gold − cold", "warm_gold", "cold"),
             ("lift: warm_critic − cold", "warm_critic", "cold"),
             ("lift: warm_gold_diag − cold", "warm_gold_diag", "cold"),
             ("judge-noise cost: warm_gold_diag − warm_critic", "warm_gold_diag", "warm_critic"),
             ("diagnosis value: warm_gold_diag − warm_gold", "warm_gold_diag", "warm_gold"))
# two-sided 95% t quantiles, df = 1..30
_T = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145, 2.131,
      2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042]


def ci(d: list[float]) -> str:
    n = len(d)
    if n < 2:
        return f"{mean(d):+.3f} (n={n}, no CI)"
    h = _T[min(n - 2, 29)] * stdev(d) / math.sqrt(n)
    return f"{mean(d):+.3f} [{mean(d) - h:+.3f}, {mean(d) + h:+.3f}] (n={n} tables)"


def main() -> None:
    rows = [json.loads(x) for x in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = [r for r in rows if not r["error"]]
    cell = defaultdict(list)                       # (arm, case) -> [passed per rep]
    for r in rows:
        cell[(r["arm"], r["case_id"])].append(r["passed"])
    cases = sorted({c for _, c in cell})
    full = [c for c in cases if all((a, c) in cell for a in ARMS)]
    print(f"records={len(rows)} tables={len(cases)} tables with all 4 arms={len(full)}  "
          f"verifier_sha={sorted({r['verifier_sha'] for r in rows})}\n")

    print("== pass rate by arm (scored against gold) ==")
    for a in ARMS:
        v = [p for c in full for p in cell[(a, c)]]
        print(f"  {a:15s} {sum(v):3d}/{len(v):3d} = {mean(v):.3f}")

    print("\n== paired contrasts, table level, 95% t-CI ==")
    for name, x, y in CONTRASTS:
        print(f"  {name:48s} {ci([mean(cell[(x, c)]) - mean(cell[(y, c)]) for c in full])}")

    print("\n== by trap (passes / runs) ==")
    traps = sorted({c.split("__")[0] for c in full})
    print(f"  {'trap':14s}" + "".join(f"{a:>16s}" for a in ARMS))
    for t in traps:
        line = f"  {t:14s}"
        for a in ARMS:
            v = [p for c in full if c.startswith(t + "__") for p in cell[(a, c)]]
            line += f"{sum(v):>11d}/{len(v):<4d}"
        print(line)

    seeds = [r for r in rows if r["arm"] == "cold"]
    print(f"\n== in-loop judging of the {len(seeds)} seed runs ==")
    by = defaultdict(int)
    for r in seeds:
        by[("gold " + ("right" if r["passed"] else "wrong"), "critic " + r["critic_verdict"])] += 1
    for k, n in sorted(by.items()):
        print(f"  {k[0]:11s} × {k[1]:15s} {n}")
    flipped = [r for r in seeds if r["judge_agree"] is False]
    print(f"  abstentions (no card, warm_critic ran on empty memory): {sum(r['critic_verdict'] == 'abstain' for r in seeds)}")
    print(f"  reverse-polarity critic cards: {len(flipped)}")
    for r in flipped:
        print(f"    rep{r['replicate']} {r['case_id']} gold={'right' if r['passed'] else 'wrong'} "
              f"critic={r['critic_verdict']} printed={r['printed']} | {r['critic_suspect'][:120]}")

    print("\n== warm arms split by whether the seed's two judges agreed ==")
    for a in ARMS[1:]:
        for lab, key in (("agree", True), ("disagree", False), ("abstain", None)):
            v = [r["passed"] for r in rows if r["arm"] == a and r.get("seed_judge_agree", 0) is key]
            if v:
                print(f"  {a:15s} seed judges {lab:9s} {sum(v):3d}/{len(v):3d}")

    print("\n== failed warm runs (for plan-hit / execution-miss labelling) ==")
    for r in rows:
        if r["arm"] != "cold" and not r["passed"]:
            print(f"  rep{r['replicate']} {r['arm']:15s} {r['case_id']:28s} printed={r['printed']} gold={r['gold']} "
                  f"exits={r['attempt_exit_codes']} injected={r['card_injected']} card={r['card_polarity'] or 'none'}")

    tok = defaultdict(int)
    for r in rows:
        tok["agent"] += r["tokens_prompt"] + r["tokens_completion"]
        tok["distiller"] += r.get("distill_tokens") or 0
    cp = Path(sys.argv[1]).with_suffix(".critic.jsonl")
    if cp.exists():
        cr = [json.loads(x) for x in cp.read_text(encoding="utf-8").splitlines() if x.strip()]
        tok["critic"], tok["critic_calls"] = sum(c["total_tokens"] for c in cr), len(cr)
    print("\n== tokens ==\n  " + "  ".join(f"{k}={v}" for k, v in tok.items()))


if __name__ == "__main__":
    main()
