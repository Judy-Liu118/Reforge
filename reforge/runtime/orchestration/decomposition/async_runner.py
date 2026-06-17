"""AsyncSubtaskRunner — parallel execution of independent subtasks.

Uses ThreadPoolExecutor because LangGraph's graph.stream() is synchronous.
Subtasks with no mutual dependencies in the same topological level run
concurrently; dependent subtasks wait for their prerequisites.

Context propagation: when subtask N depends on M, M's final_answer is
injected into N's request before execution so the LLM has the necessary data.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

from reforge.runtime.orchestration.decomposition.models import (
    DecompositionResult,
    MultiStepResult,
    SubtaskPlan,
    SubtaskResult,
    SubtaskRuntimeState,
)
from reforge.memory.substrate import MemorySubstrate
from reforge.runtime.orchestration.decomposition.runner import SubtaskRunner
from reforge.runtime.infrastructure.trajectory.store import TrajectoryStore

_MAX_CONTEXT_CHARS = 400  # Truncate injected context to avoid prompt bloat


def _group_by_levels(subtasks: list[SubtaskPlan]) -> list[list[SubtaskPlan]]:
    """Topological sort — group subtasks into parallel execution levels.

    Each level contains subtasks whose dependencies are all in earlier levels.
    Subtasks within the same level are independent and can run concurrently.
    Falls back to sequential on invalid dependency graphs (cycles, missing indices).
    """
    completed: set[int] = set()
    remaining = list(subtasks)
    levels: list[list[SubtaskPlan]] = []

    # Guard against cycles: at most len(subtasks) iterations
    for _ in range(len(subtasks) + 1):
        if not remaining:
            break
        ready = [s for s in remaining if all(d in completed for d in s.depends_on)]
        if not ready:
            # Cycle or invalid deps — take first item to break deadlock
            ready = remaining[:1]
        levels.append(ready)
        for s in ready:
            completed.add(s.index)
        remaining = [s for s in remaining if s not in ready]

    return levels


def _enrich_subtask(subtask: SubtaskPlan, completed: dict[int, SubtaskResult]) -> SubtaskPlan:
    """Return a copy of subtask with dependency results injected into the request.

    Returns the original subtask unchanged when it has no dependencies or
    when no completed dependency has a non-empty final_answer.
    """
    if not subtask.depends_on:
        return subtask

    context_lines: list[str] = []
    for dep_idx in subtask.depends_on:
        dep = completed.get(dep_idx)
        if dep and dep.final_answer:
            snippet = dep.final_answer[:_MAX_CONTEXT_CHARS]
            context_lines.append(f"[Step {dep_idx + 1} result]: {snippet}")

    if not context_lines:
        return subtask

    enriched_request = (
        f"{subtask.request}\n\nContext from previous steps:\n" + "\n".join(context_lines)
    )
    return SubtaskPlan(
        index=subtask.index,
        request=enriched_request,
        description=subtask.description,
        depends_on=subtask.depends_on,
    )


class AsyncSubtaskRunner:
    """Runs subtasks with parallelism where dependency graph allows it.

    Sequential levels (single subtask) run inline.
    Parallel levels (multiple independent subtasks) use a thread pool.
    Context from completed subtasks is propagated to dependents before execution.
    """

    def __init__(
        self,
        trajectory_store: TrajectoryStore | None = None,
        memory_substrate: MemorySubstrate | None = None,
        max_workers: int = 4,
    ) -> None:
        self._sync_runner = SubtaskRunner(
            trajectory_store=trajectory_store,
            memory_substrate=memory_substrate,
        )
        self._max_workers = max_workers

    def run_all(self, decomposition: DecompositionResult) -> MultiStepResult:
        """Execute all subtasks respecting dependency order, parallelising where possible."""
        levels = _group_by_levels(decomposition.subtasks)
        completed_states: dict[int, SubtaskRuntimeState] = {}

        for level in levels:
            # Build lightweight result dict for dependency enrichment (needs final_answer only).
            completed_results: dict[int, SubtaskResult] = {
                idx: srs.to_result() for idx, srs in completed_states.items()
            }

            if len(level) == 1:
                subtask = level[0]
                enriched = _enrich_subtask(subtask, completed_results)
                completed_states[subtask.index] = self._sync_runner.run_one(enriched)
            else:
                workers = min(len(level), self._max_workers)
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {
                        executor.submit(
                            self._sync_runner.run_one,
                            _enrich_subtask(subtask, completed_results),
                        ): subtask.index
                        for subtask in level
                    }
                    for future in as_completed(futures):
                        idx = futures[future]
                        try:
                            completed_states[idx] = future.result()
                        except Exception as exc:
                            subtask = next(s for s in level if s.index == idx)
                            logger.exception(
                                "Subtask %d (%s) raised in AsyncSubtaskRunner",
                                idx,
                                subtask.description or subtask.request[:60],
                            )
                            completed_states[idx] = SubtaskRuntimeState(
                                subtask=subtask,
                                session_id="",
                                state=None,
                                duration_ms=0.0,
                                error=f"{type(exc).__name__}: {exc}",
                            )

        ordered = [
            completed_states[s.index].to_result()
            for s in decomposition.subtasks
            if s.index in completed_states
        ]
        return MultiStepResult.from_results(decomposition.original_request, ordered)

    @property
    def has_parallel_levels(self) -> bool:
        """Utility for callers to detect if any level has 2+ subtasks."""
        return False  # computed per-decomposition; use _group_by_levels directly
