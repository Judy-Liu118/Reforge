"""End-to-end contract for the memory → repair_hint → retry-prompt loop.

The headline self-heal claim is a *loop*, and every joint gets pinned here:

  write side:  failing attempt → reflection snapshots the failure →
               governor stamps failure_mode → RECOVERED session persists
               (problem_signature → repair_strategy) to ExecutionMemory
  read side:   next session fails similarly → ClassifyStage recalls the
               record via fingerprint scoring → repair_hint lands on
               semantic_state → the retry codegen prompt contains it

Uses mock LLMs + the real subprocess sandbox (same pattern as
test_runtime_chains). Intent classification is mocked separately.
"""

from __future__ import annotations

import json
from contextlib import ExitStack
from unittest.mock import Mock, patch

from reforge.memory.execution_memory import ExecutionMemory
from reforge.memory.writer import execution_record_from_final_state
from reforge.paths import execution_memory_path
from reforge.runtime.orchestration.engine.runner import RuntimeRunner
from reforge.runtime.orchestration.retry_context import RetryContextData, build_retry_prompt
from reforge.runtime.domain.state.models import (
    ExecutionState,
    FailureSnapshot,
    OutcomeState,
    RuntimeState,
    SemanticState,
)

_BAD_CODE = "import nonexistent_module_xyz_reforge_test\n"
_GOOD_CODE = "print('fixed')\n"
_REFLECTION_TEXT = (
    "ErrorType: ModuleNotFoundError\n"
    "Summary: import of a module that does not exist\n"
    "Fix: drop the bogus import and use stdlib only"
)


def _recording_factory(responses: list[str], prompts: list[tuple[str, str]]):
    """Shared-cursor fake LLM that also records every (system, user) prompt."""
    cursor = [0]

    class FakeLLM:
        def chat(self, system: str, user: str) -> str:
            prompts.append((system, user))
            idx = cursor[0]
            cursor[0] += 1
            return responses[idx] if idx < len(responses) else responses[-1]

    return FakeLLM


def _patch_llm_nodes(stack: ExitStack, factory) -> None:
    for module in ("planner", "codegen", "reflection"):
        stack.enter_context(
            patch(f"reforge.runtime.orchestration.graph.nodes.{module}.LLMClient", factory)
        )


def _intent_mock() -> Mock:
    llm = Mock()
    llm.chat = Mock(return_value="NORMAL_EXECUTION")
    return llm


def _run_fail_then_succeed(prompts: list[tuple[str, str]]) -> RuntimeState:
    """Drive one full RECOVERED session: bad import → retry → success."""
    factory = _recording_factory(
        ["plan: print something", _BAD_CODE, _REFLECTION_TEXT, _GOOD_CODE],
        prompts,
    )
    with ExitStack() as stack:
        _patch_llm_nodes(stack, factory)
        stack.enter_context(
            patch("reforge.runtime.policy.task_intent.LLMClient", return_value=_intent_mock())
        )
        runner = RuntimeRunner()
        return runner.run("print a fixed marker")


class TestWriteSide:
    def test_recovered_session_persists_execution_record(self) -> None:
        prompts: list[tuple[str, str]] = []
        state = _run_fail_then_succeed(prompts)

        assert state.outcome_state.task_outcome == "RECOVERED"
        path = execution_memory_path()
        assert path.exists(), "RECOVERED session must write ExecutionMemory"
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        assert len(records) == 1
        rec = records[0]
        assert rec["repair_strategy"] == "drop the bogus import and use stdlib only"
        assert rec["failure_mode"] == "execution_error"
        assert rec["error_type"] == "ModuleNotFoundError"
        # Structural fingerprint of the *failing* attempt, not the final success.
        assert rec["problem_signature"]["error_class"] == "ModuleNotFoundError"

    def test_clean_success_writes_nothing(self) -> None:
        prompts: list[tuple[str, str]] = []
        factory = _recording_factory(["plan", _GOOD_CODE], prompts)
        with ExitStack() as stack:
            _patch_llm_nodes(stack, factory)
            stack.enter_context(
                patch(
                    "reforge.runtime.policy.task_intent.LLMClient",
                    return_value=_intent_mock(),
                )
            )
            state = RuntimeRunner().run("print a fixed marker")
        assert state.outcome_state.task_outcome == "SUCCESS"
        assert not execution_memory_path().exists()

    def test_qualification_rules(self) -> None:
        snapshot = FailureSnapshot(
            error_type="KeyError",
            suggested_fix="use the actual column name",
            failure_mode="execution_error",
            problem_signature={"error_class": "KeyError"},
        )
        recovered = RuntimeState(
            user_request="task",
            exec_state=ExecutionState(exit_code=0, stdout="ok"),
            semantic_state=SemanticState(task_intent="NORMAL_EXECUTION", last_failure=snapshot),
            outcome_state=OutcomeState(task_outcome="RECOVERED"),
        )
        kwargs = execution_record_from_final_state(recovered)
        assert kwargs is not None and kwargs["repair_strategy"] == "use the actual column name"

        failed = recovered.model_copy(
            update={"outcome_state": OutcomeState(task_outcome="FAILED")}
        )
        assert execution_record_from_final_state(failed) is None

        no_fix = recovered.model_copy(
            update={
                "semantic_state": SemanticState(
                    last_failure=snapshot.model_copy(update={"suggested_fix": ""})
                )
            }
        )
        assert execution_record_from_final_state(no_fix) is None


class TestReadSide:
    def test_recalled_hint_reaches_retry_codegen_prompt(self) -> None:
        # Seed memory as if a prior session had recovered from the same failure.
        ExecutionMemory().record(
            request="print a fixed marker",
            outcome="RECOVERED",
            failure_mode="execution_error",
            retryable=True,
            repair_strategy="drop the bogus import and use stdlib only",
            task_intent="NORMAL_EXECUTION",
            problem_signature={
                "error_class": "ModuleNotFoundError",
                "root_cause": "missing_import",
                "missing_module": "nonexistent_module_xyz_reforge_test",
            },
            error_type="ModuleNotFoundError",
        )

        prompts: list[tuple[str, str]] = []
        state = _run_fail_then_succeed(prompts)

        assert state.outcome_state.task_outcome == "RECOVERED"
        # Prompt order: planner, codegen#1, reflection, codegen#2 (retry).
        retry_user_prompt = prompts[3][1]
        assert "Repair hint (from memory of similar past failures):" in retry_user_prompt
        assert "drop the bogus import and use stdlib only" in retry_user_prompt

    def test_hint_cleared_when_recall_is_empty(self) -> None:
        prompts: list[tuple[str, str]] = []
        state = _run_fail_then_succeed(prompts)
        retry_user_prompt = prompts[3][1]
        assert "Repair hint" not in retry_user_prompt
        # repair_hint stays None on state when nothing was recalled.
        assert state.semantic_state.repair_hint is None


class TestPromptRendering:
    def test_build_retry_prompt_renders_hint(self) -> None:
        ctx = RetryContextData(
            original_request="task",
            previous_code="x",
            repair_hint="use utf-8 encoding when reading the file",
            retry_reason="execution_error",
        )
        prompt = build_retry_prompt(ctx)
        assert "Repair hint (from memory of similar past failures):" in prompt
        assert "use utf-8 encoding when reading the file" in prompt

    def test_from_state_reads_semantic_repair_hint(self) -> None:
        state = RuntimeState(
            user_request="task",
            generated_code="x = 1",
            exec_state=ExecutionState(exit_code=1, stderr="Traceback...\nKeyError: 'Revenue'"),
            semantic_state=SemanticState(repair_hint="match the CSV header exactly"),
        )
        ctx = RetryContextData.from_state(state)
        assert ctx.repair_hint == "match the CSV header exactly"
