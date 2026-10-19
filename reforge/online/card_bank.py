"""CardBank — strategy cards as a projection of the loop event log.

Nothing is stored outside the log: card state is replayed from CARD_* events
of committed tries, so resume, rollback and "which runs used this card" are
all just reads of the log.

Lifecycle (minting rule, 2026-10-19):
    admitted active        ONLY when the critic confirmed the failure with
                           conf >= τ (below BORDER stays quarantined) and the
                           gate passed
    admitted quarantined   everything else that is worth keeping: failures
                           seen only by the cheap tiers (L0 / L1b — on natural
                           data L1b's "repair" can change the question's
                           meaning, see da0024 in the smoke run), gate soft
                           fails, borderline critic confidence. Injected only
                           into its own source task's retry, never elsewhere.
    promoted               quarantined → active by a manual promote only.
                           Re-observation (another task distils a near-
                           duplicate) just accumulates `observers` and queues
                           the card for review: N tasks hitting the same
                           cheap-tier bias would look like N independent
                           confirmations, and nothing here can tell them apart.
    evicted                terminal; a later near-duplicate is rejected
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace

from reforge.memory.substrate import _char_ngrams, _cosine
from reforge.online.loop_log import LoopEventLog

ACTIVE, QUARANTINED, EVICTED = "active", "quarantined", "evicted"


def provenance_hash(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update((p or "").encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


@dataclass(frozen=True)
class Card:
    card_id: str
    body: dict                       # title / description / content / polarity
    source_task_id: str
    source_run_id: str
    source_request: str              # recall key, as recall_distilled used it
    created_at: str
    verifier_tier: str
    critic_confidence: float | None
    signals: tuple[str, ...]         # applicability: data signals of the source task
    provenance: str
    status: str = ACTIVE
    observers: tuple[str, ...] = ()  # distinct task ids that produced this card
    history: tuple[dict, ...] = field(default_factory=tuple)

    def to_memory_record(self) -> dict:
        """The MemoryRecord shape PlannerMemoryContext reads via recall_distilled."""
        return {"memory_id": self.card_id, "memory_type": "FAILURE" if self.body.get("polarity") != "success"
                else "SUCCESS_PATTERN", "user_request": self.source_request, "distilled_strategy": self.body}


class CardBank:
    def __init__(self, log: LoopEventLog) -> None:
        self.log = log

    # -- projection ----------------------------------------------------------

    def cards(self, *, pending_try: str | None = None) -> dict[str, Card]:
        out: dict[str, Card] = {}
        for e in self.log.events(pending_try=pending_try):
            p = e.payload
            if e.kind == "CARD_ADMITTED":
                c = p["card"]
                out[c["card_id"]] = Card(**{**c, "signals": tuple(c["signals"]), "observers": tuple(c["observers"]),
                                            "history": ({"event": "admitted", "status": c["status"],
                                                         "reasons": p.get("reasons", []), "ts": e.timestamp},)})
            elif e.kind in ("CARD_OBSERVED", "CARD_PROMOTED", "CARD_EVICTED") and p.get("card_id") in out:
                c = out[p["card_id"]]
                if e.kind == "CARD_OBSERVED":
                    obs = c.observers if p["observer_task_id"] in c.observers else c.observers + (p["observer_task_id"],)
                    out[c.card_id] = replace(c, observers=obs)
                    continue
                status = ACTIVE if e.kind == "CARD_PROMOTED" else EVICTED
                out[c.card_id] = replace(c, status=status, history=c.history + (
                    {"event": e.kind.removeprefix("CARD_").lower(), "reason": p.get("reason", ""), "ts": e.timestamp},))
        return out

    def get(self, card_id: str, *, pending_try: str | None = None) -> Card | None:
        return self.cards(pending_try=pending_try).get(card_id)

    def by_status(self, status: str, *, pending_try: str | None = None) -> list[Card]:
        return [c for c in self.cards(pending_try=pending_try).values() if c.status == status]

    def version(self) -> str:
        """Hash of the active set — stamps canary results."""
        return provenance_hash(*sorted(c.card_id for c in self.by_status(ACTIVE)))

    def recall(self, request: str, signals: list[str] | tuple[str, ...] = (), *, limit: int = 1,
               min_score: float = 0.3, signal_bonus: float = 0.3, require_signal_overlap: bool = False,
               pending_try: str | None = None) -> list[tuple[Card, float]]:
        """Active cards only, request char-ngram cosine + bonus on shared data signals.

        `min_score` closes the old hole where any non-empty memory injected
        its top-1 card into every task regardless of relevance.

        `require_signal_overlap` (pre-registered for the full run, 2026-10-19):
        a card is recalled only if its source task's data signals overlap the
        current task's detected L1 signals AND request cosine alone >= min_score;
        the score is then that cosine. Templated requests made similarity alone
        too broad (smoke: a footer card reached all 8 trap canaries). Cost: a
        card whose source had no detectable signal (e.g. footer written into a
        numeric column) is never recalled cross-task — it can still drive its
        own source task's retry or be promoted by hand.
        """
        q = _char_ngrams(request)
        sig = set(signals)
        scored = []
        for c in self.by_status(ACTIVE, pending_try=pending_try):
            cos = _cosine(q, _char_ngrams(c.source_request))
            if require_signal_overlap:
                if not sig & set(c.signals) or cos < min_score:
                    continue
                s = cos
            else:
                s = cos + (signal_bonus if sig & set(c.signals) else 0.0)
                if s < min_score:
                    continue
            scored.append((c, round(s, 4)))
        scored.sort(key=lambda x: (-x[1], x[0].card_id))
        return scored[:limit]

    def used_by(self, card_id: str) -> list[dict]:
        """Every committed run whose planner was handed this card."""
        return [{"task_id": e.task_id, "try_id": e.try_id, "attempt": e.payload.get("attempt"),
                 "run_id": e.payload.get("run_id"), "injected": e.payload.get("injected"),
                 "score": dict(zip(e.payload.get("card_ids", []), e.payload.get("scores", []))).get(card_id)}
                for e in self.log.events("CARD_RECALLED") if card_id in e.payload.get("card_ids", [])]

    # -- writes (all go through the log) ---------------------------------------

    def admit(self, card: Card, *, task_id: str, try_id: str, reasons: list[str] | None = None, **extra) -> Card:
        d = {**card.__dict__, "signals": list(card.signals), "observers": list(card.observers or (card.source_task_id,))}
        d.pop("history")
        self.log.append("CARD_ADMITTED", task_id=task_id, try_id=try_id, card=d, reasons=reasons or [], **extra)
        return self.get(card.card_id, pending_try=try_id)

    def reject(self, body: dict, reasons: list[str], *, task_id: str, try_id: str, **extra) -> None:
        self.log.append("CARD_REJECTED", task_id=task_id, try_id=try_id, body=body, reasons=reasons, **extra)

    def observe(self, card_id: str, *, task_id: str, try_id: str, **extra) -> Card:
        """A near-duplicate was distilled by *task_id*: count it, never promote on it."""
        self.log.append("CARD_OBSERVED", task_id=task_id, try_id=try_id, card_id=card_id, observer_task_id=task_id,
                        **extra)
        return self.get(card_id, pending_try=try_id)

    def promote(self, card_id: str, reason: str = "manual") -> Card:
        c = self._require(card_id, QUARANTINED)
        self.log.append("CARD_PROMOTED", card_id=c.card_id, reason=reason)
        return self.get(card_id)

    def evict(self, card_id: str, reason: str) -> Card:
        c = self._require(card_id, ACTIVE, QUARANTINED)
        # Snapshot the blast radius into the log itself: which runs were handed this card.
        self.log.append("CARD_EVICTED", card_id=c.card_id, reason=reason, used_by=self.used_by(card_id))
        return self.get(card_id)

    def record_recall(self, *, task_id: str, try_id: str, run_id: str, attempt: int,
                      hits: list[tuple[Card, float]], injected: bool, override: bool = False) -> None:
        self.log.append("CARD_RECALLED", task_id=task_id, try_id=try_id, run_id=run_id, attempt=attempt,
                        card_ids=[c.card_id for c, _ in hits], scores=[s for _, s in hits],
                        injected=injected, override=override)

    def _require(self, card_id: str, *statuses: str) -> Card:
        c = self.get(card_id)
        if c is None:
            raise KeyError(f"no card {card_id!r}")
        if c.status not in statuses:
            raise ValueError(f"card {card_id} is {c.status}, expected one of {statuses}")
        return c
