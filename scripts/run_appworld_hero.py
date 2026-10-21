"""AppWorld hero trace v2 — pagination silent error (prereg: runs/appworld_hero/prereg_v2.md).

    python scripts/run_appworld_hero.py --step mint      # card from a recorded real failure, no agent run
    python scripts/run_appworld_hero.py --step promote --card <id> --canary n,ctl,trt
    python scripts/run_appworld_hero.py --step arms      # recall targets e85d92a_2 / e85d92a_3

Since 2026-10-21 minting, the gate, quarantine/promote and recall go through reforge.experience
(scripts/appworld_experience.py). The recorded v2 runs were made with the pre-component bank;
scripts/replay_appworld_component.py checks the component reproduces them.

Mint is verifier-gated and gold-free: the source episode's own request log ->
read-only re-calls in a fresh world -> confirmed incomplete read -> distill -> admit.
Per arm: agent runs and stops -> gold evaluate (shadow only) -> request log markers -> close.
v1 (mint on a fresh e85d92a_1 run, loose card) is in git history and runs/appworld_hero/{mint,target_*}.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import appworld_cards as cards_mod  # noqa: E402
import appworld_experience as ae  # noqa: E402
import appworld_gold  # noqa: E402
from appworld_agent import AppWorldAgent  # noqa: E402
from appworld_client import AppWorldClient  # noqa: E402
from reforge.experience import CanaryReport  # noqa: E402

OUT = _ROOT / "runs" / "appworld_hero" / "v2"
EXPERIMENT = "reforge_hero_v2"
MINT_SOURCE = ("reforge_gate", "e85d92a_1", _ROOT / "runs" / "appworld_gate" / "e85d92a_1" / "steps.jsonl")
NEARMISS = {"id": "nearmiss", "scope": "appworld",
            "trigger": "The task needs a maximum, minimum, count or 'all' over items returned by a list or search API.",
            "guardrail": "List and search APIs return up to 10 results per call by default, which covers typical "
                         "queries; one call is enough before aggregating."}
# (arm, task, cards: none | recall, thinking); e85d92a_2 none / nearmiss reuse v1 (same condition, prereg)
ARMS = [("recall", "e85d92a_2", "recall", False), ("none", "e85d92a_3", "none", False),
        ("recall", "e85d92a_3", "recall", False), ("recall_think", "e85d92a_2", "recall", True),
        ("recall_think", "e85d92a_3", "recall", True)]


def _instruction(task: str) -> str:
    spec = cards_mod.APPWORLD_ROOT / "data" / "tasks" / task / "specs.json"
    return json.loads(spec.read_text(encoding="utf-8"))["instruction"]


def mint(client: AppWorldClient, loop) -> dict:
    """Component path: recorded request log -> probe + verifier (fresh world) -> distill -> gate -> QUARANTINED."""
    exp, task, steps = MINT_SOURCE
    finished = any("complete_task" in s["code"] for s in ae.read_steps(steps))
    log = cards_mod.APPWORLD_ROOT / "experiments" / "outputs" / exp / "tasks" / task / "logs" / "api_calls.jsonl"
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    client.initialize(task, EXPERIMENT + "_verify", timeout_seconds=None)     # fresh world, read-only re-calls
    try:
        failure = ae.pagination_failure(client, task, calls, finished=finished, source_run=f"{exp}/{task}")
    finally:
        client.close(task)
    rec = {"source": f"{exp}/{task}", "agent_finished": finished,
           "verifier_confirmed": list(failure.evidence) if failure else [], "decision": "skip: verifier found nothing"}
    if failure:
        res = loop.mint(failure, ae.llm_distiller)
        rec.update(decision="quarantined" if res.card else "gate failed", reasons=list(res.reasons),
                   card=res.card.to_dict() if res.card else None)
    (OUT / "mint.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return rec


def run_arm(arm: str, task: str, which: str, thinking: bool, client: AppWorldClient, loop) -> dict:
    injected = loop.recall(ae.task(task, _instruction(task), f"{arm}_{task}")) if which == "recall" else []
    d = OUT / f"{arm}_{task}"
    ep = AppWorldAgent(client, cards=injected, thinking=thinking).run(task, EXPERIMENT, d)
    # ---- measurement only: agent has stopped -------------------------------------------------
    shadow = appworld_gold.evaluate(client, task)
    (d / "shadow_eval.json").write_text(json.dumps(shadow, ensure_ascii=False, indent=1), encoding="utf-8")
    reads = cards_mod.list_reads(cards_mod.read_api_calls(client, task, EXPERIMENT))
    client.close(task)
    rec = {"arm": arm, **ep, "markers": cards_mod.behaviour_markers(reads),
           "injected_cards": [c.to_dict() for c in injected],
           "success": shadow.get("success"), "num_failures": len(shadow.get("failures", [])),
           "num_tests": shadow.get("num_tests")}
    (d / "record.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return rec


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--step", required=True, choices=["mint", "promote", "arms"])
    ap.add_argument("--card", help="promote: card id")
    ap.add_argument("--canary", help="promote: n,control_success,treated_success (paired canary, scored offline)")
    args = ap.parse_args()
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
    OUT.mkdir(parents=True, exist_ok=True)
    client, loop = AppWorldClient(), ae.make_loop(ae.BANK)
    if args.step == "mint":
        r = mint(client, loop)
        print(json.dumps({k: r.get(k) for k in ("decision", "card", "verifier_confirmed")}, ensure_ascii=False, indent=1))
        return
    if args.step == "promote":
        n, ctl, trt = (int(x) for x in args.canary.split(","))
        print(loop.promote(args.card, CanaryReport(args.card, n, ctl, trt)).status)
        return
    if not loop.store.by_status("active"):
        raise SystemExit("no active card — run --step mint, canary it, then --step promote")
    for arm, task, which, thinking in ARMS:
        r = run_arm(arm, task, which, thinking, client, loop)
        print(json.dumps({k: r[k] for k in ("arm", "task_id", "thinking", "status", "done_step", "success",
                                            "prompt_tokens", "completion_tokens")}
                         | {"beyond_page0": r["markers"]["read_beyond_page0"], "sort_by": r["markers"]["used_sort_by"]},
                         ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
