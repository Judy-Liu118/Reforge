"""Held-out test of the frozen verifier ladder (see heldout_trap_cases.py).

    python scripts/run_verifier_heldout.py --replicates 3
    → runs/verifier_heldout.agent.jsonl   agent runs (resumable)
    → runs/verifier_heldout.jsonl         ladder verdicts

Agent runs are cold (no memory, no card). The verifier is applied unchanged
via verifier_apply.py.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replicates", type=int, default=3)
    ap.add_argument("--agent-model", default="qwen3.8-27b")
    ap.add_argument("--cases", default="", help="comma-separated case-id prefixes (smoke)")
    ap.add_argument("--no-critic", action="store_true")
    ap.add_argument("--out", default="runs/verifier_heldout.jsonl")
    args = ap.parse_args()

    from run_distill_ablation import _configure_models, run_one
    _configure_models(args.agent_model)
    os.environ["REFORGE_EXP_DISTILLED_MEMORY"] = "0"      # plain cold runs: no cards read or written

    from heldout_trap_cases import GROUPS
    from verifier_apply import VERIFIER_SHA, apply_ladder

    out = _ROOT / args.out
    agent_path = out.with_suffix(".agent.jsonl")
    cases = [(g, c) for g, cs in GROUPS.items() for c in cs
             if not args.cases or c.id.startswith(tuple(args.cases.split(",")))]
    done = {}
    if agent_path.exists():
        for line in agent_path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if not r["error"]:
                done[(r["replicate"], r["case_id"])] = r

    root = Path(tempfile.mkdtemp(prefix="reforge_heldout_"))
    print(f"agent={os.environ['LLM_MODEL']} verifier_sha={VERIFIER_SHA} isolation={root}", flush=True)
    with open(agent_path, "a", encoding="utf-8") as fh:
        for rep in range(args.replicates):
            for i, (g, c) in enumerate(cases):
                if (rep, c.id) in done:
                    continue
                rec, _ = run_one(c, pair_id=c.id.split("_")[0], role=g, arm="cold", replicate=rep,
                                 order_index=i, run_dir=root / f"r{rep}" / c.id, preload=None, distill=False)
                r = {**asdict(rec), "group": g}
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                fh.flush()
                print(f"  rep{rep} {c.id:28s} {'PASS' if rec.passed else 'FAIL'} printed={rec.printed} "
                      f"gold={rec.gold} {rec.outcome} {rec.error[:120]}", flush=True)
                if not rec.error:
                    done[(rep, c.id)] = r

    data_dirs, requests = {}, {}
    for _g, c in cases:
        data_dirs[c.id] = Path(c.build(str(root / "data" / c.id)))
        requests[c.id] = c.request
    rows = []
    for (rep, cid), r in sorted(done.items()):
        if cid in data_dirs and rep < args.replicates:
            rows.append({**r, "run": "heldout", "wrong": not r["passed"]})
    apply_ladder(rows, data_dirs, requests, out, critic_model=None if args.no_critic else "qwen3.8-max")
    print(f"records={len(rows)} → {out}")


if __name__ == "__main__":
    main()
