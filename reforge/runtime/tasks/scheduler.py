"""TaskScheduler — executes a TaskGraph respecting dependency order.

Algorithm
---------
1. validate() the graph upfront.
2. Submit all currently ready tasks to a ThreadPoolExecutor.
3. Block on wait(FIRST_COMPLETED); when a future resolves, record the result,
   update the completed/failed sets, skip any tasks whose dep just failed, and
   submit newly ready tasks.
4. Repeat until no futures are in flight and no new work can be submitted.

Skipping: a task is marked "skipped" (without running) as soon as any of its
direct deps is recorded as failed.  Transitive skips are handled naturally
because a skipped task never enters the completed set, so its dependents will
also be skipped in a subsequent iteration.
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait

from reforge.runtime.tasks.graph import TaskGraph
from reforge.runtime.tasks.models import Task, TaskResult, execute_task

_DEFAULT_MAX_WORKERS = 4


class TaskScheduler:
    """Synchronous DAG task executor backed by a thread pool."""

    def __init__(self, max_workers: int = _DEFAULT_MAX_WORKERS) -> None:
        self._max_workers = max(1, max_workers)

    def run(self, graph: TaskGraph) -> dict[str, TaskResult]:
        """Execute *graph* and return a mapping of task_id → TaskResult.

        Tasks with a failed (or transitively skipped) dependency are recorded
        as skipped without executing their callable.
        """
        graph.validate()

        all_ids: set[str] = set(graph.tasks)
        results: dict[str, TaskResult] = {}
        completed: set[str] = set()
        failed: set[str] = set()
        in_flight: dict[Future[TaskResult], str] = {}

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            while len(results) < len(all_ids):
                # -- skip tasks whose dep just failed ----------------------
                self._apply_skips(graph, results, failed, in_flight)

                # -- submit newly ready tasks ------------------------------
                self._submit_ready(graph, executor, completed, results, in_flight)

                if not in_flight:
                    break  # nothing running, nothing left to submit

                # -- wait for at least one future to finish ----------------
                done, _ = wait(list(in_flight), return_when=FIRST_COMPLETED)
                for future in done:
                    task_id = in_flight.pop(future)
                    result = future.result()
                    results[task_id] = result
                    if result.status == "completed":
                        completed.add(task_id)
                    else:
                        failed.add(task_id)

        return results

    # ------------------------------------------------------------------

    @staticmethod
    def _submit_ready(
        graph: TaskGraph,
        executor: ThreadPoolExecutor,
        completed: set[str],
        results: dict[str, TaskResult],
        in_flight: dict[Future[TaskResult], str],
    ) -> None:
        in_flight_ids = set(in_flight.values())
        for task in graph.ready(completed):
            if task.task_id not in results and task.task_id not in in_flight_ids:
                future = executor.submit(execute_task, task)
                in_flight[future] = task.task_id

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
                # Treat skipped as failed so its dependents also get skipped
                failed.add(task.task_id)
