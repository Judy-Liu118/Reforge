"""AppWorld multi-card + cross-scenario recall (prereg: runs/appworld_multicard/prereg.md).

    python scripts/run_appworld_multicard.py --step bank        # mint L (gold-free), assemble P/L/D1/D2
    python scripts/run_appworld_multicard.py --step preflight   # theta from the D1 null + recall on targets (0 LLM tokens)
    python scripts/run_appworld_multicard.py --step arms        # none / retrieval per target, budget-guarded
    python scripts/run_appworld_multicard.py --step scope       # v2: none / scope-injected conventions (prereg v2)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import appworld_cards as cards_mod  # noqa: E402
import appworld_experience as ae  # noqa: E402
import appworld_gold  # noqa: E402
import appworld_recall  # noqa: E402
from appworld_agent import AppWorldAgent  # noqa: E402
from appworld_client import AppWorldClient  # noqa: E402
from reforge.experience import VerifiedFailure  # noqa: E402

OUT = _ROOT / "runs" / "appworld_multicard"
GATE = _ROOT / "runs" / "appworld_gate"
EXPERIMENT = "reforge_multicard"
TARGETS = ["287e338_1", "82e2fac_2"]
SCOPE_TARGETS = ["07b42fd_2", "c901732_3"]                  # prereg v2: L cross-scenario
K, BATCH_BUDGET, EPISODE_CAP = 2, 120_000, 45_000
LOGIN_SOURCES = [("cf6abd2_2", "simple_note"), ("302c169_2", "phone")]
D1 = {"id": "D1", "role": "distractor", "apps": ["venmo"],
      "trigger": "When sending money to, or requesting money from, a contact on Venmo.",
      "guardrail": "Payment APIs identify the other person by their account email, so look the email up before "
                   "creating the transaction."}
D2 = {"id": "D2", "role": "distractor", "apps": None,
      "trigger": "The task needs a maximum, minimum, count or 'all' over items returned by a list or search API.",
      "guardrail": "List and search APIs return up to 10 results per call by default, which covers typical "
                   "queries; one call is enough before aggregating."}


def _encoder():
    return appworld_recall.Encoder(cache_path=OUT / "emb_cache.jsonl")


def step_bank(client: AppWorldClient) -> None:
    """L mint through the component (L0 probe on recorded steps -> identity replay -> distill -> gate ->
    QUARANTINED in the shared AppWorld bank). bank.json (P / L / distractors D1, D2 as plain dicts) is still
    written for the embedding preflight, which tests retrieval only — distractors never enter the component."""
    loop = ae.make_loop(ae.BANK)
    rec = {"login_sources": []}
    evidence = []
    for task, app in LOGIN_SOURCES:
        steps = ae.read_steps(GATE / task / "steps.jsonl")
        f = ae.login_failure(client, task, app, steps, EXPERIMENT + "_verify")
        rec["login_sources"].append({"task": task, "app": app, "verified": list(f.evidence) if f else None})
        if f:
            evidence += list(f.evidence)
    if not evidence:
        raise SystemExit("no verified login failure — stop (prereg)")
    failure = VerifiedFailure("login_identity", "identity_replay", tuple(evidence),
                              source_task="+".join(t for t, _ in LOGIN_SOURCES))
    res = loop.mint(failure, ae.llm_distiller)
    rec.update(card=res.card.to_dict() if res.card else None, reasons=list(res.reasons))
    (OUT / "mint_login.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    if not res.card:
        raise SystemExit("L card not admitted — stop (prereg)")
    active = {c.kind: c for c in loop.store.by_status("active")}
    p = active.get("pagination")
    bank = ([{"id": "P", "role": "verified", "apps": None, "trigger": p.trigger, "guardrail": p.guardrail}] if p else []) + [
        {"id": "L", "role": "verified", "apps": None, "trigger": res.card.trigger, "guardrail": res.card.guardrail,
         "card_id": res.card.card_id}, D1, D2]
    (OUT / "bank.json").write_text(json.dumps(bank, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(bank, ensure_ascii=False, indent=1))


def _instruction(task: str) -> str:
    """The task text the agent sees (specs.json instruction), not ground truth."""
    spec = cards_mod.APPWORLD_ROOT / "data" / "tasks" / task / "specs.json"
    return json.loads(spec.read_text(encoding="utf-8"))["instruction"]


def step_preflight() -> dict:
    bank = json.loads((OUT / "bank.json").read_text(encoding="utf-8"))
    enc = _encoder()
    train = (cards_mod.APPWORLD_ROOT / "data" / "datasets" / "train.txt").read_text().split()
    null_tasks = [t for t in train if {"spotify"} <= appworld_recall.apps_in(_instruction(t))
                  and "venmo" not in appworld_recall.apps_in(_instruction(t))]
    sims = sorted(r["sim"] for t in null_tasks for r in appworld_recall.score(enc, _instruction(t), [D1]))
    theta = sims[min(len(sims) - 1, int(0.95 * len(sims)))]
    out = {"theta": theta, "null_n": len(sims), "null_min_max": [sims[0], sims[-1]], "k": K, "targets": {}}
    for t in TARGETS:
        chosen, rows = appworld_recall.recall(enc, _instruction(t), bank, theta, K)
        out["targets"][t] = {"instruction": _instruction(t), "apps": sorted(appworld_recall.apps_in(_instruction(t))),
                             "scores": rows, "recalled": [c["id"] for c in chosen],
                             "run": any(c["id"] in ("P", "L") for c in chosen)}
    (OUT / "preflight.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return out


def _login_markers(steps_path: Path) -> dict:
    first, errors = None, 0
    for line in steps_path.read_text(encoding="utf-8").splitlines():
        x = json.loads(line)
        for m in re.finditer(r"apis\.(\w+)\.login\((\w+)=", x["code"]):
            first = first or {"step": x["step"], "app": m.group(1), "param": m.group(2)}
            errors += x["output"].startswith("Execution failed")
    return {"first_login": first, "first_login_username": bool(first and first["param"] == "username"),
            "login_error_steps": errors}


def run_arm(arm: str, task: str, cards: list, client: AppWorldClient, recall_info: dict | None) -> dict:
    d = OUT / f"{arm}_{task}"
    ep = AppWorldAgent(client, cards=cards, token_cap=EPISODE_CAP).run(task, EXPERIMENT, d)
    shadow = appworld_gold.evaluate(client, task)                    # measurement only
    (d / "shadow_eval.json").write_text(json.dumps(shadow, ensure_ascii=False, indent=1), encoding="utf-8")
    reads = cards_mod.list_reads(cards_mod.read_api_calls(client, task, EXPERIMENT))
    client.close(task)
    rec = {"arm": arm, **ep, "recall": recall_info, "injected": [getattr(c, "card_id", None) or c["id"] for c in cards],
           "markers": cards_mod.behaviour_markers(reads) | _login_markers(d / "steps.jsonl"),
           "success": shadow.get("success"), "num_failures": len(shadow.get("failures", [])),
           "num_tests": shadow.get("num_tests")}
    (d / "record.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return rec


def step_arms(client: AppWorldClient) -> None:
    bank = json.loads((OUT / "bank.json").read_text(encoding="utf-8"))
    pre = json.loads((OUT / "preflight.json").read_text(encoding="utf-8"))
    spent = 0
    for t in TARGETS:
        info = pre["targets"][t]
        if not info["run"]:
            print(f"{t}: retrieval recalled neither P nor L — not run (prereg)", flush=True)
            continue
        for arm in ("none", "retrieval"):
            if spent + EPISODE_CAP > BATCH_BUDGET:
                print(f"budget guard: spent {spent}, next episode could exceed {BATCH_BUDGET} — stop", flush=True)
                return
            cards = [c for c in bank if c["id"] in info["recalled"]] if arm == "retrieval" else []
            r = run_arm(arm, t, cards, client, info if arm == "retrieval" else None)
            spent += r["prompt_tokens"] + r["completion_tokens"]
            m = r["markers"]
            print(json.dumps({"arm": arm, "task": t, "injected": r["injected"], "status": r["status"],
                              "done_step": r["done_step"], "success": r["success"],
                              "tokens": r["prompt_tokens"] + r["completion_tokens"], "spent": spent,
                              "beyond_page0": m["read_beyond_page0"], "first_login": m["first_login"],
                              "login_errors": m["login_error_steps"]}, ensure_ascii=False), flush=True)


def step_scope(client: AppWorldClient) -> None:
    """Layer-1 recall through the component: active convention cards whose scope covers the task."""
    loop = ae.make_loop(ae.BANK)
    spent = 0
    for t in SCOPE_TARGETS:
        cards = loop.recall(ae.task(t, _instruction(t), f"scope_{t}"))
        info = {"layer": "scope", "apps": sorted(appworld_recall.apps_in(_instruction(t))),
                "injected": [c.card_id for c in cards]}
        for arm in ("none", "scope"):
            if spent + EPISODE_CAP > BATCH_BUDGET:
                print(f"budget guard: spent {spent}, next episode could exceed {BATCH_BUDGET} — stop", flush=True)
                return
            r = run_arm(arm, t, cards if arm == "scope" else [], client, info if arm == "scope" else None)
            spent += r["prompt_tokens"] + r["completion_tokens"]
            m = r["markers"]
            print(json.dumps({"arm": arm, "task": t, "injected": r["injected"], "status": r["status"],
                              "done_step": r["done_step"], "success": r["success"],
                              "tokens": r["prompt_tokens"] + r["completion_tokens"], "spent": spent,
                              "beyond_page0": m["read_beyond_page0"], "first_login": m["first_login"],
                              "login_errors": m["login_error_steps"]}, ensure_ascii=False), flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--step", required=True, choices=["bank", "preflight", "arms", "scope"])
    args = ap.parse_args()
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
    OUT.mkdir(parents=True, exist_ok=True)
    if args.step == "preflight":
        step_preflight()
        return
    client = AppWorldClient()
    {"bank": step_bank, "arms": step_arms, "scope": step_scope}[args.step](client)


if __name__ == "__main__":
    main()
