"""Reflection node — LLM-based traceback root-cause analysis.

Memory query is delegated to the MemorySubstrate Protocol. Callers can inject
a custom substrate (e.g. in tests); the default is CompositeMemorySubstrate.
"""

from __future__ import annotations

from reforge.memory.substrate import CompositeMemorySubstrate, MemorySubstrate
from reforge.models.adapters.llm_client import LLMClient
from reforge.models.prompts.templates import REFLECTION_SYSTEM
from reforge.runtime.domain.state.models import ReflectionResult, RuntimeState
from reforge.runtime.infrastructure.error_extraction import extract_error_type


def _format_memory_context(
    traceback: str, substrate: MemorySubstrate | None = None
) -> str:
    substrate = substrate or CompositeMemorySubstrate()
    error_type = extract_error_type(traceback)
    query = error_type or traceback.strip().split("\n")[-1]
    records = substrate.recall(query, limit=3)
    if not records:
        return ""
    lines = ["--- Past recovery experiences ---"]
    for record in records:
        lines.append(
            f"Error: {record.error_type} → Action: {record.recovery_action or 'none'} "
            f"→ Outcome: {record.outcome}"
        )
    return "\n".join(lines)


def reflection_node(
    state: RuntimeState,
    *,
    substrate: MemorySubstrate | None = None,
) -> dict:
    if not state.traceback:
        # Distinguish a true success from a graceful early exit (script
        # printed an error and called exit(1) without raising). Both have
        # empty traceback, but only one is actually success — reporting
        # them the same way makes eval/governor signals incoherent.
        exit_code = state.exec_state.exit_code
        if exit_code is not None and exit_code != 0:
            tail_lines = (state.exec_state.stdout or "").strip().splitlines()[-5:]
            tail = "\n".join(tail_lines)[:300]
            summary = (
                f"Script exited with code {exit_code} but produced no traceback. "
                f"Likely a graceful early exit() after printing an error rather "
                f"than completing the task. Output tail: {tail}"
            )
            reflection_fail = ReflectionResult(
                error_type="NonZeroExit",
                error_summary=summary,
                suggested_fix=(
                    "Do not exit() early when an input is missing — either "
                    "synthesize fallback data, retry with different inputs, "
                    "or attempt the task with what you have. Print the real "
                    "cause and let the runtime decide retry vs accept."
                ),
            )
            return {
                "reflection_result": reflection_fail.model_dump(),
                "semantic_state": state.semantic_state.model_copy(
                    update={
                        "reflection_summary": summary,
                        "reflection_result": reflection_fail,
                    }
                ),
            }

        reflection_ok = ReflectionResult(error_summary="Execution succeeded")
        return {
            "reflection_result": reflection_ok.model_dump(),
            "semantic_state": state.semantic_state.model_copy(
                update={"reflection_summary": "Execution succeeded", "reflection_result": reflection_ok}
            ),
        }

    memory_context = _format_memory_context(state.traceback, substrate=substrate)
    llm = LLMClient()
    user_msg = (
        f"User request: {state.user_request}\n\n"
        f"Traceback:\n{state.traceback}\n\n"
        f"{memory_context}"
    )
    reflection_text = llm.chat(REFLECTION_SYSTEM, user_msg)

    error_type = ""
    error_summary = ""
    suggested_fix = ""
    for line in reflection_text.strip().split("\n"):
        if line.startswith("ErrorType:"):
            error_type = line.removeprefix("ErrorType:").strip()
        elif line.startswith("Summary:"):
            error_summary = line.removeprefix("Summary:").strip()
        elif line.startswith("Fix:"):
            suggested_fix = line.removeprefix("Fix:").strip()

    reflection = ReflectionResult(
        error_type=error_type, error_summary=error_summary, suggested_fix=suggested_fix
    )
    return {
        "reflection_result": reflection.model_dump(),
        "semantic_state": state.semantic_state.model_copy(
            update={"reflection_summary": error_summary, "reflection_result": reflection}
        ),
    }
