"""Two-layer recall.

Layer 1 — conventions, by scope (implemented, measured). An app / environment
convention card is handed to every task of its environment whose instruction
names one of the card's apps (apps=None: every task). No embedding: the
measured failure of embedding here is that a convention card's trigger
describes a runtime event ("a paginated call returns exactly page_limit
items") that no task instruction mentions — over 90 AppWorld train
instructions the useful pagination / login cards cleared a null-calibrated
threshold 4/90 and 1/90 times, while a near-miss card phrased in task language
cleared it 46/90.

Layer 2 — task-level cards (NOT solved). Retrieval of task-specific lessons is
an open problem in this project (instruction-vs-trigger similarity is the
wrong key; request-text similarity on templated tasks had top-1 same-root-cause
precision 1/21 in the CSV loop). The interface exists so a host can plug a
retriever in; the default returns nothing, so task-level cards are stored and
gated but never injected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from reforge.experience.cards import ACTIVE, CONVENTION, TASK, ConstraintCard


@dataclass(frozen=True)
class TaskContext:
    task_id: str
    env: str
    instruction: str
    run_id: str = ""


def apps_in(instruction: str, known_apps: tuple[str, ...]) -> set[str]:
    """App names mentioned in the instruction text (snake_case apps match 'simple note' too)."""
    t = instruction.lower().replace(" ", "_")
    return {a for a in known_apps if re.search(rf"(?<![a-z]){re.escape(a)}(?![a-z])", t)}


def scope_recall(task: TaskContext, cards: list[ConstraintCard], known_apps: tuple[str, ...]) -> list[ConstraintCard]:
    mentioned = apps_in(task.instruction, known_apps)
    return [c for c in cards
            if c.status == ACTIVE and c.scope.level == CONVENTION and c.scope.env == task.env
            and (c.scope.apps is None or bool(set(c.scope.apps) & mentioned))]


class TaskLevelRetriever(Protocol):
    """Plug-in point for layer 2. Must be gold-free and return only ACTIVE task-level cards."""

    def __call__(self, task: TaskContext, cards: list[ConstraintCard], *, stage: str,
                 step: dict | None = None) -> list[ConstraintCard]: ...


def unsolved_task_retriever(task: TaskContext, cards: list[ConstraintCard], *, stage: str,
                            step: dict | None = None) -> list[ConstraintCard]:
    """Default layer 2: returns nothing (task-level retrieval is an open problem — see module doc)."""
    return []


def task_level_only(cards: list[ConstraintCard]) -> list[ConstraintCard]:
    return [c for c in cards if c.status == ACTIVE and c.scope.level == TASK]
