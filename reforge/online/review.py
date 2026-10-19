"""Review queue — a projection of REVIEW_ENQUEUED minus REVIEW_RESOLVED.

Items come from the loop (uncertain verdicts, re-observed cards). A human
resolves them through scripts/online_cards.py; the decision is logged, never
acted on silently. Promoting or evicting a card resolves its open
card_reobserved items with that decision.
"""

from __future__ import annotations

from reforge.online.card_bank import Card, CardBank
from reforge.online.loop_log import LoopEvent, LoopEventLog


def resolved_ids(log: LoopEventLog) -> set[str]:
    return {e.payload["review_id"] for e in log.events("REVIEW_RESOLVED")}


def pending(log: LoopEventLog) -> list[LoopEvent]:
    done = resolved_ids(log)
    return [e for e in log.events("REVIEW_ENQUEUED") if e.event_id not in done]


def resolve(log: LoopEventLog, review_id: str, decision: str, note: str = "") -> LoopEvent:
    if review_id not in {e.event_id for e in log.events("REVIEW_ENQUEUED")}:
        raise KeyError(f"no committed review item {review_id!r}")
    if review_id in resolved_ids(log):
        raise ValueError(f"review item {review_id} already resolved")
    return log.append("REVIEW_RESOLVED", review_id=review_id, decision=decision, note=note)


def _close_card_items(log: LoopEventLog, card_id: str, decision: str, note: str) -> list[str]:
    ids = [e.event_id for e in pending(log) if e.payload.get("card_id") == card_id]
    for i in ids:
        resolve(log, i, decision, note)
    return ids


def promote_card(bank: CardBank, card_id: str, reason: str) -> tuple[Card, list[str]]:
    c = bank.promote(card_id, reason=f"manual: {reason}")
    return c, _close_card_items(bank.log, card_id, "promote", reason)


def evict_card(bank: CardBank, card_id: str, reason: str) -> tuple[Card, list[str]]:
    c = bank.evict(card_id, reason)
    return c, _close_card_items(bank.log, card_id, "evict", reason)
