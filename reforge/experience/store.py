"""ConstraintCardStore — card state as a projection of the loop event log.

Reuses reforge.online.loop_log.LoopEventLog (append-only JSONL, fsync, commit
semantics), so cards are never stored anywhere else: resume, rollback and
"which runs were handed this card" are reads of the log.

Lifecycle
    admit     → QUARANTINED   only with VerifiedFailure evidence and a passing gate
    promote   → ACTIVE        only with a passing CanaryReport (logged as CANARY_RESULT)
    evict     → EVICTED       terminal; snapshots used_by (blast radius) into the event
Every recall is logged as CARD_RECALLED (task, run, card ids, layer) — that is
the used_by provenance.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from reforge.experience.cards import ACTIVE, EVICTED, QUARANTINED, ConstraintCard
from reforge.online.loop_log import LoopEventLog

_SOURCE = "experience"            # tags this component's events in a shared log


@dataclass(frozen=True)
class CanaryReport:
    """Paired canary run: same canary tasks with and without the candidate card (scored offline)."""
    card_id: str
    n: int
    control_success: int
    treated_success: int
    regressions: tuple[str, ...] = ()        # canary tasks that passed without the card and fail with it

    @property
    def passed(self) -> bool:
        return self.n > 0 and not self.regressions and self.treated_success >= self.control_success


class ConstraintCardStore:
    def __init__(self, log: LoopEventLog) -> None:
        self.log = log

    def _mine(self, kind: str):
        return [e for e in self.log.events(kind) if e.payload.get("source") == _SOURCE]

    def cards(self) -> dict[str, ConstraintCard]:
        out: dict[str, ConstraintCard] = {}
        for e in self.log.events():
            p = e.payload
            if p.get("source") != _SOURCE:
                continue
            if e.kind == "CARD_ADMITTED":
                c = ConstraintCard.from_dict(p["card"])
                out[c.card_id] = replace(c, history=({"event": "admitted", "ts": e.timestamp},))
            elif e.kind in ("CARD_PROMOTED", "CARD_EVICTED") and p.get("card_id") in out:
                c = out[p["card_id"]]
                status = ACTIVE if e.kind == "CARD_PROMOTED" else EVICTED
                out[c.card_id] = replace(c, status=status, history=c.history + (
                    {"event": e.kind.removeprefix("CARD_").lower(), "reason": p.get("reason", ""), "ts": e.timestamp},))
        return out

    def get(self, card_id: str) -> ConstraintCard | None:
        return self.cards().get(card_id)

    def by_status(self, status: str) -> list[ConstraintCard]:
        return [c for c in self.cards().values() if c.status == status]

    # -- writes ---------------------------------------------------------------

    def admit(self, card: ConstraintCard, *, reasons: tuple[str, ...] = ()) -> ConstraintCard:
        if not card.evidence:
            raise ValueError("verifier-gated store: no confirmed failure behind this card")
        if card.card_id in self.cards():
            raise ValueError(f"card {card.card_id} already exists")
        self.log.append("CARD_ADMITTED", task_id=card.source_task, source=_SOURCE,
                        card={**card.to_dict(), "status": QUARANTINED}, reasons=list(reasons))
        return self.get(card.card_id)

    def reject(self, trigger: str, guardrail: str, reasons: tuple[str, ...], *, source_task: str) -> None:
        self.log.append("CARD_REJECTED", task_id=source_task, source=_SOURCE,
                        body={"trigger": trigger, "guardrail": guardrail}, reasons=list(reasons))

    def promote(self, card_id: str, canary: CanaryReport) -> ConstraintCard:
        c = self._require(card_id, QUARANTINED)
        if canary.card_id != card_id:
            raise ValueError("canary report is for a different card")
        self.log.append("CANARY_RESULT", source=_SOURCE, card_id=card_id, n=canary.n,
                        control_success=canary.control_success, treated_success=canary.treated_success,
                        regressions=list(canary.regressions), passed=canary.passed)
        if not canary.passed:
            raise ValueError(f"canary did not pass for {card_id}: {canary}")
        self.log.append("CARD_PROMOTED", source=_SOURCE, card_id=c.card_id, reason="canary passed")
        return self.get(card_id)

    def evict(self, card_id: str, reason: str) -> ConstraintCard:
        c = self._require(card_id, ACTIVE, QUARANTINED)
        self.log.append("CARD_EVICTED", source=_SOURCE, card_id=c.card_id, reason=reason,
                        used_by=self.used_by(card_id))
        return self.get(card_id)

    def record_recall(self, *, task_id: str, run_id: str, stage: str, layer: str,
                      cards: list[ConstraintCard], injected: bool) -> None:
        self.log.append("CARD_RECALLED", task_id=task_id, source=_SOURCE, run_id=run_id, stage=stage,
                        layer=layer, card_ids=[c.card_id for c in cards], injected=injected)

    def used_by(self, card_id: str) -> list[dict]:
        return [{"task_id": e.task_id, "run_id": e.payload.get("run_id"), "stage": e.payload.get("stage"),
                 "layer": e.payload.get("layer"), "injected": e.payload.get("injected"), "ts": e.timestamp}
                for e in self._mine("CARD_RECALLED") if card_id in e.payload.get("card_ids", [])]

    def _require(self, card_id: str, *statuses: str) -> ConstraintCard:
        c = self.get(card_id)
        if c is None:
            raise KeyError(f"no card {card_id!r}")
        if c.status not in statuses:
            raise ValueError(f"card {card_id} is {c.status}, expected one of {statuses}")
        return c
