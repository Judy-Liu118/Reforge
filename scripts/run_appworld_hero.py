"""AppWorld hero trace — pagination silent error: mint -> recall -> near-miss (prereg: runs/appworld_hero/prereg.md).

    python scripts/run_appworld_hero.py --arm mint | target_none | target_recall | target_nearmiss | all

Order per arm: agent runs and stops -> gold evaluate (shadow, measurement only) ->
request log + read-only verifier (gold-free) -> [mint only] distill + gated admit -> close.
The mint decision reads only the verifier's evidence, never the shadow record.
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

OUT = _ROOT / "runs" / "appworld_hero"
EXPERIMENT = "reforge_hero"
MINT_TASK, TARGET_TASK = "e85d92a_1", "e85d92a_2"
NEARMISS = {"id": "nearmiss", "scope": "appworld",
            "trigger": "The task needs a maximum, minimum, count or 'all' over items returned by a list or search API.",
            "guardrail": "List and search APIs return up to 10 results per call by default, which covers typical "
                         "queries; one call is enough before aggregating."}


def run_arm(arm: str, client: AppWorldClient, bank: cards_mod.CardBank) -> dict:
    task = MINT_TASK if arm == "mint" else TARGET_TASK
    injected = {"mint": [], "target_none": [], "target_recall": bank.recall(), "target_nearmiss": [NEARMISS]}[arm]
    if arm == "target_recall" and not injected:
        raise SystemExit("no card in the bank — run --arm mint first")
    d = OUT / arm
    ep = AppWorldAgent(client, cards=injected).run(task, EXPERIMENT, d)
    # ---- measurement only: agent has stopped -------------------------------------------------
    shadow = appworld_gold.evaluate(client, task)
    (d / "shadow_eval.json").write_text(json.dumps(shadow, ensure_ascii=False, indent=1), encoding="utf-8")
    # ---- gold-free: request log + verifier ----------------------------------------------------
    reads = cards_mod.list_reads(cards_mod.read_api_calls(client, task, EXPERIMENT))
    evidence = cards_mod.verify(client, task, reads, completed=ep["status"] == "completed")
    rec = {"arm": arm, **ep, "markers": cards_mod.behaviour_markers(reads), "verifier_confirmed": evidence,
           "injected_cards": injected, "success": shadow.get("success"),
           "num_failures": len(shadow.get("failures", [])), "num_tests": shadow.get("num_tests")}
    if arm == "mint":
        rec["mint"] = {"decision": "skip: verifier found nothing"}
        if evidence:
            card, attempts = cards_mod.distill(evidence)
            rec["mint"] = {"decision": "admitted" if card else "lint failed twice", "attempts": attempts,
                           "card": bank.admit(card, evidence, task) if card else None}
    client.close(task)
    (d / "record.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return rec


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arm", required=True, choices=["mint", "target_none", "target_recall", "target_nearmiss", "all"])
    args = ap.parse_args()
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
    OUT.mkdir(parents=True, exist_ok=True)
    client, bank = AppWorldClient(), cards_mod.CardBank(OUT / "cards.json")
    arms = ["mint", "target_none", "target_recall", "target_nearmiss"] if args.arm == "all" else [args.arm]
    for arm in arms:
        r = run_arm(arm, client, bank)
        print(json.dumps({k: r[k] for k in ("arm", "status", "steps", "done_step", "success", "prompt_tokens",
                                            "completion_tokens")} | {"markers": r["markers"]}, ensure_ascii=False),
              flush=True)
        if arm == "mint" and r["mint"]["decision"] != "admitted":
            print("mint did not admit a card — stopping (prereg)", r["mint"]["decision"])
            break


if __name__ == "__main__":
    main()
