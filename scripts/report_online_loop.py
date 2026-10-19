"""Report for one online-loop run: control metrics, shadow accuracy, calibration, cards, canary A/B.

Reads runs/online/<name>/{loop_events,shadow,canary}.jsonl and writes report.md
next to them. This is the measurement side: it may read gold (via shadow rows);
nothing it computes feeds back into the loop.

    python scripts/report_online_loop.py --name smoke
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from reforge.online import metrics  # noqa: E402
from reforge.online.canary import wilson  # noqa: E402
from reforge.online.card_bank import CardBank  # noqa: E402
from reforge.online.decision import TAU  # noqa: E402
from reforge.online.loop_log import LoopEventLog  # noqa: E402
from reforge.online.shadow import ShadowLog, calibration, calibration_by_tier, calibration_pairs  # noqa: E402

CAVEATS = f"""\
- **τ = {TAU} is pre-registered, uncalibrated and provisional.** The calibration tables below are the
  first evidence for or against it; if critic self-confidence does not track correctness, switch to
  `--critic-k 3` (consistency confidence).
- **L1b's 0.95 holds only on the trap corpus it was measured on.** On natural data a "repair" can change
  the question's meaning (smoke: da0024, a real duplicate row that gold counts). Cheap-tier-only failures
  therefore mint quarantined cards only.
- **"unverified" is not success.** The cheap tiers see only the contaminants they were written for (0%
  recall on InfiAgent's natural errors in the earlier metamorphic run); their pass is no evidence.
- **Retry success is not lift.** The retry card was distilled from that same task. Lift is reported from
  the canary A/B only, and with n = 12 it can only detect large changes.
- Canary lift CI: Newcombe (1998) method 10 with the corrected φ, Wilson bounds per arm; harness errors are
  excluded from `n_pairs`. Cross-checked by a paired percentile bootstrap on the same pairs (B = 10000,
  seed 0; "agree" = same zero-inclusion and both endpoints within 0.10, fixed before the run). With no
  discordant pairs the bootstrap collapses to [0, 0] and is reported as disagreeing — that is the
  bootstrap's known small-n failure, not evidence of certainty.
- **Recall rule:** see the pre-registration section (and its history, if superseded). Cards that share
  neither a signal nor a slot with a task are never recalled cross-task — see "Recall coverage" below.
"""


def _pct(k, n):
    return f"{k}/{n} ({k / n:.0%})" if n else "—"


def _ci(t):
    return f"[{t[0]:.2f}, {t[1]:.2f}]" if t else "—"


def build(root: Path) -> str:
    log = LoopEventLog(root / "loop_events.jsonl")
    bank = CardBank(log)
    s = metrics.summarize(log, bank)
    rows = ShadowLog(root / "shadow.jsonl").rows()
    cfg = json.loads((root / "run_config.json").read_text(encoding="utf-8")) if (root / "run_config.json").exists() else {}
    out = [f"# Online loop report — `{root.name}`\n",
           f"agent `{cfg.get('agent_model')}` · critic `{cfg.get('critic_model')}` k={cfg.get('critic_k')} · "
           f"verifier_sha `{cfg.get('verifier_sha')}` · audit `{cfg.get('audit')}`\n",
           "## Caveats\n", CAVEATS]
    pre = cfg.get("preregistration")
    if pre:
        out += [f"## Pre-registration v{pre.get('version', 1)}\n",
                f"- recall `{pre.get('recall_rule')}`: {pre.get('recall_rule_text', '')}",
                f"- known blind spot: {pre.get('recall_known_blind_spot', '')}"]
        for h in pre.get("history", []):
            out.append(f"- supersedes v{h['version']} (run `{h['run']}`, recall `{h['block'].get('recall_rule')}`): "
                       f"{h.get('superseded_because', '')}")
        out.append("")
    if (root / "notes.md").exists():
        out += ["## Findings (written by hand, from this run's logs)\n",
                (root / "notes.md").read_text(encoding="utf-8"), ""]

    out += ["## Control metrics (gold-free)\n", "| metric | value |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in s.items()]

    out += ["\n## Shadow: decisions vs gold\n",
            "Attempt-1 rows; `label_correct` is only defined for pass/fail claims.\n",
            "| source | n | agent right | claims (pass/fail) | claims right | unverified | unverified actually wrong (miss) | uncertain |",
            "|---|---|---|---|---|---|---|---|"]
    by_src = defaultdict(list)
    for r in rows:
        if r["attempt"] == 1:
            by_src[r["source"]].append(r)
    for src, rs in sorted(by_src.items()):
        claims = [r for r in rs if r["label_correct"] is not None]
        unv = [r for r in rs if r["label"] == "unverified"]
        out.append(f"| {src} | {len(rs)} | {_pct(sum(bool(r['answer_correct']) for r in rs), len(rs))} | "
                   f"{len(claims)} | {_pct(sum(r['label_correct'] for r in claims), len(claims))} | {len(unv)} | "
                   f"{_pct(sum(r['answer_correct'] is False for r in unv), len(unv))} | "
                   f"{sum(r['label'] == 'uncertain' for r in rs)} |")
    aud = [r for r in rows if r.get("audited")]
    if aud:
        out.append(f"\nAudited cheap-tier passes (all attempts): {len(aud)}; agent actually wrong in "
                   f"{_pct(sum(r['answer_correct'] is False for r in aud), len(aud))}; critic flagged "
                   f"{sum(r['critic_verdict'] == 'wrong' for r in aud)}, critic call right "
                   f"{_pct(sum(bool(r['critic_verdict_correct']) for r in aud if r['critic_verdict_correct'] is not None), sum(r['critic_verdict_correct'] is not None for r in aud))}.")

    out += ["\n## Calibration\n", "Per deciding tier — assigned confidence vs how often the pass/fail claim matched gold:\n",
            "| tier | n | mean assigned conf | accuracy |", "|---|---|---|---|"]
    for t, b in sorted(calibration_by_tier(rows).items()):
        out.append(f"| {t} | {b['n']} | {b['mean_conf']} | {b['accuracy']} |")
    out += ["\nCritic self-confidence (all critic calls with a wrong/ok call, incl. those below τ):\n",
            "| conf bin | n | mean conf | accuracy |", "|---|---|---|---|"]
    for b in calibration(rows):
        out.append(f"| [{b['lo']:.2f}, {b['hi']:.2f}) | {b['n']} | {b['mean_conf'] and round(b['mean_conf'], 3)} | "
                   f"{b['accuracy'] and round(b['accuracy'], 3)} |")

    out += ["\nAll tiers — (self-reported conf, claim right vs gold) pairs per bin; `cheap_pass` is a cheap-tier "
            "pass read as claim *pass* at its 0.4:\n", "| tier | conf bin | n | mean conf | accuracy |",
            "|---|---|---|---|---|"]
    for t, bins in calibration_pairs(rows).items():
        for b in bins:
            if b["n"]:
                out.append(f"| {t} | [{b['lo']:.2f}, {b['hi']:.2f}) | {b['n']} | {b['mean_conf']} | {b['accuracy']} |")

    out += ["\n## Recall coverage (known blind spot)\n",
            "Trap stream tasks, attempt 1: how many had **no** L1 signal detected — under the pre-registered rule a "
            "card from such a task is never recalled cross-task (it can still drive its own retry / be promoted).\n",
            "| trap kind | tasks | no signal detected |", "|---|---|---|"]
    kinds: dict[str, list] = defaultdict(list)
    for e in log.events("VERDICT"):
        if e.task_id.startswith("trap:") and e.payload.get("attempt", 1) == 1:
            kinds[e.task_id.split(":", 1)[1].split("__")[0]].append(not e.payload.get("signals"))
    for k, v in sorted(kinds.items()):
        out.append(f"| {k} | {len(v)} | {sum(v)} |")

    out += ["\n## Cards\n", "| card | status | minted by | source task | title | observers | used by (tasks) |",
            "|---|---|---|---|---|---|---|"]
    for c in bank.cards().values():
        used = sorted({u["task_id"] for u in bank.used_by(c.card_id)})
        out.append(f"| `{c.card_id}` | {c.status} | {c.verifier_tier} | {c.source_task_id} | {c.body.get('title', '')} | "
                   f"{len(c.observers)} | {', '.join(used) or '—'} |")
    for e in log.events("CARD_EVICTED"):
        out.append(f"\nEvicted `{e.payload['card_id']}` — {e.payload.get('reason', '')}; had been handed to: "
                   f"{sorted({u['task_id'] for u in e.payload.get('used_by', [])}) or '—'}")

    res = sorted((e.payload for e in log.events("CANARY_RESULT")), key=lambda p: p["checkpoint"])
    out += ["\n## Canary A/B (held-out, gold-scored)\n",
            "| checkpoint (tasks committed) | bank active | n pairs | on with cards | off pass [Wilson] | on pass [Wilson] | on-only / off-only | lift [Newcombe] | bootstrap CI | agree |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for p in res:
        n = p["n_pairs"]
        out.append(f"| {p['checkpoint']} | {p['bank_active']} | {n} | {p['n_on_with_cards']} | "
                   f"{_pct(p['off_pass'], n)} {_ci(wilson(p['off_pass'], n))} | {_pct(p['on_pass'], n)} {_ci(wilson(p['on_pass'], n))} | "
                   f"{p['on_only']} / {p['off_only']} | {p['lift']} {_ci(p['lift_ci'])} | {_ci(p.get('lift_ci_boot'))} | "
                   f"{p.get('ci_agree', '—')} |")
    if not res:
        out.append("| — | | | | | | | | | |")
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    args = ap.parse_args()
    root = _ROOT / "runs" / "online" / args.name
    text = build(root)
    (root / "report.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
