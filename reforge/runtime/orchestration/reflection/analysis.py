"""Pure helpers for the reflection node — memory context + LLM output parsing.

Lives in the reflection support package (next to planner_context) so the
graph node file stays thin wiring, per the node size budget contract
(tests/test_workflow_module_slim.py).
"""

from __future__ import annotations

from reforge.memory.substrate import CompositeMemorySubstrate, MemorySubstrate
from reforge.runtime.domain.state.models import ReflectionResult
from reforge.runtime.infrastructure.error_extraction import extract_error_type


def format_memory_context(
    traceback: str, substrate: MemorySubstrate | None = None
) -> str:
    """Render similar past recoveries as prompt context for the reflection LLM."""
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


def parse_reflection(reflection_text: str) -> ReflectionResult:
    """Parse the reflection LLM's `ErrorType:/Summary:/Fix:` line format."""
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
    return ReflectionResult(
        error_type=error_type, error_summary=error_summary, suggested_fix=suggested_fix
    )
