"""Loop metrics — a pure projection of committed loop events (no gold here).

Gold-based numbers (shadow accuracy, calibration, miss rate, canary lift) are
computed by the report from shadow.jsonl / CANARY_RESULT, never here.
"""

from __future__ import annotations

from collections import Counter

from reforge.online import review
from reforge.online.card_bank import ACTIVE, EVICTED, QUARANTINED, CardBank
from reforge.online.loop_log import LoopEventLog


def agent_tokens(log: LoopEventLog) -> int:
    """All attempts, committed or not — this is spend, not outcome."""
    return sum(e.payload.get("tokens_prompt", 0) + e.payload.get("tokens_completion", 0)
               for e in log.events("ATTEMPT_FINISHED", committed_only=False))


def _rate(a: int, b: int) -> float | None:
    return round(a / b, 4) if b else None


def summarize(log: LoopEventLog, bank: CardBank) -> dict:
    commits = log.events("TASK_COMMITTED")
    verdicts = log.events("VERDICT")
    recalls = [e for e in log.events("CARD_RECALLED") if not e.payload.get("override")]
    critic = [e for e in verdicts if e.payload.get("critic_called")]
    card_ev = {k: len(log.events(k)) for k in ("CARD_ADMITTED", "CARD_REJECTED", "CARD_OBSERVED",
                                               "CARD_PROMOTED", "CARD_EVICTED")}
    admitted = Counter(e.payload["card"]["status"] for e in log.events("CARD_ADMITTED"))
    cards = bank.cards()
    a1 = [e for e in verdicts if e.payload["attempt"] == 1]
    a2 = [e for e in verdicts if e.payload["attempt"] == 2]
    hit = [e for e in recalls if e.payload.get("card_ids")]
    distill_tok = sum(e.payload.get("distill_tokens", 0) for k in ("CARD_ADMITTED", "CARD_REJECTED", "CARD_OBSERVED")
                      for e in log.events(k))
    return {
        "tasks_committed": len(commits),
        "tasks_by_source": dict(Counter(e.payload.get("source", "") for e in commits)),
        "final_label": dict(Counter(e.payload.get("final_label") for e in commits)),
        "attempts": len(verdicts),
        "attempt1_label": dict(Counter(e.payload["label"] for e in a1)),
        "attempt1_tier": dict(Counter(e.payload["tier"] for e in a1)),
        "retries": len(a2),
        "retry_label": dict(Counter(e.payload["label"] for e in a2)),   # NOT lift evidence: card fit to this task
        "critic_calls": len(critic),
        "critic_call_rate": _rate(len(critic), len(verdicts)),
        "critic_abstain_rate": _rate(sum(e.payload["reason"] == "critic_abstain" for e in critic), len(critic)),
        "critic_uncertain_reasons": dict(Counter(e.payload["reason"] for e in critic
                                                 if e.payload["label"] == "uncertain")),
        "audits": sum(bool(e.payload.get("audited")) for e in verdicts),
        "review_enqueued": len(log.events("REVIEW_ENQUEUED")),
        "review_open": len(review.pending(log)),
        "review_reasons": dict(Counter(e.payload.get("reason") for e in log.events("REVIEW_ENQUEUED"))),
        "cards_admitted_active": admitted.get(ACTIVE, 0),
        "cards_admitted_quarantined": admitted.get(QUARANTINED, 0),
        "quarantine_reasons": dict(Counter(r.split(":")[0] for e in log.events("CARD_ADMITTED")
                                           for r in e.payload.get("reasons", []))),
        "cards_rejected": card_ev["CARD_REJECTED"],
        "cards_observed": card_ev["CARD_OBSERVED"],
        "cards_promoted": card_ev["CARD_PROMOTED"],          # manual only
        "cards_evicted": card_ev["CARD_EVICTED"],
        "bank_active": sum(c.status == ACTIVE for c in cards.values()),
        "bank_quarantined": sum(c.status == QUARANTINED for c in cards.values()),
        "bank_evicted": sum(c.status == EVICTED for c in cards.values()),
        "recall_attempts": len(recalls),
        "recall_hit_rate": _rate(len(hit), len(recalls)),
        "recall_injected_rate": _rate(sum(bool(e.payload.get("injected")) for e in hit), len(recalls)),
        "recall_mean_score": round(sum(s for e in hit for s in e.payload["scores"]) /
                                   max(1, sum(len(e.payload["scores"]) for e in hit)), 4) if hit else None,
        "tokens_agent": agent_tokens(log),
        "tokens_critic": sum(e.payload.get("critic_tokens", 0) for e in critic),
        "tokens_distill": distill_tok,
    }


def markdown(s: dict) -> str:
    rows = "\n".join(f"| {k} | {v} |" for k, v in s.items())
    return ("# Online loop summary\n\n"
            "Gold-free control metrics (committed tasks only). `retry_label` is not lift evidence — "
            "the retry card was distilled from that same task; lift comes from the canary A/B only.\n\n"
            f"| metric | value |\n|---|---|\n{rows}\n")
