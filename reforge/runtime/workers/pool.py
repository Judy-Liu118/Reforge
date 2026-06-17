"""WorkerPool — routes tasks to named, typed, capacity-limited workers.

Routing rules
-------------
- Task.worker_type == ""  → any non-stopped worker with available capacity
- Task.worker_type == "X" → only workers whose worker_type == "X"
- Among eligible workers, the least-loaded (lowest active count) is chosen.
- If no eligible worker exists, WorkerUnavailableError is raised; the caller
  (typically WorkerOrchestrator) may retry after an in-flight task completes.
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace

from reforge.runtime.tasks.models import Task, TaskResult, execute_task
from reforge.runtime.workers.models import WorkerSpec, WorkerState

_DEFAULT_MAX_THREADS = 16


class WorkerUnavailableError(RuntimeError):
    """No eligible worker exists for the requested worker_type."""


class WorkerPool:
    """Thread-backed pool of named workers with typed task routing."""

    def __init__(self, max_threads: int = _DEFAULT_MAX_THREADS) -> None:
        self._specs: dict[str, WorkerSpec] = {}
        self._states: dict[str, WorkerState] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max(1, max_threads))

    # ------------------------------------------------------------------
    # Registration

    def register(self, spec: WorkerSpec) -> None:
        """Register *spec*.  Duplicate worker_id overwrites the previous entry."""
        with self._lock:
            self._specs[spec.worker_id] = spec
            self._states[spec.worker_id] = WorkerState(
                worker_id=spec.worker_id,
                worker_type=spec.worker_type,
            )

    def stop(self, worker_id: str) -> None:
        """Mark *worker_id* as stopped; no new tasks will be routed to it.

        In-flight tasks already submitted to the worker continue to completion.
        """
        with self._lock:
            if worker_id not in self._states:
                raise KeyError(worker_id)
            self._states[worker_id].stopped = True

    # ------------------------------------------------------------------
    # Submission

    def submit(self, task: Task) -> Future[TaskResult]:
        """Route *task* to an available worker and return a Future.

        Raises WorkerUnavailableError if no eligible worker has free capacity.
        """
        with self._lock:
            spec = self._pick_worker(task.worker_type)
            self._states[spec.worker_id].active += 1

        wid = spec.worker_id
        future: Future[TaskResult] = self._executor.submit(execute_task, task)

        def _on_done(f: Future) -> None:
            result: TaskResult = f.result()
            with self._lock:
                st = self._states[wid]
                st.active -= 1
                if result.status == "completed":
                    st.completed += 1
                else:
                    st.failed += 1

        future.add_done_callback(_on_done)
        return future

    # ------------------------------------------------------------------
    # Inspection

    def state(self, worker_id: str) -> WorkerState:
        """Return a snapshot of *worker_id*'s current state."""
        with self._lock:
            if worker_id not in self._states:
                raise KeyError(worker_id)
            s = self._states[worker_id]
            return WorkerState(
                worker_id=s.worker_id,
                worker_type=s.worker_type,
                active=s.active,
                completed=s.completed,
                failed=s.failed,
                stopped=s.stopped,
            )

    def all_states(self) -> dict[str, WorkerState]:
        """Return a snapshot of all workers' states."""
        with self._lock:
            return {
                wid: WorkerState(
                    worker_id=s.worker_id,
                    worker_type=s.worker_type,
                    active=s.active,
                    completed=s.completed,
                    failed=s.failed,
                    stopped=s.stopped,
                )
                for wid, s in self._states.items()
            }

    # ------------------------------------------------------------------
    # Lifecycle

    def shutdown(self, wait: bool = True) -> None:
        """Shut down the underlying thread pool."""
        self._executor.shutdown(wait=wait)

    # ------------------------------------------------------------------
    # Internal

    def _pick_worker(self, worker_type: str) -> WorkerSpec:
        """Return least-loaded eligible worker.  Caller must hold _lock."""
        candidates = [
            spec
            for spec in self._specs.values()
            if not self._states[spec.worker_id].stopped
            and self._states[spec.worker_id].active < spec.capacity
            and (not worker_type or spec.worker_type == worker_type)
        ]
        if not candidates:
            raise WorkerUnavailableError(
                f"No available worker for type {worker_type!r}"
            )
        return min(candidates, key=lambda s: self._states[s.worker_id].active)
