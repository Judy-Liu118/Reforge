"""Task and TaskResult dataclasses for the P20 task graph scheduler.

Task uses a plain dataclass (not Pydantic) because fn: Callable is not
JSON-serialisable; equality and hashing are intentionally identity-based.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

TaskStatus = Literal["completed", "failed", "skipped"]


@dataclass
class Task:
    """A schedulable unit of work with explicit dependency declarations.

    task_id     — unique within a TaskGraph; used to express deps
    fn          — zero-argument callable; return value becomes TaskResult.output
    deps        — set of task_ids that must complete before this task is ready
    priority    — higher value → scheduled first among equally-ready tasks
    worker_type — optional; routes task to workers of this type in WorkerPool;
                  empty string means any available worker
    """

    task_id: str
    fn: Callable[[], Any]
    deps: frozenset[str] = field(default_factory=frozenset)
    priority: int = 0
    worker_type: str = ""

    def __post_init__(self) -> None:
        # Accept plain sets/lists and normalise to frozenset
        if not isinstance(self.deps, frozenset):
            object.__setattr__(self, "deps", frozenset(self.deps))


@dataclass
class TaskResult:
    """Outcome of a single task execution."""

    task_id: str
    status: TaskStatus
    output: Any = None
    error: str = ""
    duration_ms: float = 0.0


def execute_task(task: Task) -> TaskResult:
    """Run *task.fn* and return a TaskResult; never raises."""
    start = time.monotonic()
    try:
        output = task.fn()
        return TaskResult(
            task_id=task.task_id,
            status="completed",
            output=output,
            duration_ms=(time.monotonic() - start) * 1000,
        )
    except Exception as exc:  # noqa: BLE001
        return TaskResult(
            task_id=task.task_id,
            status="failed",
            error=str(exc),
            duration_ms=(time.monotonic() - start) * 1000,
        )
