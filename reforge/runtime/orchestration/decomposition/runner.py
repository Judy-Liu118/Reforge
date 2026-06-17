"""SubtaskRunner — executes a decomposed multi-step task.

Each subtask runs through a full RuntimeRunner (sandbox + retry loop).
Results are aggregated into a MultiStepResult.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

from reforge.runtime.orchestration.decomposition.models import (
    DecompositionResult,
    MultiStepResult,
    SubtaskPlan,
    SubtaskResult,
    SubtaskRuntimeState,
)
from reforge.memory.substrate import MemorySubstrate
from reforge.runtime.orchestration.engine.runner import RuntimeRunner
from reforge.runtime.domain.state.models import RuntimeState
from reforge.runtime.infrastructure.trajectory.store import TrajectoryStore

NodeName = str


class SubtaskRunner:
    """Runs multiple subtasks sequentially through the full execution loop.

    Each subtask gets its own RuntimeRunner instance with independent retry,
    reflection, and policy evaluation. The trajectory_store is shared so all
    subtask arcs land in the same JSONL file for future recall.
    """

    def __init__(
        self,
        trajectory_store: TrajectoryStore | None = None,
        memory_substrate: MemorySubstrate | None = None,
    ) -> None:
        self._trajectory_store = trajectory_store
        self._memory_substrate = memory_substrate

    def run_all(self, decomposition: DecompositionResult) -> MultiStepResult:
        """Run all subtasks and return aggregated result. Blocking."""
        results: list[SubtaskResult] = []
        for subtask in decomposition.subtasks:
            srs = self.run_one(subtask)
            results.append(srs.to_result())
        return MultiStepResult.from_results(decomposition.original_request, results)

    def stream_all(
        self, decomposition: DecompositionResult,
    ) -> Iterator[tuple[int, NodeName, RuntimeState]]:
        """Stream (subtask_index, node_name, state) tuples for all subtasks.

        Allows the CLI to display per-node progress across all steps,
        identical to the single-task streaming experience.
        """
        for subtask in decomposition.subtasks:
            runner = RuntimeRunner(
                trajectory_store=self._trajectory_store,
                memory_substrate=self._memory_substrate,
            )
            for node_name, state in runner.stream(subtask.request):
                yield subtask.index, node_name, state

    def run_one(self, subtask: SubtaskPlan) -> SubtaskRuntimeState:
        """Run a single subtask and return its full lifecycle record."""
        start = time.monotonic()
        runner = RuntimeRunner(
            trajectory_store=self._trajectory_store,
            memory_substrate=self._memory_substrate,
        )
        final_state: RuntimeState | None = None
        for _node, state in runner.stream(subtask.request):
            final_state = state
        elapsed_ms = (time.monotonic() - start) * 1000
        return SubtaskRuntimeState(
            subtask=subtask,
            session_id=runner.session_id,
            state=final_state,
            duration_ms=elapsed_ms,
        )

    def _run_one(self, subtask: SubtaskPlan) -> SubtaskResult:
        """Deprecated: use run_one() which returns SubtaskRuntimeState."""
        return self.run_one(subtask).to_result()
