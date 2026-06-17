"""WorkerOrchestrator — executes a TaskGraph via a WorkerPool.

Algorithm mirrors TaskScheduler (P20) but tasks are routed to named, typed
workers instead of anonymous ThreadPoolExecutor slots:

1. validate() the graph upfront.
2. _apply_skips(): mark tasks whose dep just failed as skipped.
3. _submit_ready(): route eligible tasks to the pool; silently defer tasks
   that hit WorkerUnavailableError (no free matching worker) — they will be
   retried when an in-flight task completes and frees a worker slot.
4. wait(FIRST_COMPLETED) then record results and update completed/failed sets.
5. Repeat until no tasks remain.
6. _finalize(): any tasks still pending after the loop are either dependents
   of failed tasks (→ skipped) or permanently unroutable (→ failed).
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, wait

from reforge.runtime.tasks.graph import TaskGraph
from reforge.runtime.tasks.models import TaskResult
from reforge.runtime.workers.pool import WorkerPool, WorkerUnavailableError


class WorkerOrchestrator:
    """Executes a TaskGraph by dispatching tasks through a WorkerPool."""

    def __init__(self, pool: WorkerPool) -> None:
        self._pool = pool

    def run(self, graph: TaskGraph) -> dict[str, TaskResult]:
        """Execute *graph* and return task_id → TaskResult for every task."""
        graph.validate()

        all_ids: set[str] = set(graph.tasks)
        results: dict[str, TaskResult] = {}
        completed: set[str] = set()
        failed: set[str] = set()
        in_flight: dict[Future[TaskResult], str] = {}

        while len(results) < len(all_ids):
            self._apply_skips(graph, results, failed, in_flight)
            self._submit_ready(graph, completed, results, in_flight)

            if not in_flight:
                break  # nothing running and nothing new to submit → exit

            done, _ = wait(list(in_flight), return_when=FIRST_COMPLETED)
            for future in done:
                task_id = in_flight.pop(future)
                result = future.result()
                results[task_id] = result
                if result.status == "completed":
                    completed.add(task_id)
                else:
                    failed.add(task_id)

        self._finalize(graph, results, failed)
        return results

    # ------------------------------------------------------------------

    def _submit_ready(
        self,
        graph: TaskGraph,
        completed: set[str],
        results: dict[str, TaskResult],
        in_flight: dict[Future[TaskResult], str],
    ) -> None:
        in_flight_ids = set(in_flight.values())
        for task in graph.ready(completed):
            if task.task_id in results or task.task_id in in_flight_ids:
                continue
            try:
                future = self._pool.submit(task)
                in_flight[future] = task.task_id
            except WorkerUnavailableError:
                pass  # defer until a worker frees up

    @staticmethod
    def _apply_skips(
        graph: TaskGraph,
        results: dict[str, TaskResult],
        failed: set[str],
        in_flight: dict[Future[TaskResult], str],
    ) -> None:
        in_flight_ids = set(in_flight.values())
        for task in graph.tasks.values():
            if task.task_id in results or task.task_id in in_flight_ids:
                continue
            if task.deps & failed:
                results[task.task_id] = TaskResult(
                    task_id=task.task_id, status="skipped"
                )
                failed.add(task.task_id)

    @staticmethod
    def _finalize(
        graph: TaskGraph,
        results: dict[str, TaskResult],
        failed: set[str],
    ) -> None:
        """Resolve any tasks still pending after the main loop exits.

        Two ordered passes:
        Pass 1 — mark truly-stuck tasks (all in-graph deps resolved, no dep
                  failed them) as failed with an unroutable error.  Repeats
                  until no new stuck task is found, so that chained unroutable
                  tasks are handled correctly.
        Pass 2 — skip any remaining tasks whose dep just entered failed.
        """
        all_task_ids = set(graph.tasks)

        # Pass 1: mark unroutable tasks as failed
        changed = True
        while changed:
            changed = False
            for task_id, task in graph.tasks.items():
                if task_id in results:
                    continue
                in_graph_deps = task.deps & all_task_ids
                if in_graph_deps - set(results):
                    continue  # still has pending in-graph deps
                if task.deps & failed:
                    continue  # a dep already failed → handle in pass 2
                results[task_id] = TaskResult(
                    task_id=task_id,
                    status="failed",
                    error=f"No worker available for type {task.worker_type!r}",
                )
                failed.add(task_id)
                changed = True

        # Pass 2: skip dependents of all failed tasks
        changed = True
        while changed:
            changed = False
            for task_id, task in graph.tasks.items():
                if task_id in results:
                    continue
                if task.deps & failed:
                    results[task_id] = TaskResult(task_id=task_id, status="skipped")
                    failed.add(task_id)
                    changed = True
