from __future__ import annotations

import uuid
from collections.abc import Iterator

from reforge.config import config
from reforge.memory.substrate import CompositeMemorySubstrate, MemorySubstrate
from reforge.memory.writer import record_from_final_state
from reforge.observability.tracing.collector import TraceCollector
from reforge.runtime.events.log import ExecutionEventLog
from reforge.runtime.events.models import ExecutionContext
from reforge.runtime.orchestration.graph.workflow import build_graph
from reforge.runtime.domain.state.models import RuntimeState
from reforge.runtime.infrastructure.trajectory.models import TrajectoryRecord
from reforge.runtime.infrastructure.trajectory.store import TrajectoryStore

NodeName = str


class RuntimeRunner:
    """Entry point for the self-healing runtime loop.

    Uses LangGraph streaming to yield node-level execution events,
    so observers (CLI, loggers, trace) can hook in without touching graph nodes.
    Pass trajectory_store to persist per-session semantic arcs for future recall.
    Pass memory_substrate to swap memory backend (default: CompositeMemorySubstrate).
    Pass event_log to collect structured ExecutionEvents across the lifecycle.
    """

    def __init__(
        self,
        trajectory_store: TrajectoryStore | None = None,
        memory_substrate: MemorySubstrate | None = None,
        event_log: ExecutionEventLog | None = None,
        conversation_id: str | None = None,
    ) -> None:
        self._session_id = uuid.uuid4().hex[:8]
        # One ExecutionContext per runner — its trace_id stamps every emitted
        # event so the dashboard can group multi-session investigations back to
        # the originating request.
        self._context = ExecutionContext.new(self._session_id)
        # conversation_id groups multiple tasks from one REPL session together.
        # Falls back to per-task session_id when running in single-shot mode.
        self._conversation_id = conversation_id or self._session_id
        # Always maintain an active log so emitter overrides are never skipped.
        self._event_log = event_log if event_log is not None else ExecutionEventLog()
        # Materialise substrate once so graph nodes (read) and write-back (write)
        # share the same instance — especially important for SQLite where the
        # connection is per-instance.
        self._memory_substrate: MemorySubstrate = (
            memory_substrate if memory_substrate is not None else CompositeMemorySubstrate()
        )
        self._graph = build_graph(
            memory_substrate=self._memory_substrate,
            event_log=self._event_log,
            context=self._context,
        )
        self._collector: TraceCollector | None = None
        self._trajectory_store = trajectory_store

    def run(self, user_request: str) -> RuntimeState:
        """Run the full workflow and return the final state."""
        final_state: RuntimeState | None = None
        for _node_name, state in self.stream(user_request):
            final_state = state
        if final_state is None:
            raise RuntimeError("Graph produced no output")
        return final_state

    def stream(
        self, user_request: str, collector: TraceCollector | None = None,
    ) -> Iterator[tuple[NodeName, RuntimeState]]:
        """Stream node executions, yielding (node_name, full_state) after each node.

        An optional TraceCollector receives structured events for each node.
        """
        if collector is None:
            collector = TraceCollector(session_id=self._session_id)
        self._collector = collector

        initial = RuntimeState(user_request=user_request)
        current = initial

        for chunk in self._graph.stream(initial, stream_mode="updates"):
            for node_name, node_update in chunk.items():
                if node_update:
                    merged = current.model_dump() | node_update
                    current = RuntimeState.model_validate(merged)
                collector.on_node(node_name, current)
                if node_name == "final_response":
                    if self._trajectory_store:
                        record = TrajectoryRecord.from_final_state(current, self._session_id)
                        self._trajectory_store.save(record)
                    mem_record = record_from_final_state(current, self._conversation_id)
                    if mem_record is not None:
                        self._memory_substrate.write(mem_record)
                yield node_name, current

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def context(self) -> ExecutionContext:
        return self._context

    @property
    def collector(self) -> TraceCollector | None:
        return self._collector

    @property
    def event_log(self) -> ExecutionEventLog:
        return self._event_log

    @property
    def conversation_id(self) -> str:
        return self._conversation_id

    @property
    def memory_substrate(self) -> MemorySubstrate:
        return self._memory_substrate
