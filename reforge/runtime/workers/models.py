"""Worker descriptors and live state for P21 Worker Orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class WorkerSpec:
    """Static descriptor for a worker unit.

    worker_id   — unique identifier within a WorkerPool
    worker_type — logical category; routes tasks with matching worker_type
    capacity    — maximum concurrent tasks this worker may run
    """

    worker_id: str
    worker_type: str = "generic"
    capacity: int = 1

    def __post_init__(self) -> None:
        if self.capacity < 1:
            raise ValueError(f"capacity must be ≥ 1, got {self.capacity}")


@dataclass
class WorkerState:
    """Live execution state of a registered worker.

    Mutated under WorkerPool's internal lock; never access directly from
    outside WorkerPool — use WorkerPool.state() which returns a snapshot.
    """

    worker_id: str
    worker_type: str
    active: int = 0
    completed: int = 0
    failed: int = 0
    stopped: bool = False
