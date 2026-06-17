"""Data models for the Parallel Execution Runtime (P24).

RuntimeTask   — describes a single RuntimeRunner invocation
RuntimeOutput — structured result of runner.run()
RuntimeResult — caller-facing summary (analogous to TaskResult for runtime tasks)

RuntimeTask deliberately mirrors the Task dataclass contract from P20:
  task_id / deps / priority / worker_type are identical in semantics
so RuntimeTask objects can be trivially converted to Task objects inside
ParallelRuntime without leaking implementation details upward.

No dependencies on LangGraph or LLM clients — models stay serialisable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from reforge.runtime.tasks.models import TaskStatus


@dataclass
class RuntimeOutput:
    """Structured output of a single RuntimeRunner.run() call.

    Carries both the final RuntimeState and runner identity so callers can
    trace events back to a specific session.
    """

    state: Any  # RuntimeState — typed as Any to avoid circular import
    session_id: str
    event_log: Any | None = None  # ExecutionEventLog | None


@dataclass
class RuntimeTask:
    """A task whose work is running a RuntimeRunner against a user_request.

    runner_factory — zero-argument callable that creates a fresh RuntimeRunner;
                     called once per execution for complete session isolation
    deps           — task_ids that must complete before this task is eligible
    priority       — higher → scheduled first among equally-ready tasks
    worker_type    — routes to workers with matching type in WorkerPool
    """

    task_id: str
    user_request: str
    runner_factory: Callable[[], Any]  # () -> RuntimeRunner
    deps: frozenset[str] = field(default_factory=frozenset)
    priority: int = 0
    worker_type: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.deps, frozenset):
            object.__setattr__(self, "deps", frozenset(self.deps))


@dataclass
class RuntimeResult:
    """Caller-facing outcome of a RuntimeTask execution.

    Mirrors TaskResult semantics (status: completed/failed/skipped) but adds
    runtime-specific fields: final_answer, session_id, and the full output.
    """

    task_id: str
    user_request: str
    status: TaskStatus
    final_answer: str = ""
    session_id: str = ""
    error: str = ""
    duration_ms: float = 0.0
    output: RuntimeOutput | None = None
