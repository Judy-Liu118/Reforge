"""Reflection node — LLM-based traceback root-cause analysis.

Memory query is delegated to the MemorySubstrate Protocol. Callers can inject
a custom substrate (e.g. in tests); the default is CompositeMemorySubstrate.
Pure helpers (memory context rendering, LLM output parsing) live in
`orchestration.reflection.analysis`.
"""

from __future__ import annotations

from reforge.memory.fingerprint import extract_fingerprint
from reforge.memory.substrate import MemorySubstrate
from reforge.models.adapters.llm_client import LLMClient
from reforge.models.prompts.templates import REFLECTION_SYSTEM
from reforge.runtime.orchestration.reflection.analysis import (
    format_memory_context,
    parse_reflection,
)
from reforge.runtime.domain.state.models import FailureSnapshot, ReflectionResult, RuntimeState

_EARLY_EXIT_FIX = (
    "Do not exit() early when an input is missing — either synthesize "
    "fallback data, retry with different inputs, or attempt the task with "
    "what you have. Print the real cause and let the runtime decide retry "
    "vs accept."
)


def _failed_result(state: RuntimeState, reflection: ReflectionResult, traceback: str) -> dict:
    """Build the node return for a failed attempt.

    Also snapshots the failure while the traceback is still on the state —
    the next attempt overwrites exec_state, and a RECOVERED session needs
    the pairing (signature of what broke → fix that worked) for the
    ExecutionMemory write-back.
    """
    snapshot = FailureSnapshot(
        error_type=reflection.error_type,
        suggested_fix=reflection.suggested_fix,
        problem_signature=extract_fingerprint(traceback, reflection.error_type).to_dict(),
    )
    return {
        "reflection_result": reflection.model_dump(),
        "semantic_state": state.semantic_state.model_copy(
            update={
                "reflection_summary": reflection.error_summary,
                "reflection_result": reflection,
                "last_failure": snapshot,
                "failure_signature_history": [
                    *state.semantic_state.failure_signature_history,
                    snapshot.problem_signature,
                ],
            }
        ),
    }


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
                suggested_fix=_EARLY_EXIT_FIX,
            )
            return _failed_result(state, reflection_fail, traceback="")

        reflection_ok = ReflectionResult(error_summary="Execution succeeded")
        return {
            "reflection_result": reflection_ok.model_dump(),
            "semantic_state": state.semantic_state.model_copy(
                update={"reflection_summary": "Execution succeeded", "reflection_result": reflection_ok}
            ),
        }

    memory_context = format_memory_context(state.traceback, substrate=substrate)
    llm = LLMClient()
    user_msg = (
        f"User request: {state.user_request}\n\n"
        f"Traceback:\n{state.traceback}\n\n"
        f"{memory_context}"
    )
    reflection = parse_reflection(llm.chat(REFLECTION_SYSTEM, user_msg))
    return _failed_result(state, reflection, state.traceback)
