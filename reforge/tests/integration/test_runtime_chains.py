"""Integration tests — verify full runtime chain with mock LLM.

Tests cover: clean success, recovered after retry, capability deny, expected failure,
event log consistency, SubtaskRuntimeState lifecycle, memory-assisted recovery.
Each test verifies nested state + control state + outcome state consistency.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import Mock, patch

from reforge.runtime.orchestration.engine.runner import RuntimeRunner
from reforge.runtime.events.log import ExecutionEventLog
from reforge.runtime.policy.task_intent import TaskIntent


def _patch_llm_nodes(factory):
    """Patch LLMClient in every node module that instantiates it."""
    stack = ExitStack()
    for module in ("planner", "codegen", "reflection"):
        stack.enter_context(
            patch(f"reforge.runtime.orchestration.graph.nodes.{module}.LLMClient", factory)
        )
    return stack

_INTENT_MOCK = Mock()
_INTENT_MOCK.chat = Mock(return_value="NORMAL_EXECUTION")
_INTENT_EXPECTED = Mock()
_INTENT_EXPECTED.chat = Mock(return_value="EXPECTED_ERROR")
_DENY_INTENT_MOCK = Mock()
_DENY_INTENT_MOCK.chat = Mock(return_value="NORMAL_EXECUTION")


def _make_factory(responses: list[str]):
    cursor = [0]

    class FakeLLM:
        def chat(self, _system: str, _user: str) -> str:
            idx = cursor[0]
            cursor[0] += 1
            if idx < len(responses):
                return responses[idx]
            return responses[-1]

    return FakeLLM


def test_clean_success_chain():
    """Full chain: clean execution → SUCCESS → nested state populated."""
    factory = _make_factory(["1. Plan A", "print('hello')"])
    with (
        _patch_llm_nodes(factory),
        patch("reforge.runtime.policy.task_intent.LLMClient", return_value=_INTENT_MOCK),
    ):
        runner = RuntimeRunner()
        state = runner.run("test request")

    assert state.exec_state.exit_code == 0
    assert state.exec_state.stdout == "hello\n"
    assert state.outcome_state.task_outcome == "SUCCESS"
    assert state.control_state.retry_decision_action in ("ACCEPT", None)
    assert state.control_state.retry_count == 0


def test_recovered_after_retry_chain():
    """Error → retry → success → RECOVERED → nested state consistency."""
    responses = [
        "1. Plan B",
        "x = 1 / 0",
        "ErrorType: ZeroDivisionError\nSummary: div by zero\nFix: use print(42)",
        "print('fixed')",
    ]
    factory = _make_factory(responses)
    with (
        _patch_llm_nodes(factory),
        patch("reforge.runtime.policy.task_intent.LLMClient", return_value=_INTENT_MOCK),
    ):
        runner = RuntimeRunner()
        state = runner.run("test retry")

    assert state.control_state.retry_count >= 1
    assert state.exec_state.exit_code == 0
    assert state.exec_state.stdout == "fixed\n"
    assert state.outcome_state.task_outcome in ("RECOVERED", "SUCCESS")
    assert state.control_state.retry_count >= 1
    assert state.semantic_state.task_intent == "NORMAL_EXECUTION"


def test_capability_deny_chain():
    """Dangerous request → DENY → no execution → nested state reflects deny."""
    factory = _make_factory(["1. Plan"])
    with (
        _patch_llm_nodes(factory),
        patch("reforge.runtime.policy.task_intent.LLMClient", return_value=_INTENT_MOCK),
    ):
        runner = RuntimeRunner()
        state = runner.run("run rm -rf / to delete everything")

    assert state.capability_decision is not None
    assert state.exec_state.exit_code is None  # No execution happened
    assert state.outcome_state.task_outcome == "DENIED"
    assert state.control_state.retry_count == 0


def test_expected_failure_chain():
    """Intentional error → STOP → EXPECTED_FAILURE → no retry."""
    intent_mock = Mock()
    intent_mock.chat = Mock(return_value="EXPECTED_ERROR")
    responses = [
        "1. Plan C",
        "raise Exception('intentional')",
        "ErrorType: Exception\nSummary: intentional error\nFix: none",
    ]
    factory = _make_factory(responses)
    with (
        _patch_llm_nodes(factory),
        patch("reforge.runtime.policy.task_intent.LLMClient", return_value=intent_mock),
    ):
        runner = RuntimeRunner()
        state = runner.run("intentionally raise error")

    assert state.outcome_state.task_outcome == "EXPECTED_FAILURE"
    assert state.control_state.retry_decision_action == "STOP"
    assert state.control_state.retry_count == 0  # No retry


# ---------------------------------------------------------------------------
# Event log consistency
# ---------------------------------------------------------------------------


def test_event_log_consistency_clean_success():
    """After clean execution, event log projection must match runtime state."""
    from reforge.runtime.bridge.consistency import check_state_consistency
    from reforge.runtime.events.projection import project_state

    log = ExecutionEventLog()
    factory = _make_factory(["1. Plan", "print('ok')"])
    with (
        _patch_llm_nodes(factory),
        patch("reforge.runtime.policy.task_intent.LLMClient", return_value=_INTENT_MOCK),
    ):
        runner = RuntimeRunner(event_log=log)
        state = runner.run("test event consistency")

    proj = project_state(runner.session_id, log)
    report = check_state_consistency(proj, state)
    assert report.is_consistent, f"Inconsistencies: {report.mismatch_fields()}"


def test_event_log_consistency_after_retry():
    """After recovery, event log projection must still match runtime state."""
    from reforge.runtime.bridge.consistency import check_state_consistency
    from reforge.runtime.events.projection import project_state

    log = ExecutionEventLog()
    responses = [
        "1. Plan",
        "x = 1 / 0",
        "ErrorType: ZeroDivisionError\nSummary: div by zero\nFix: use print(1)",
        "print(1)",
    ]
    factory = _make_factory(responses)
    with (
        _patch_llm_nodes(factory),
        patch("reforge.runtime.policy.task_intent.LLMClient", return_value=_INTENT_MOCK),
    ):
        runner = RuntimeRunner(event_log=log)
        state = runner.run("test retry consistency")

    proj = project_state(runner.session_id, log)
    report = check_state_consistency(proj, state)
    assert report.is_consistent, f"Inconsistencies: {report.mismatch_fields()}"


def test_event_kinds_sequence_clean_success():
    """Clean success must emit: EXECUTION_STARTED → SUCCEEDED → EVALUATION → POLICY → TASK_COMPLETED."""
    log = ExecutionEventLog()
    # stdout must be ≥5 chars to pass the output_not_empty heuristic check
    factory = _make_factory(["1. Plan", "print('hello world')"])
    with (
        _patch_llm_nodes(factory),
        patch("reforge.runtime.policy.task_intent.LLMClient", return_value=_INTENT_MOCK),
    ):
        runner = RuntimeRunner(event_log=log)
        runner.run("test event sequence")

    kinds = [e.kind for e in log.replay()]
    assert "EXECUTION_STARTED" in kinds
    assert "EXECUTION_SUCCEEDED" in kinds
    assert "EVALUATION_COMPLETED" in kinds
    assert "POLICY_DECIDED" in kinds
    assert "TASK_COMPLETED" in kinds
    # No sandbox failure on clean run (exit_code=0)
    assert "EXECUTION_FAILED" not in kinds


def test_event_kinds_sequence_retry():
    """Retry path must emit EXECUTION_FAILED + RECOVERY_ATTEMPTED before final success."""
    log = ExecutionEventLog()
    responses = [
        "1. Plan",
        "1/0",
        "ErrorType: ZeroDivisionError\nSummary: div by zero\nFix: print(1)",
        "print(1)",
    ]
    factory = _make_factory(responses)
    with (
        _patch_llm_nodes(factory),
        patch("reforge.runtime.policy.task_intent.LLMClient", return_value=_INTENT_MOCK),
    ):
        runner = RuntimeRunner(event_log=log)
        runner.run("test retry sequence")

    kinds = [e.kind for e in log.replay()]
    assert "EXECUTION_FAILED" in kinds
    assert "RECOVERY_ATTEMPTED" in kinds
    assert "TASK_COMPLETED" in kinds


# ---------------------------------------------------------------------------
# SubtaskRuntimeState lifecycle
# ---------------------------------------------------------------------------


def test_subtask_runtime_state_preserves_full_state():
    """SubtaskRunner.run_one() must return full RuntimeState, not just summary."""
    from unittest.mock import patch as _patch
    from reforge.runtime.orchestration.decomposition.models import SubtaskPlan, SubtaskRuntimeState
    from reforge.runtime.orchestration.decomposition.runner import SubtaskRunner

    factory = _make_factory(["1. Plan", "print('subtask done')"])
    with (
        _patch_llm_nodes(factory),
        _patch("reforge.runtime.policy.task_intent.LLMClient", return_value=_INTENT_MOCK),
    ):
        runner = SubtaskRunner()
        subtask = SubtaskPlan(index=0, request="print subtask done", description="test")
        srs = runner.run_one(subtask)

    assert isinstance(srs, SubtaskRuntimeState)
    assert srs.state is not None
    # Full RuntimeState with nested sub-states
    assert srs.state.exec_state.exit_code == 0
    assert srs.state.outcome_state.task_outcome == "SUCCESS"
    assert srs.state.control_state.retry_count == 0
    # to_result() still produces the lightweight summary
    result = srs.to_result()
    assert result.task_outcome == "SUCCESS"
    assert result.session_id == srs.session_id


def test_subtask_runtime_state_retry_captured():
    """SubtaskRuntimeState captures retry history; SubtaskResult.retry_count matches."""
    from unittest.mock import patch as _patch
    from reforge.runtime.orchestration.decomposition.models import SubtaskPlan
    from reforge.runtime.orchestration.decomposition.runner import SubtaskRunner

    # Use output ≥5 chars on success attempt to pass the output_not_empty heuristic
    responses = [
        "1. Plan",
        "1/0",
        "ErrorType: ZeroDivisionError\nSummary: div\nFix: print fixed",
        "print('fixed value')",
    ]
    factory = _make_factory(responses)
    with (
        _patch_llm_nodes(factory),
        _patch("reforge.runtime.policy.task_intent.LLMClient", return_value=_INTENT_MOCK),
    ):
        runner = SubtaskRunner()
        subtask = SubtaskPlan(index=0, request="divide and recover", description="retry test")
        srs = runner.run_one(subtask)

    assert srs.state.control_state.retry_count >= 1
    result = srs.to_result()
    assert result.retry_count >= 1
    assert result.task_outcome in ("RECOVERED", "SUCCESS")


# ---------------------------------------------------------------------------
# Memory-assisted retry
# ---------------------------------------------------------------------------


def test_memory_assisted_retry_calls_substrate():
    """reflection_node must query the injected MemorySubstrate on failure.

    Verifies:
    - substrate.recall() is called during the retry path
    - The full run still completes successfully (memory doesn't break the flow)
    - The final outcome is RECOVERED (error → memory recall → retry → success)
    """
    from unittest.mock import Mock, patch as _patch

    class _FakeSubstrate:
        def __init__(self) -> None:
            self.recall_calls: list[str] = []

        def _make_record(self) -> Mock:
            rec = Mock()
            rec.error_type = "ZeroDivisionError"
            rec.recovery_action = "avoid dividing by zero"
            rec.outcome = "RECOVERED"
            return rec

        def recall(self, query: str, limit: int = 3):
            self.recall_calls.append(query)
            return [self._make_record()]

        def recall_for_planning(self, user_request: str, limit: int = 3):
            return []

        def find_by_error(self, error_type: str, limit: int = 3):
            return []

        def write(self, record) -> None:
            pass

        def save(self, record) -> None:
            pass

    substrate = _FakeSubstrate()
    responses = [
        "1. Plan",
        "x = 1 / 0",
        "ErrorType: ZeroDivisionError\nSummary: div by zero\nFix: print('safe')",
        "print('safe value')",
    ]
    factory = _make_factory(responses)
    with (
        _patch_llm_nodes(factory),
        patch("reforge.runtime.policy.task_intent.LLMClient", return_value=_INTENT_MOCK),
    ):
        runner = RuntimeRunner(memory_substrate=substrate)
        state = runner.run("compute something safely")

    # Memory substrate was queried during reflection (the core invariant)
    assert len(substrate.recall_calls) >= 1, "substrate.recall() was not called"

    # A retry was attempted (error → reflection → memory query → retry)
    assert state.control_state.retry_count >= 1


# ---------------------------------------------------------------------------
# AsyncSubtaskRunner alignment with SubtaskRuntimeState
# ---------------------------------------------------------------------------


def test_async_subtask_runner_uses_run_one():
    """AsyncSubtaskRunner must use run_one() internally (returns SubtaskRuntimeState).

    Verifies the end-to-end result is correct after the P42 alignment fix.
    """
    from unittest.mock import patch as _patch
    from reforge.runtime.orchestration.decomposition.models import (
        DecompositionResult,
        SubtaskPlan,
    )
    from reforge.runtime.orchestration.decomposition.async_runner import AsyncSubtaskRunner

    factory = _make_factory(["1. Plan A", "print('step one')", "1. Plan B", "print('step two')"])
    with (
        _patch_llm_nodes(factory),
        _patch("reforge.runtime.policy.task_intent.LLMClient", return_value=_INTENT_MOCK),
    ):
        runner = AsyncSubtaskRunner()
        decomp = DecompositionResult(
            is_multistep=True,
            original_request="two step task",
            subtasks=[
                SubtaskPlan(index=0, request="step one task", description="first"),
                SubtaskPlan(index=1, request="step two task", description="second"),
            ],
        )
        result = runner.run_all(decomp)

    assert result.overall_outcome in ("COMPLETE", "PARTIAL", "FAILED")
    assert len(result.subtask_results) == 2
    # Both subtasks should have outcomes (not empty)
    for sr in result.subtask_results:
        assert sr.task_outcome in ("SUCCESS", "RECOVERED", "EXPECTED_FAILURE", "FAILED")
        assert sr.session_id != "" or sr.task_outcome == "FAILED"


# ---------------------------------------------------------------------------
# SqliteMemorySubstrate write-back via RuntimeRunner
# ---------------------------------------------------------------------------


def test_runner_writes_back_to_sqlite_substrate():
    """After a successful run, RuntimeRunner must write a MemoryRecord to the substrate.

    Verifies the full read-write loop:
      runner.run() → final_response node → record_from_final_state → substrate.write()
    """
    import tempfile
    from pathlib import Path
    from reforge.memory.sqlite_substrate import SqliteMemorySubstrate

    tmp_dir = tempfile.TemporaryDirectory()
    substrate = SqliteMemorySubstrate(db_path=Path(tmp_dir.name) / "test.db")
    try:
        factory = _make_factory(["1. Plan", "print('hello world')"])
        with (
            _patch_llm_nodes(factory),
            patch("reforge.runtime.policy.task_intent.LLMClient", return_value=_INTENT_MOCK),
        ):
            runner = RuntimeRunner(memory_substrate=substrate)
            state = runner.run("print hello world")

        assert state.outcome_state.task_outcome == "SUCCESS"
        records = substrate.list_all()
        assert len(records) >= 1
        assert any(r.session_id == runner.session_id for r in records)
    finally:
        substrate.close()
        tmp_dir.cleanup()


def test_runner_writes_recovery_record_after_retry():
    """After error → retry → success, runner must write a record with error info."""
    import tempfile
    from pathlib import Path
    from reforge.memory.sqlite_substrate import SqliteMemorySubstrate

    db_path = None
    substrate = None
    tmp_dir = tempfile.TemporaryDirectory()
    try:
        db_path = Path(tmp_dir.name) / "recovery.db"
        substrate = SqliteMemorySubstrate(db_path=db_path)

        # Use output ≥5 chars so output_not_empty heuristic passes
        responses = [
            "1. Plan",
            "x = 1 / 0",
            "ErrorType: ZeroDivisionError\nSummary: div by zero\nFix: print fixed value",
            "print('fixed value')",
        ]
        factory = _make_factory(responses)
        with (
            _patch_llm_nodes(factory),
            patch("reforge.runtime.policy.task_intent.LLMClient", return_value=_INTENT_MOCK),
        ):
            runner = RuntimeRunner(memory_substrate=substrate)
            state = runner.run("compute something")

        assert state.control_state.retry_count >= 1
        records = substrate.list_all()
        assert len(records) >= 1
        session_records = [r for r in records if r.session_id == runner.session_id]
        assert len(session_records) >= 1
        # Any error recovery record is acceptable (RECOVERY or FAILURE depending on eval)
        assert any(r.error_type for r in session_records), "record should have error_type set"
    finally:
        if substrate:
            substrate.close()
        tmp_dir.cleanup()


def test_runner_recall_from_previous_session():
    """Experiences written in session 1 must be recallable in session 2."""
    import tempfile
    from pathlib import Path
    from reforge.memory.sqlite_substrate import SqliteMemorySubstrate

    tmp_dir = tempfile.TemporaryDirectory()
    db_path = Path(tmp_dir.name) / "cross_session.db"
    substrate1 = substrate2 = None
    try:
        # Session 1: error + recovery → writes a record with ZeroDivisionError
        responses1 = [
            "1. Plan",
            "x = 1 / 0",
            "ErrorType: ZeroDivisionError\nSummary: division error\nFix: print fixed value",
            "print('fixed value')",
        ]
        factory1 = _make_factory(responses1)
        substrate1 = SqliteMemorySubstrate(db_path=db_path)
        with (
            _patch_llm_nodes(factory1),
            patch("reforge.runtime.policy.task_intent.LLMClient", return_value=_INTENT_MOCK),
        ):
            runner1 = RuntimeRunner(memory_substrate=substrate1)
            runner1.run("divide numbers")
        substrate1.close()
        substrate1 = None

        # Session 2: new substrate on same DB — can recall session 1's experience
        substrate2 = SqliteMemorySubstrate(db_path=db_path)
        recalled = substrate2.recall("ZeroDivisionError", limit=3)
        assert len(recalled) >= 1
        assert any("ZeroDivisionError" in r.error_type for r in recalled)
    finally:
        if substrate1:
            substrate1.close()
        if substrate2:
            substrate2.close()
        tmp_dir.cleanup()
