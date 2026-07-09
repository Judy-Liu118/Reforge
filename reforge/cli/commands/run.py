"""CLI run command — single-task and multi-step execution."""

from __future__ import annotations


from reforge.cli.case_loader import list_cases
from reforge.cli.events import DEFAULT_EVENT_LOG_PATH
from reforge.cli.formatter import (
    format_code,
    format_header,
    format_multistep_header,
    format_stdout_tail,
    format_multistep_summary,
    format_node,
    format_result,
    format_subtask_header,
    format_summary,
    format_traceback,
)
from reforge.cli.progress import ProgressPrinter
from reforge.cli.research import is_research_question, run_research
from reforge.memory.sqlite_substrate import SqliteMemorySubstrate
from reforge.memory.writer import record_from_final_state
from reforge.observability.tracing.storage import save_trace
from reforge.runtime.orchestration.decomposition import TaskDecomposer
from reforge.runtime.orchestration.decomposition.models import DecompositionResult, SubtaskResult
from reforge.runtime.orchestration.engine.runner import RuntimeRunner
from reforge.runtime.events.persistent_log import PersistentEventLog
from reforge.runtime.infrastructure.history.models import SessionRecord
from reforge.runtime.infrastructure.history.storage import HistoryStorage
from reforge.runtime.infrastructure.trajectory.store import TrajectoryStore


def run_multistep_task(
    decomposition: DecompositionResult,
    case_meta: str = "",
    conversation_id: str | None = None,
) -> None:
    """Execute a decomposed multi-step task."""
    from reforge.runtime.orchestration.decomposition.async_runner import (
        AsyncSubtaskRunner,
        _enrich_subtask,
        _group_by_levels,
    )

    n = len(decomposition.subtasks)
    traj_store = TrajectoryStore()
    substrate = SqliteMemorySubstrate()
    levels = _group_by_levels(decomposition.subtasks)
    has_parallel = any(len(lv) > 1 for lv in levels)
    completed_results: dict[int, SubtaskResult] = {}

    print(format_multistep_header(decomposition.original_request, n, has_parallel))
    if case_meta:
        print(f"  {case_meta}")

    subtask_states: list = []
    subtask_session_ids: list[str] = []

    for level in levels:
        if len(level) > 1:
            parallel_labels = ", ".join(
                f"Step {s.index + 1}" + (f" ({s.description})" if s.description else "")
                for s in level
            )
            print(f"\n  [ Parallel ] {parallel_labels}")
            async_runner = AsyncSubtaskRunner(trajectory_store=traj_store, memory_substrate=substrate)
            parallel_decomp = DecompositionResult(
                is_multistep=True,
                subtasks=level,
                original_request=decomposition.original_request,
            )
            ms_result = async_runner.run_all(parallel_decomp)
            for sr in ms_result.subtask_results:
                print(format_subtask_header(sr.subtask.index, n, sr.subtask.description, sr.subtask.request))
                outcome_line = f"  [Outcome] {sr.task_outcome}"
                if sr.final_answer:
                    outcome_line += f"  |  {sr.final_answer[:120]}"
                print(outcome_line)
                completed_results[sr.subtask.index] = sr
                subtask_session_ids.append(sr.session_id)
        else:
            subtask = level[0]
            enriched = _enrich_subtask(subtask, completed_results)
            print(format_subtask_header(subtask.index, n, subtask.description, subtask.request))

            runner = RuntimeRunner(
                trajectory_store=traj_store,
                event_log=PersistentEventLog(DEFAULT_EVENT_LOG_PATH),
                memory_substrate=substrate,
                conversation_id=conversation_id,
            )
            state = None
            for node_name, state in runner.stream(enriched.request):
                line = format_node(node_name, state)
                if line:
                    print(line)
                tb = format_traceback(state)
                if tb and node_name == "execution":
                    print(tb)
                if node_name == "execution":
                    tail = format_stdout_tail(state)
                    if tail:
                        print(tail)
                if node_name == "code_generation":
                    code = format_code(state)
                    if code:
                        print(code)

            if state is not None:
                print(format_summary(state))
                print(format_result(state))
                subtask_states.append(state)
                subtask_session_ids.append(runner.session_id)
                HistoryStorage().save(SessionRecord.from_state(state, session_id=runner.session_id))

                os_ = state.outcome_state
                completed_results[subtask.index] = SubtaskResult(
                    subtask=subtask,
                    task_outcome=os_.task_outcome or "FAILED",
                    final_answer=os_.final_answer,
                    retry_count=state.control_state.retry_count,
                    session_id=runner.session_id,
                )

    success_outcomes = {"SUCCESS", "RECOVERED", "EXPECTED_FAILURE"}
    subtask_outcomes = [
        (s.outcome_state.task_outcome or "FAILED") for s in subtask_states
    ]
    all_ok = all(o in success_outcomes for o in subtask_outcomes)
    any_ok = any(o in success_outcomes for o in subtask_outcomes)
    overall = "COMPLETE" if all_ok else ("PARTIAL" if any_ok else "FAILED")
    total_ms = sum(s.exec_state.duration_ms or 0.0 for s in subtask_states)
    print(format_multistep_summary(overall, subtask_outcomes, total_ms))

    traj_store.save_multistep(
        original_request=decomposition.original_request,
        subtask_session_ids=subtask_session_ids,
        subtask_outcomes=subtask_outcomes,
        subtask_descriptions=[s.description for s in decomposition.subtasks],
        overall_outcome=overall,
        total_attempts=sum(len(s.attempts) for s in subtask_states),
    )


def run_task(
    user_request: str,
    case_meta: str = "",
    conversation_id: str | None = None,
) -> None:
    """Execute a single task, print the runtime trace, and persist the session."""
    if is_research_question(user_request):
        run_research(user_request)
        return

    decomposer = TaskDecomposer()
    decomposition = decomposer.decompose(user_request)
    if decomposition.is_multistep:
        run_multistep_task(decomposition, case_meta, conversation_id=conversation_id)
        return

    event_log = PersistentEventLog(DEFAULT_EVENT_LOG_PATH)
    printer = ProgressPrinter(event_log)
    substrate = SqliteMemorySubstrate()
    runner = RuntimeRunner(
        event_log=event_log,
        memory_substrate=substrate,
        conversation_id=conversation_id,
    )
    storage = HistoryStorage()

    print(format_header(user_request))
    if case_meta:
        print(f"  {case_meta}")
    print()

    state = None
    for node_name, state in runner.stream(user_request):
        line = format_node(node_name, state)
        if line:
            print(line)
        tb = format_traceback(state)
        if tb and node_name == "execution":
            print(tb)
        if node_name == "code_generation":
            code = format_code(state)
            if code:
                print(code)

    printer.stop()

    if state is None:
        return

    print(format_summary(state))
    print(format_result(state))

    sid = runner.session_id
    record = SessionRecord.from_state(state, session_id=sid)
    storage.save(record)

    if runner.collector:
        save_trace(runner.collector)

    # Memory write-back is handled by the runner automatically.
    # Derive the tag for display from the same helper (no duplicate write).
    mem_record = record_from_final_state(state, sid)
    mem_tag = (
        f"  [memory: {mem_record.memory_type.value}]"
        if mem_record is not None
        else "  [memory: skipped]"
    )

    print(f"  [saved: {sid}]  [trace: runs/{sid}/trace.json]{mem_tag}")


def handle_list() -> None:
    cases = list_cases()
    if not cases:
        print("No runtime cases found.")
        return
    print(f"{'Case':<24} {'Category':<16} Expected")
    print("-" * 78)
    for c in sorted(cases, key=lambda x: (x.category, x.name)):
        print(f"  {c.name:<22} {c.category:<16} {c.expected_behavior}")
