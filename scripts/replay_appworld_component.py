"""Zero-token replay: does the component version reproduce the script version's recorded results?

No LLM call anywhere. Needs `appworld serve environment` (local) for the verifiers' read-only
re-calls and login replays — the same gold-free checks the original runs made.

    A  hero mint     recorded Gate-2 failure log -> component probe + verifier -> distill (recorded card text)
                     -> component gate -> QUARANTINED; evidence and card must match the recorded mint
    B  promote       paired canary from the recorded hero no-think arms (offline scoring, gold read here only)
    C  recall        component scope recall on e85d92a_2 / _3 -> rendered system prompt must be byte-identical
                     to the prompt the script version built from the recorded injected cards
    D  behaviour     component probes on every recorded request log == the legacy reader's groups; the
                     recorded per-arm markers + outcomes (4/4) are then re-tabulated
    E  login mint    recorded login failures -> component L0 probe + identity replay -> gate -> QUARANTINED
    F  scope         component recall on the cross-scenario target -> P + L, prompt byte-identical to the
                     recorded scope arm; the unverified distractor cannot enter the store
Writes runs/appworld_experience/replay_report.json (git-ignored).

This file is a report, not a control module: it reads recorded shadow results to tabulate outcomes and
to build the offline canary in B. Nothing it reads flows into an agent.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(_ROOT))

import appworld_cards as ac  # noqa: E402
import appworld_experience as ae  # noqa: E402
from appworld_agent import system_prompt  # noqa: E402
from appworld_baseline import SYSTEM  # noqa: E402
from appworld_client import AppWorldClient  # noqa: E402
from reforge.experience import CanaryReport, VerifiedFailure  # noqa: E402
from reforge.experience import probes  # noqa: E402

RUNS = _ROOT / "runs"
HERO, HERO_V1, MULTI, GATE = RUNS / "appworld_hero" / "v2", RUNS / "appworld_hero", RUNS / "appworld_multicard", RUNS / "appworld_gate"
OUT = RUNS / "appworld_experience"
LOGS = ac.APPWORLD_ROOT / "experiments" / "outputs"

# the pre-component prompt formula, verbatim from scripts/appworld_agent.py @ dfed12b
LEGACY_HEADER = ("\n\nGuardrails learned from earlier verified failures in this environment "
                 "(guidance for your reasoning, not code to copy):")


def legacy_prompt(cards: list[dict]) -> str:
    if not cards:
        return SYSTEM
    return SYSTEM + LEGACY_HEADER + "".join(f"\n- When: {c['trigger']}\n  Then: {c['guardrail']}" for c in cards)


def _j(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def _calls(exp: str, task: str) -> list[dict]:
    p = LOGS / exp / "tasks" / task / "logs" / "api_calls.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def _instruction(task: str) -> str:
    return _j(ac.APPWORLD_ROOT / "data" / "tasks" / task / "specs.json")["instruction"]


def _recorded(card: dict):
    """Distiller replay: hand back the card text the recorded run's distiller produced (no LLM)."""
    return lambda failure: {"trigger": card["trigger"], "guardrail": card["guardrail"]}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    bank_path = OUT / "replay_loop.jsonl"
    bank_path.unlink(missing_ok=True)
    loop, client, rep, ok = ae.make_loop(bank_path), AppWorldClient(), {}, {}

    # ---- A: hero mint -------------------------------------------------------------------------
    rec_mint, rec_card = _j(HERO / "mint.json"), _j(HERO / "cards.json")[0]
    steps = ae.read_steps(GATE / "e85d92a_1" / "steps.jsonl")
    client.initialize("e85d92a_1", "reforge_replay_verify", timeout_seconds=None)
    try:
        pf = ae.pagination_failure(client, "e85d92a_1", _calls("reforge_gate", "e85d92a_1"),
                                   finished=any("complete_task" in s["code"] for s in steps))
    finally:
        client.close("e85d92a_1")
    mres = loop.mint(pf, _recorded(rec_card)) if pf else None
    p_card = mres.card if mres else None
    ok["A_evidence_matches"] = bool(pf) and list(pf.evidence) == rec_mint["verifier_confirmed"]
    ok["A_card_matches"] = bool(p_card) and (p_card.trigger, p_card.guardrail) == (rec_card["trigger"], rec_card["guardrail"])
    ok["A_quarantined"] = bool(p_card) and p_card.status == "quarantined"
    rep["A"] = {"evidence": list(pf.evidence) if pf else None, "card_id": p_card.card_id if p_card else None,
                "gate_reasons": list(mres.reasons) if mres else None}

    # quarantine is not recalled
    ok["A_quarantine_not_recalled"] = loop.recall(ae.task("e85d92a_2", _instruction("e85d92a_2"))) == []

    # ---- B: promote on the recorded paired canary (offline scoring) --------------------------------
    control = [_j(HERO_V1 / "target_none" / "shadow_eval.json"), _j(HERO / "none_e85d92a_3" / "shadow_eval.json")]
    treated = [_j(HERO / "recall_e85d92a_2" / "shadow_eval.json"), _j(HERO / "recall_e85d92a_3" / "shadow_eval.json")]
    canary = CanaryReport(p_card.card_id, n=2, control_success=sum(bool(x.get("success")) for x in control),
                          treated_success=sum(bool(x.get("success")) for x in treated),
                          regressions=tuple(t for t, c, x in zip(("e85d92a_2", "e85d92a_3"), control, treated)
                                            if c.get("success") and not x.get("success")))
    ok["B_promoted"] = loop.promote(p_card.card_id, canary).status == "active"
    rep["B"] = canary.__dict__

    # ---- C: recall + byte-identical prompt ---------------------------------------------------------
    rep["C"] = {}
    for arm in ("recall_e85d92a_2", "recall_e85d92a_3", "recall_think_e85d92a_2", "recall_think_e85d92a_3"):
        task = arm.rsplit("_", 2)[-2] + "_" + arm.rsplit("_", 1)[-1]
        got = loop.recall(ae.task(task, _instruction(task), f"replay:{arm}"))
        recorded_cards = _j(HERO / arm / "record.json")["injected_cards"]
        same = system_prompt(got) == legacy_prompt(recorded_cards)
        rep["C"][arm] = {"recalled": [c.card_id for c in got], "prompt_identical": same}
        ok[f"C_prompt_identical_{arm}"] = same

    # ---- D: behaviour markers -----------------------------------------------------------------------
    eps, legacy_eps = ae.endpoints(), ac.paginated_endpoints()
    equal, compared = True, 0
    for exp_dir in sorted(p for p in LOGS.iterdir() if p.is_dir()):
        for tdir in sorted((exp_dir / "tasks").glob("*")):
            calls = _calls(exp_dir.name, tdir.name)
            if not calls:
                continue
            new = sorted((r.app, r.api, json.dumps(r.params, sort_keys=True), r.pages, r.limit)
                         for r in probes.paginated_reads(calls, eps))
            old = sorted((r["app"], r["api"], json.dumps(r["params"], sort_keys=True), tuple(r["pages"]), max(r["limits"]))
                         for r in ac.list_reads(calls, legacy_eps))
            equal &= new == old
            compared += 1
    ok["D_probe_equivalent_on_all_logs"] = equal
    table = []
    arms = [("none (v1)", HERO_V1 / "target_none"), ("nearmiss (v1)", HERO_V1 / "target_nearmiss")] + \
           [(p.name, p) for p in sorted(HERO.iterdir()) if (p / "record.json").exists()]
    for name, d in arms:
        r, sh = _j(d / "record.json"), _j(d / "shadow_eval.json")
        table.append({"arm": name, "task": r["task_id"], "thinking": r.get("thinking", False),
                      "read_beyond_page0": r["markers"]["read_beyond_page0"], "success": bool(sh.get("success"))})
    # last arm per task still has its live log: recompute its marker with the component and compare
    for name, exp, task in (("recall_think_e85d92a_2", "reforge_hero_v2", "e85d92a_2"),
                            ("recall_think_e85d92a_3", "reforge_hero_v2", "e85d92a_3")):
        live = any(max(r.pages) >= 1 for r in probes.paginated_reads(_calls(exp, task), eps))
        ok[f"D_marker_recomputed_{name}"] = live == _j(HERO / name / "record.json")["markers"]["read_beyond_page0"]
    card_runs = [t for t in table if t["arm"].startswith("recall")]
    rep["D"] = {"logs_compared": compared, "arms": table,
                "card_arms_fixed": f"{sum(t['success'] and t['read_beyond_page0'] for t in card_runs)}/{len(card_runs)}",
                "no_card_correct": f"{sum(t['success'] for t in table if t['arm'].startswith('none'))}/"
                                   f"{sum(1 for t in table if t['arm'].startswith('none'))}"}

    # ---- E: login mint --------------------------------------------------------------------------------
    rec_login = _j(MULTI / "mint_login.json")
    evidence = []
    for task, app in (("cf6abd2_2", "simple_note"), ("302c169_2", "phone")):
        f = ae.login_failure(client, task, app, ae.read_steps(GATE / task / "steps.jsonl"), "reforge_replay_verify")
        evidence += list(f.evidence) if f else []
    recorded_ev = [s["verified"] for s in rec_login["login_sources"]]
    ok["E_evidence_matches"] = evidence == recorded_ev
    lres = loop.mint(VerifiedFailure("login_identity", "identity_replay", tuple(evidence), source_task="cf6abd2_2+302c169_2"),
                     _recorded(rec_login["card"]))
    l_card = lres.card
    ok["E_quarantined"] = bool(l_card) and l_card.status == "quarantined"
    none_sh, scope_sh = _j(MULTI / "none_07b42fd_2" / "shadow_eval.json"), _j(MULTI / "scope_07b42fd_2" / "shadow_eval.json")
    l_canary = CanaryReport(l_card.card_id, n=1, control_success=int(bool(none_sh.get("success"))),
                            treated_success=int(bool(scope_sh.get("success"))),
                            regressions=("07b42fd_2",) if none_sh.get("success") and not scope_sh.get("success") else ())
    ok["E_promoted"] = loop.promote(l_card.card_id, l_canary).status == "active"
    rep["E"] = {"evidence": evidence, "card_id": l_card.card_id, "canary": l_canary.__dict__}

    # ---- F: scope recall on the cross-scenario target ---------------------------------------------------
    got = loop.recall(ae.task("07b42fd_2", _instruction("07b42fd_2"), "replay:scope_07b42fd_2"))
    bank = {c["id"]: c for c in _j(MULTI / "bank.json")}
    recorded_ids = _j(MULTI / "scope_07b42fd_2" / "record.json")["injected"]
    ok["F_recalls_P_and_L"] = [c.kind for c in got] == ["pagination", "login_identity"]
    ok["F_prompt_identical"] = system_prompt(got) == legacy_prompt([bank[i] for i in recorded_ids])
    try:
        VerifiedFailure("distractor", "none", (), source_task="D1")
        ok["F_distractor_refused"] = False
    except ValueError:
        ok["F_distractor_refused"] = True
    login_errs = {arm: len(probes.login_failures(ae.read_steps(MULTI / f"{arm}_07b42fd_2" / "steps.jsonl")))
                  for arm in ("none", "scope")}
    ok["F_login_marker_matches"] = all(
        login_errs[a] == _j(MULTI / f"{a}_07b42fd_2" / "record.json")["markers"]["login_error_steps"] for a in login_errs)
    rep["F"] = {"recalled": [c.card_id for c in got], "login_error_steps": login_errs}

    rep["checks"] = ok
    rep["all_ok"] = all(ok.values())
    (OUT / "replay_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=list), encoding="utf-8")
    print(json.dumps({"checks": ok, "all_ok": rep["all_ok"], "D": {k: rep["D"][k] for k in ("logs_compared",
                      "card_arms_fixed", "no_card_correct")}}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
