"""Constraint cards and the verified failures they are minted from.

A card is one atomic constraint: a `trigger` (when it applies, phrased as
something the agent can observe) and a `guardrail` (what to do). It is reasoning
guidance for the agent's prompt, never code to run and never spliced into an
execution output.

`scope` decides the recall layer:
    level="convention"  app / environment convention (login identity, pagination).
                        Recalled by scope: apps=None means every task in the
                        environment, otherwise tasks that name one of `apps`.
    level="task"        task-specific lesson. Needs task-level retrieval, which
                        is NOT solved (see recall.py) — such cards are stored
                        but never recalled by the default retriever.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

CONVENTION, TASK = "convention", "task"
QUARANTINED, ACTIVE, EVICTED = "quarantined", "active", "evicted"


@dataclass(frozen=True)
class Scope:
    env: str                                  # e.g. "appworld"
    apps: tuple[str, ...] | None = None       # None = environment-wide
    level: str = CONVENTION

    def to_dict(self) -> dict:
        return {"env": self.env, "apps": list(self.apps) if self.apps is not None else None, "level": self.level}

    @classmethod
    def from_dict(cls, d: dict) -> "Scope":
        apps = d.get("apps")
        return cls(env=d["env"], apps=tuple(apps) if apps is not None else None, level=d.get("level", CONVENTION))


@dataclass(frozen=True)
class VerifiedFailure:
    """A process failure confirmed by a gold-free verifier — the only thing a card may be minted from.

    `evidence` must be redacted by the probe (no user data, ids or answers): it is
    what the distiller sees.
    """
    kind: str                     # "pagination" | "login_identity" | ...
    verifier: str                 # which check confirmed it, e.g. "next_page_nonempty"
    evidence: tuple[dict, ...]
    source_task: str
    source_run: str = ""

    def __post_init__(self) -> None:
        if not self.evidence:
            raise ValueError("VerifiedFailure needs non-empty verifier evidence")


@dataclass(frozen=True)
class ConstraintCard:
    card_id: str
    trigger: str
    guardrail: str
    scope: Scope
    kind: str
    source_task: str
    verifier: str
    evidence: tuple[dict, ...]
    provenance: str
    status: str = QUARANTINED
    history: tuple[dict, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["scope"] = self.scope.to_dict()
        d["evidence"] = list(self.evidence)
        d.pop("history")
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ConstraintCard":
        return cls(**{**d, "scope": Scope.from_dict(d["scope"]), "evidence": tuple(d.get("evidence", ())),
                      "history": ()})


def provenance_hash(failure: VerifiedFailure, trigger: str, guardrail: str) -> str:
    blob = json.dumps([failure.kind, failure.verifier, list(failure.evidence), failure.source_task,
                       failure.source_run, trigger, guardrail], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
