"""AppWorld hero trace v2 — pagination silent error (prereg: runs/appworld_hero/prereg_v2.md).

    python scripts/run_appworld_hero.py --step mint      # card from a recorded real failure, no agent run
    python scripts/run_appworld_hero.py --step arms      # recall targets e85d92a_2 / e85d92a_3

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
import appworld_gold  # noqa: E402
from appworld_agent import AppWorldAgent  # noqa: E402
from appworld_client import AppWorldClient  # noqa: E402

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


def mint(client: AppWorldClient, bank: cards_mod.CardBank) -> dict:
    exp, task, steps = MINT_SOURCE
    finished = any("complete_task" in json.loads(line)["code"] for line in steps.read_text(encoding="utf-8").splitlines())
    log = cards_mod.APPWORLD_ROOT / "experiments" / "outputs" / exp / "tasks" / task / "logs" / "api_calls.jsonl"
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    reads = cards_mod.list_reads(calls)
    client.initialize(task, EXPERIMENT + "_verify", timeout_seconds=None)     # fresh world, read-only re-calls
    evidence = cards_mod.verify(client, task, reads, completed=finished)
    client.close(task)
    rec = {"source": f"{exp}/{task}", "agent_finished": finished, "markers": cards_mod.behaviour_markers(reads),
           "verifier_confirmed": evidence, "decision": "skip: verifier found nothing"}
    if evidence:
        card, attempts = cards_mod.distill(evidence)
        rec.update(decision="admitted" if card else "lint failed twice", attempts=attempts,
                   card=bank.admit(card, evidence, task) if card else None)
    (OUT / "mint.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return rec


def run_arm(arm: str, task: str, which: str, thinking: bool, client: AppWorldClient, bank: cards_mod.CardBank) -> dict:
    injected = bank.recall() if which == "recall" else []
    d = OUT / f"{arm}_{task}"
    ep = AppWorldAgent(client, cards=injected, thinking=thinking).run(task, EXPERIMENT, d)
    # ---- measurement only: agent has stopped -------------------------------------------------
    shadow = appworld_gold.evaluate(client, task)
    (d / "shadow_eval.json").write_text(json.dumps(shadow, ensure_ascii=False, indent=1), encoding="utf-8")
    reads = cards_mod.list_reads(cards_mod.read_api_calls(client, task, EXPERIMENT))
    client.close(task)
    rec = {"arm": arm, **ep, "markers": cards_mod.behaviour_markers(reads), "injected_cards": injected,
           "success": shadow.get("success"), "num_failures": len(shadow.get("failures", [])),
           "num_tests": shadow.get("num_tests")}
    (d / "record.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return rec


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--step", required=True, choices=["mint", "arms"])
    args = ap.parse_args()
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
    OUT.mkdir(parents=True, exist_ok=True)
    client, bank = AppWorldClient(), cards_mod.CardBank(OUT / "cards.json")
    if args.step == "mint":
        r = mint(client, bank)
        print(json.dumps({k: r.get(k) for k in ("decision", "card", "verifier_confirmed")}, ensure_ascii=False, indent=1))
        return
    if not bank.recall():
        raise SystemExit("no admitted card — run --step mint first")
    for arm, task, which, thinking in ARMS:
        r = run_arm(arm, task, which, thinking, client, bank)
        print(json.dumps({k: r[k] for k in ("arm", "task_id", "thinking", "status", "done_step", "success",
                                            "prompt_tokens", "completion_tokens")}
                         | {"beyond_page0": r["markers"]["read_beyond_page0"], "sort_by": r["markers"]["used_sort_by"]},
                         ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
