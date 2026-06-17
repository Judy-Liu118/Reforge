"""ParallelRuntime — executes RuntimeTasks concurrently via WorkerOrchestrator.

Bridging layer between the P21 worker/task substrate and the P18 runtime loop:

    RuntimeTask list
        ↓  _build_graph()
    TaskGraph  (each Task.fn wraps a RuntimeRunner.run() call)
        ↓  WorkerOrchestrator.run()
    dict[task_id, TaskResult]  (TaskResult.output = RuntimeOutput)
        ↓  _to_result()
    dict[task_id, RuntimeResult]

Isolation guarantee: each RuntimeTask specifies its own runner_factory.
_make_fn() calls runner_factory() fresh for every execution, so concurrent
tasks never share RuntimeRunner state, session_id, or event_log.

Failure semantics mirror TaskScheduler / WorkerOrchestrator:
  - A runner that raises → TaskResult(status="failed", error=str(exc))
  - A task whose dep failed/was-skipped → RuntimeResult(status="skipped")
"""

from __future__ import annotations

from typing import Callable, Iterable

from reforge.runtime.parallel.models import RuntimeOutput, RuntimeResult, RuntimeTask
from reforge.runtime.tasks.graph import TaskGraph
from reforge.runtime.tasks.models import Task, TaskResult
from reforge.runtime.workers.orchestrator import WorkerOrchestrator
from reforge.runtime.workers.pool import WorkerPool


class ParallelRuntime:
    """Run a collection of RuntimeTasks concurrently, respecting dependencies."""

    def __init__(self, pool: WorkerPool) -> None:
        self._orchestrator = WorkerOrchestrator(pool)

    def run(self, tasks: Iterable[RuntimeTask]) -> dict[str, RuntimeResult]:
        """Execute *tasks* and return task_id → RuntimeResult for every task."""
        task_list = list(tasks)
        if not task_list:
            return {}

        graph = self._build_graph(task_list)
        task_results = self._orchestrator.run(graph)

        by_id = {t.task_id: t for t in task_list}
        return {
            tid: self._to_result(by_id[tid], tr)
            for tid, tr in task_results.items()
        }

    # ------------------------------------------------------------------

    @staticmethod
    def _build_graph(tasks: list[RuntimeTask]) -> TaskGraph:
        graph = TaskGraph()
        for rt in tasks:
            graph.add(
                Task(
                    task_id=rt.task_id,
                    fn=ParallelRuntime._make_fn(rt),
                    deps=rt.deps,
                    priority=rt.priority,
                    worker_type=rt.worker_type,
                )
            )
        return graph

    @staticmethod
    def _make_fn(rt: RuntimeTask) -> Callable[[], RuntimeOutput]:
        """Return a zero-arg callable that runs a fresh RuntimeRunner."""
        def fn() -> RuntimeOutput:
            runner = rt.runner_factory()
            state = runner.run(rt.user_request)
            return RuntimeOutput(
                state=state,
                session_id=runner.session_id,
                event_log=runner.event_log,
            )
        return fn

    @staticmethod
    def _to_result(rt: RuntimeTask, tr: TaskResult) -> RuntimeResult:
        if tr.status != "completed":
            return RuntimeResult(
                task_id=rt.task_id,
                user_request=rt.user_request,
                status=tr.status,
                error=tr.error,
                duration_ms=tr.duration_ms,
            )
        output: RuntimeOutput = tr.output
        final_answer = ""
        if output and output.state is not None:
            outcome = getattr(output.state, "outcome_state", None)
            if outcome is not None:
                final_answer = getattr(outcome, "final_answer", "") or ""
        return RuntimeResult(
            task_id=rt.task_id,
            user_request=rt.user_request,
            status="completed",
            final_answer=final_answer,
            session_id=output.session_id if output else "",
            duration_ms=tr.duration_ms,
            output=output,
        )
