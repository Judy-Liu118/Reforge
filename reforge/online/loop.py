"""OnlineLoop — streaming, gold-free, resumable learning loop.

Per task (one `try_id`; every event below carries it, TASK_COMMITTED last):

    attempt 1   recall active cards (request similarity + data signals, with a
                floor) → agent → cheap tiers → critic if needed → VERDICT
    confident fail
                distil a card from the verifier's own diagnosis → dedupe →
                quality gate → active (critic-confirmed only) | quarantined
                (cheap-tier-only, gate soft fail, borderline) | rejected;
                then retry ONCE with that card injected. A quarantined card
                is injected only into its own source task, so a near-
                duplicate of another task's quarantined card means no retry.
                Attempt 2 is judged and logged; it distils nothing.
    confident pass / unverified
                done, no retry
    uncertain   review queue; no card, no retry
    commit      TASK_COMMITTED; only now does shadow.py score the attempts

A retry that succeeds is NOT evidence that the loop improves the agent: the
card was fit to this very task. Lift is reported from the canary A/B only.

Gold: the loop receives each task's GoldRef and hands it, untouched, to the
shadow log after commit. No decision reads it — see test_online_gold_isolation.
A harness error (agent call timed out, etc.) abandons the try uncommitted;
the task is rerun from scratch on the next resume.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from reforge.online import card_gate, metrics
from reforge.online.canary import Canary
from reforge.online.card_bank import ACTIVE, EVICTED, QUARANTINED, Card, CardBank, provenance_hash
from reforge.online.decision import TAU, Verdict, decide
from reforge.online.loop_log import LoopEventLog
from reforge.online.shadow import ShadowLog
from reforge.online.slots import task_slots
from reforge.online.tasks import GoldRef, TaskSpec


@dataclass
class Trajectory:
    stdout_tail: str
    code_tail: str
    printed: float | None
    outcome: str                      # runtime task_outcome
    plan_head: str = ""
    attempts: int = 0
    exit_codes: list[int] = field(default_factory=list)
    error_hint: str = ""              # last traceback tail — L0 evidence
    card_injected: bool = False       # planner actually pulled a card
    forbidden_terms: tuple[str, ...] = ()
    tokens_prompt: int = 0
    tokens_completion: int = 0
    llm_calls: int = 0
    duration_ms: float = 0.0
    error: str = ""                   # harness failure, not a task failure
    state: object = None              # final RuntimeState, for the distiller; never logged

    def summary(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "state"} | {"exit_codes": list(self.exit_codes),
                                                                          "forbidden_terms": list(self.forbidden_terms)}


class Agent(Protocol):
    def run(self, task: TaskSpec, data_dir: Path, run_dir: Path, cards: list[dict]) -> Trajectory: ...


class Verifier(Protocol):
    sha: str
    def data_signals(self, data_dir: Path) -> list[str]: ...
    def cheap(self, task: TaskSpec, data_dir: Path, traj: Trajectory, attempt: int, work: Path) -> dict: ...
    def critic(self, task: TaskSpec, data_dir: Path, traj: Trajectory, attempt: int) -> dict: ...


class Distiller(Protocol):
    model: str
    def distill(self, traj: Trajectory, feedback: str, forbidden_terms: tuple[str, ...]) -> tuple[dict, int]: ...


class HarnessError(RuntimeError):
    pass


@dataclass
class LoopConfig:
    tau: float = TAU
    audit_rates: dict[str, float] = field(default_factory=lambda: {"trap": 1.0, "infiagent": 0.2,
                                                                   "csv": 0.2, "bird": 0.2})
    audit_seed: int = 0
    recall_min_score: float = 0.3
    recall_signal_bonus: float = 0.3
    recall_rule: str = "signal_or_slot"        # see CardBank.recall; v3 runs "semantic"
    semantic_threshold: float | None = None    # v3: frozen offline on the dev set
    slot_filter: bool = False                  # v3: optional structured filter on top of semantic
    retry_on_confident_fail: bool = True
    agent_token_stop: int = 0         # 0 = no stop
    keep_work: bool = False
    verbose: bool = False
    canary_every: int = 20


def audit_draw(seed: int, task_id: str, attempt: int) -> float:
    """Deterministic U[0,1) per (seed, task, attempt) — same draw on resume."""
    h = hashlib.sha256(f"{seed}|{task_id}|{attempt}".encode()).digest()
    return int.from_bytes(h[:8], "big") / 2 ** 64


@dataclass
class _Attempt:
    n: int
    run_id: str
    data_dir: Path
    traj: Trajectory
    verdict: Verdict
    card_ids: list[str]
    slots: dict


class OnlineLoop:
    def __init__(self, root: Path, *, agent: Agent, verifier: Verifier, distiller: Distiller,
                 config: LoopConfig | None = None, canary_pairs: list | None = None, embed=None) -> None:
        self.root = Path(root)
        self.log = LoopEventLog(self.root / "loop_events.jsonl")
        self.bank = CardBank(self.log)
        self.shadow = ShadowLog(self.root / "shadow.jsonl")
        self.metrics_path = self.root / "metrics.jsonl"
        self.agent, self.verifier, self.distiller = agent, verifier, distiller
        self.cfg = config or LoopConfig()
        # one recall configuration for stream and canary alike
        self.recall_kw = dict(rule=self.cfg.recall_rule, min_score=self.cfg.recall_min_score,
                              signal_bonus=self.cfg.recall_signal_bonus, embed=embed,
                              semantic_threshold=self.cfg.semantic_threshold, slot_filter=self.cfg.slot_filter)
        self.canary = Canary(self.root, canary_pairs, agent=agent, data_signals=verifier.data_signals,
                             log=self.log, bank=self.bank, every=self.cfg.canary_every,
                             recall_kw=self.recall_kw, verbose=self.cfg.verbose) if canary_pairs else None

    def _spent(self) -> int:
        return metrics.agent_tokens(self.log) + (self.canary.tokens() if self.canary else 0)

    def _maybe_canary(self, *, exhausted: bool = False) -> None:
        if self.canary is not None:
            n = len(self.log.committed_tasks())
            if self.canary.due(n, exhausted=exhausted):
                self.canary.run_checkpoint(n)

    # -- driver ----------------------------------------------------------------

    def run(self, stream: Iterable[tuple[TaskSpec, GoldRef | None]], *, max_tasks: int = 0) -> dict:
        committed = self.log.committed_tasks()
        abandoned = self.log.abandoned_tries()
        self.log.append("LOOP_STARTED", resumed=bool(len(self.log)), committed=len(committed),
                        abandoned_tries=abandoned, verifier_sha=self.verifier.sha,
                        config={k: v for k, v in self.cfg.__dict__.items()})
        done_now, exhausted = 0, True
        self._maybe_canary()                                         # t=0 baseline on a fresh run
        for spec, gold_ref in stream:
            if spec.id in committed:
                self._shadow(spec, gold_ref, committed[spec.id])     # fills a row lost to a kill
                continue
            if max_tasks and done_now >= max_tasks:
                exhausted = False
                break
            if self.cfg.agent_token_stop and self._spent() >= self.cfg.agent_token_stop:
                print(f"TOKEN STOP: agent tokens (loop + canary) >= {self.cfg.agent_token_stop}", flush=True)
                exhausted = False
                break
            try:
                try_id = self.run_task(spec)
            except HarnessError as e:
                print(f"  !! {spec.id}: harness error, left uncommitted for resume: {e}", flush=True)
                exhausted = False
                continue
            self._shadow(spec, gold_ref, try_id)
            done_now += 1
            row = metrics.summarize(self.log, self.bank)
            with open(self.metrics_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"task_id": spec.id, "ts": datetime.now(timezone.utc).isoformat(), **row},
                                    ensure_ascii=False) + "\n")
            self._maybe_canary()
        if exhausted:
            self._maybe_canary(exhausted=True)                        # closing checkpoint
        summary = metrics.summarize(self.log, self.bank)
        self.log.append("METRICS", **summary)
        (self.root / "summary.md").write_text(metrics.markdown(summary), encoding="utf-8")
        return summary

    def run_task(self, spec: TaskSpec) -> str:
        try_id = uuid.uuid4().hex[:12]
        T = dict(task_id=spec.id, try_id=try_id)
        self.log.append("TASK_STARTED", **T, source=spec.source, tables=list(spec.tables))
        work = self.root / "work" / try_id
        try:
            a1 = self._attempt(spec, try_id, 1, work)
            attempts, cards, retry_skipped = [a1], [], ""
            if a1.verdict.confident_fail:
                card = self._learn(spec, try_id, a1)
                if card is None:
                    retry_skipped = "no_card"
                elif not self._injectable(card, spec.id):
                    retry_skipped = f"quarantined_card_of_other_task:{card.card_id}"
                else:
                    cards.append(card.card_id)
                    if self.cfg.retry_on_confident_fail:
                        attempts.append(self._attempt(spec, try_id, 2, work, override=card))
            for a in attempts:
                if a.verdict.label == "uncertain":
                    self.log.append("REVIEW_ENQUEUED", **T, attempt=a.n, run_id=a.run_id,
                                    reason=a.verdict.reason, critic_verdict=a.verdict.critic_verdict,
                                    critic_conf=a.verdict.critic_conf)
            last = attempts[-1].verdict
            self.log.append("TASK_COMMITTED", **T, source=spec.source, final_label=last.label,
                            final_confidence=last.confidence, attempts=len(attempts), cards=cards,
                            retry_skipped=retry_skipped)
        finally:
            if not self.cfg.keep_work:
                shutil.rmtree(work, ignore_errors=True)
        return try_id

    # -- one attempt -------------------------------------------------------------

    def _attempt(self, spec: TaskSpec, try_id: str, n: int, work: Path, override: Card | None = None) -> _Attempt:
        run_id = f"{try_id}/a{n}"
        T = dict(task_id=spec.id, try_id=try_id)
        data_dir = spec.build(work / f"a{n}" / "data")
        # The agent runs with data_dir as cwd and may write files into it; the
        # verifier gets its own untouched build of the same inputs.
        pristine = spec.build(work / f"a{n}" / "pristine")
        signals = self.verifier.data_signals(pristine)
        slots = task_slots(spec.request, spec.tables, pristine)
        hits = [(override, 1.0)] if override is not None else self.bank.recall(
            spec.request, signals, slots=slots, pending_try=try_id, **self.recall_kw)
        traj = self.agent.run(spec, data_dir, work / f"a{n}" / "run", [c.to_memory_record() for c, _ in hits])
        self.log.append("ATTEMPT_FINISHED", **T, attempt=n, run_id=run_id, **traj.summary())
        if traj.error:
            raise HarnessError(traj.error)
        self.bank.record_recall(**T, run_id=run_id, attempt=n, hits=hits, injected=traj.card_injected,
                                override=override is not None)
        cheap = self.verifier.cheap(spec, pristine, traj, n, work / f"a{n}" / "verify")
        audit = audit_draw(self.cfg.audit_seed, spec.id, n) < self.cfg.audit_rates.get(spec.source, 0.0)
        v = decide(cheap, lambda: self.verifier.critic(spec, pristine, traj, n), audit=audit, tau=self.cfg.tau)
        card_ids = [c.card_id for c, _ in hits]
        self.log.append("VERDICT", **T, attempt=n, run_id=run_id, card_ids=card_ids, slots=slots,
                        audit_eligible=audit, verifier_sha=self.verifier.sha,
                        l1=cheap.get("l1"), l1b=cheap.get("l1b"), **v.payload())
        if self.cfg.verbose:
            print(f"  [{spec.id:34s} a{n}] {v.label:10s} conf={v.confidence} tier={v.tier:9s} {v.reason:20s} "
                  f"printed={traj.printed} cards={card_ids} inj={traj.card_injected} "
                  f"tok={traj.tokens_prompt + traj.tokens_completion}", flush=True)
        return _Attempt(n, run_id, pristine, traj, v, card_ids, slots)

    # -- confident failure → card --------------------------------------------------

    def _learn(self, spec: TaskSpec, try_id: str, a: _Attempt) -> Card | None:
        T = dict(task_id=spec.id, try_id=try_id)
        v, traj = a.verdict, a.traj
        body, tokens = self.distiller.distill(traj, v.evidence, traj.forbidden_terms)
        if not body:
            self.bank.reject({}, ["distill_empty"], **T, distill_tokens=tokens)
            return None
        dup = card_gate.find_duplicate(body, self.bank.cards(pending_try=try_id).values())
        if dup is not None:
            if dup.status == EVICTED:
                self.bank.reject(body, [f"duplicate_of_evicted:{dup.card_id}"], **T, distill_tokens=tokens)
                return None
            # counted, never promoted on: a repeated cheap-tier bias looks the same as corroboration
            c = self.bank.observe(dup.card_id, **T, distill_tokens=tokens)
            self.log.append("REVIEW_ENQUEUED", **T, attempt=a.n, run_id=a.run_id, reason="card_reobserved",
                            card_id=c.card_id, card_status=c.status, observers=list(c.observers))
            return c
        g = card_gate.check(body, forbidden_terms=traj.forbidden_terms)
        if g.hard_reject:
            self.bank.reject(body, list(g.reasons), **T, distill_tokens=tokens)
            return None
        # Minting rule: active only on a critic-confirmed failure; a cheap-tier-only
        # failure (L0 / L1b) is quarantined — L1b's 0.95 holds on the trap corpus
        # it was measured on, not on natural data (smoke: da0024 false alarm).
        reasons = list(g.reasons)
        if not v.tier.startswith("critic"):
            reasons.append(f"cheap_tier_only:{v.tier}")
        if v.borderline:
            reasons.append("borderline_conf")
        card = Card(
            card_id=uuid.uuid4().hex[:10], body=body, source_task_id=spec.id, source_run_id=a.run_id,
            source_request=spec.request, created_at=datetime.now(timezone.utc).isoformat(),
            verifier_tier=v.tier, critic_confidence=v.critic_conf, signals=v.signals, slots=a.slots,
            provenance=provenance_hash(spec.request, traj.code_tail, traj.stdout_tail,
                                       json.dumps(v.payload(), sort_keys=True, ensure_ascii=False),
                                       self.verifier.sha, self.distiller.model),
            status=QUARANTINED if reasons else ACTIVE)
        return self.bank.admit(card, **T, reasons=reasons, distill_tokens=tokens)

    @staticmethod
    def _injectable(card: Card, task_id: str) -> bool:
        """Active cards go anywhere; a quarantined card only into its own source task."""
        return card.status == ACTIVE or (card.status == QUARANTINED and card.source_task_id == task_id)

    # -- shadow (after commit) -------------------------------------------------------

    def _shadow(self, spec: TaskSpec, gold_ref: GoldRef | None, try_id: str) -> None:
        if gold_ref is None:
            return
        finished = {e.payload["attempt"]: e for e in self.log.events("ATTEMPT_FINISHED") if e.try_id == try_id}
        for e in self.log.events("VERDICT"):
            if e.try_id == try_id and e.payload["attempt"] in finished:
                self.shadow.record(task_id=spec.id, try_id=try_id, attempt=e.payload["attempt"],
                                   source=spec.source, gold_ref=gold_ref,
                                   stdout=finished[e.payload["attempt"]].payload["stdout_tail"], verdict=e.payload)
