"""Report for run_bird_ceiling.py — per-arm accuracy, paired differences, per-db, go/no-go.

    python scripts/report_bird_ceiling.py      # → runs/bird_ceiling/report.md
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from reforge.online.canary import bootstrap_paired, ci_agree, newcombe_paired, wilson  # noqa: E402

ARMS = ("none", "given", "recalled")


def _ci(t):
    return f"[{t[0]:+.2f}, {t[1]:+.2f}]" if t else "—"


def paired(rows_by_q: dict, x: str, y: str) -> dict:
    """Δ = acc(x) − acc(y) over questions where both arms were graded."""
    a = b = c = d = 0
    for arms in rows_by_q.values():
        if arms.get(x) is None or arms.get(y) is None:
            continue
        p, q = arms[x], arms[y]
        a += p and q
        b += p and not q
        c += q and not p
        d += not p and not q
    n = a + b + c + d
    nc = newcombe_paired(a, b, c, d)
    bs = bootstrap_paired(a, b, c, d)
    return {"n": n, "x_only": b, "y_only": c, "delta": nc[0] if nc else None, "newcombe": nc[1] if nc else None,
            "bootstrap": bs, "agree": ci_agree(nc[1] if nc else None, bs)}


def build(root: Path) -> str:
    cfg = json.loads((root / "run_config.json").read_text(encoding="utf-8"))
    plan = {p["case_id"]: p for p in json.loads((root / "plan.json").read_text(encoding="utf-8"))}
    latest = {}
    for line in (root / "records.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if "answer_chars" not in r and len(r["final_answer"]) >= 2000:
                continue                      # graded on a truncated prefix (fixed harness bug), superseded
            latest[(r["case_id"], r["arm"])] = r
    by_q: dict[str, dict] = defaultdict(dict)
    for (q, arm), r in latest.items():
        by_q[q][arm] = r["correct"]
    full = {q: v for q, v in by_q.items() if all(v.get(a) is not None for a in ARMS)}
    n = len(full)
    acc = {a: sum(v[a] for v in full.values()) for a in ARMS}
    rc_none, gv_none, gv_rc = paired(full, "recalled", "none"), paired(full, "given", "none"), \
        paired(full, "given", "recalled")
    go = bool(rc_none["delta"] is not None and rc_none["delta"] >= 0.10 and rc_none["newcombe"][0] > 0)
    tok = defaultdict(int)
    for r in latest.values():
        tok[r["arm"]] += r.get("tokens", 0)

    out = [f"# BIRD library-fact memory — phase 0 ceiling (`{root.name}`)\n",
           f"agent `{cfg['agent_model']}` (thinking ≤ {cfg['thinking_budget']}) · embedding `{cfg['embedding']}` · "
           f"{cfg['per_db']} questions × {len(cfg['databases'])} dbs, seed {cfg['seed']}, recall top-{cfg['top_k']} · "
           f"one run per (question, arm)\n",
           f"Pre-registered go rule: {cfg['go_rule']}.\n",
           f"## Verdict: **{'GO' if go else 'NO-GO'}**\n",
           f"recalled − none = {rc_none['delta']:+.3f} {_ci(rc_none['newcombe'])} (Newcombe), bootstrap "
           f"{_ci(rc_none['bootstrap'])}, agree={rc_none['agree']}; n = {n} paired questions.\n",
           "## Accuracy per arm\n", "| arm | correct | accuracy [Wilson 95%] | agent tokens |", "|---|---|---|---|"]
    for a in ARMS:
        w = wilson(acc[a], n)
        out.append(f"| {a} | {acc[a]}/{n} | {acc[a] / n:.2f} [{w[0]:.2f}, {w[1]:.2f}] | {tok[a]:,} |")
    out += ["\n## Paired differences\n", "| Δ | value | Newcombe 95% | bootstrap 95% | agree | discordant (x-only / y-only) |",
            "|---|---|---|---|---|---|"]
    for label, p in (("recalled − none (fact reuse)", rc_none), ("given − none (upper bound)", gv_none),
                     ("given − recalled (gap to the real hint)", gv_rc)):
        out.append(f"| {label} | {p['delta']:+.3f} | {_ci(p['newcombe'])} | {_ci(p['bootstrap'])} | {p['agree']} | "
                   f"{p['x_only']} / {p['y_only']} |")
    if gv_none["delta"]:
        out.append(f"\nShare of the evidence gain recovered by recalled facts: "
                   f"{rc_none['delta'] / gv_none['delta']:.0%} (descriptive; n is small).")

    out += ["\n## Per database (descriptive — 5 questions each, no CI)\n",
            "| db | none | given | recalled | recalled − none | own clause in top-5 |", "|---|---|---|---|---|---|"]
    for db in cfg["databases"]:
        qs = [q for q in full if plan[q]["db"] == db]
        s = {a: sum(full[q][a] for q in qs) for a in ARMS}
        own = sum(plan[q]["own_clause_recalled"] > 0 for q in qs)
        out.append(f"| {db} | {s['none']}/{len(qs)} | {s['given']}/{len(qs)} | {s['recalled']}/{len(qs)} | "
                   f"{s['recalled'] - s['none']:+d} | {own}/{len(qs)} |")

    out += ["\n## Questions where recalled facts flipped the outcome\n"]
    for q, v in sorted(full.items()):
        if v["recalled"] != v["none"]:
            p = plan[q]
            out.append(f"- `{q}` none={v['none']} recalled={v['recalled']} given={v['given']} — {p['question']}\n"
                       f"  - evidence: {p['evidence']}\n  - recalled: " +
                       "; ".join(f"{f} ({s})" for f, s in p["facts"][:3]))
    out += ["\n## Caveats\n",
            "- One run per (question, arm): LLM sampling noise is inside every cell; pairing only removes "
            "question difficulty.",
            f"- n = {n}: a 95% CI on a paired difference this size spans roughly ±0.15–0.25.",
            "- 'recalled' is an **upper bound for variant B**: its facts are BIRD's human-written evidence "
            "from other questions. B must mint facts from its own failures (value probe / critic), which "
            "will be fewer and noisier.",
            "- Databases were chosen for high same-db evidence reuse; the other 5 dev databases reuse less.",
            "- Gold SQL is executed only by the grader, after each run; it never reaches a prompt."]
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="bird_ceiling")
    args = ap.parse_args()
    root = _ROOT / "runs" / args.name
    text = build(root)
    (root / "report.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
