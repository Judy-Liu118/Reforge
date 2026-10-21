"""ExperienceLoop — the entry points an agent host calls.

    recall(task)                 before execution: conventions by scope (+ layer 2 if plugged in)
    recall_on_step(task, step)   during execution: layer 2 only (runtime triggers; unsolved by default)
    render(cards)                the prompt block (guidance, not code; never put in an execution output)
    mint(failure, distill)       after a gold-free verifier confirmed a process failure
                                 → distill → gate → admit QUARANTINED (or log the rejection)
    promote(card_id, canary)     QUARANTINED → ACTIVE, only on a passing paired canary report
    evict(card_id, reason)       terminal; used_by snapshot goes into the event

Feature flag: `ExperienceConfig.enabled`, default from env REFORGE_EXPERIENCE_CARDS
(off unless "1"). Off means recall() / recall_on_step() return no cards and
log nothing, so attaching the loop does not change agent behaviour. mint /
promote / evict work regardless, so a bank can be built and canaried offline
before the flag is turned on for a slice of traffic.

Gold never enters here: inputs are task text, step records the host already
has, and verifier evidence.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from reforge.experience import gate
from reforge.experience.cards import CONVENTION, ConstraintCard, Scope, VerifiedFailure, provenance_hash
from reforge.experience.recall import TaskContext, TaskLevelRetriever, scope_recall, unsolved_task_retriever
from reforge.experience.store import CanaryReport, ConstraintCardStore
from reforge.online.loop_log import LoopEventLog

FLAG = "REFORGE_EXPERIENCE_CARDS"
HEADER = ("Guardrails learned from earlier verified failures in this environment "
          "(guidance for your reasoning, not code to copy):")

# distill(failure) -> {"trigger": str, "guardrail": str}; the host supplies it (an LLM call or a template)
Distiller = Callable[[VerifiedFailure], dict]


def _flag_default() -> bool:
    return os.environ.get(FLAG, "0") == "1"


@dataclass
class ExperienceConfig:
    log_path: Path
    env: str
    known_apps: tuple[str, ...] = ()
    enabled: bool = field(default_factory=_flag_default)
    max_injected: int = 4
    banned_terms: frozenset[str] = frozenset()                  # e.g. app / API names for env-wide cards
    banned_by_kind: dict[str, frozenset[str]] = field(default_factory=dict)   # kind -> extra banned terms
    required_anchors: dict[str, frozenset[str]] = field(default_factory=dict)   # kind -> terms that must appear
    task_retriever: TaskLevelRetriever = unsolved_task_retriever
    distill_attempts: int = 2


@dataclass(frozen=True)
class MintResult:
    card: ConstraintCard | None
    reasons: tuple[str, ...]
    attempts: int


class ExperienceLoop:
    def __init__(self, config: ExperienceConfig) -> None:
        self.config = config
        self.store = ConstraintCardStore(LoopEventLog(config.log_path))

    # -- recall ---------------------------------------------------------------

    def recall(self, task: TaskContext) -> list[ConstraintCard]:
        if not self.config.enabled:
            return []
        active = self.store.by_status("active")
        conv = scope_recall(task, active, self.config.known_apps)
        extra = [c for c in self.config.task_retriever(task, active, stage="pre") if c not in conv]
        picked = (conv + extra)[: self.config.max_injected]
        self.store.record_recall(task_id=task.task_id, run_id=task.run_id, stage="pre",
                                 layer="scope" if not extra else "scope+task", cards=picked, injected=bool(picked))
        return picked

    def recall_on_step(self, task: TaskContext, step: dict, already: list[ConstraintCard]) -> list[ConstraintCard]:
        if not self.config.enabled:
            return []
        new = [c for c in self.config.task_retriever(task, self.store.by_status("active"), stage="step", step=step)
               if c not in already]
        if new:
            self.store.record_recall(task_id=task.task_id, run_id=task.run_id, stage="step", layer="task",
                                     cards=new, injected=True)
        return new

    @staticmethod
    def render(cards: list[ConstraintCard]) -> str:
        if not cards:
            return ""
        return HEADER + "".join(f"\n- When: {c.trigger}\n  Then: {c.guardrail}" for c in cards)

    # -- learning ---------------------------------------------------------------

    def mint(self, failure: VerifiedFailure, distill: Distiller, *, apps: tuple[str, ...] | None = None,
             level: str = CONVENTION) -> MintResult:
        """Verifier-confirmed failure → card in quarantine. Gold is not an input: confirmation is the verifier's."""
        required = self.config.required_anchors.get(failure.kind, frozenset())
        banned = self.config.banned_terms | self.config.banned_by_kind.get(failure.kind, frozenset())
        reasons: tuple[str, ...] = ()
        for attempt in range(1, self.config.distill_attempts + 1):
            body = distill(failure) or {}
            trig, guard = str(body.get("trigger", "")), str(body.get("guardrail", ""))
            res = gate.check(trig, guard, banned=banned, required=required, has_evidence=True)
            if not res.ok:
                reasons = res.reasons
                self.store.reject(trig, guard, res.reasons, source_task=failure.source_task)
                continue
            dup = next((c for c in self.store.cards().values()
                        if c.kind == failure.kind and c.trigger == trig and c.guardrail == guard), None)
            if dup is not None:                         # same lesson again (or an evicted one): never re-admit
                reasons = (f"duplicate_of:{dup.card_id}:{dup.status}",)
                self.store.reject(trig, guard, reasons, source_task=failure.source_task)
                return MintResult(None, reasons, attempt)
            card = ConstraintCard(card_id=f"cc-{uuid.uuid4().hex[:10]}", trigger=trig, guardrail=guard,
                                  scope=Scope(env=self.config.env, apps=apps, level=level), kind=failure.kind,
                                  source_task=failure.source_task, verifier=failure.verifier,
                                  evidence=failure.evidence, provenance=provenance_hash(failure, trig, guard))
            return MintResult(self.store.admit(card), (), attempt)
        return MintResult(None, reasons, self.config.distill_attempts)

    def promote(self, card_id: str, canary: CanaryReport) -> ConstraintCard:
        return self.store.promote(card_id, canary)

    def evict(self, card_id: str, reason: str) -> ConstraintCard:
        return self.store.evict(card_id, reason)
