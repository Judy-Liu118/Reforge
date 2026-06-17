"""Event-State Consistency Validator.

Compares a RuntimeStateProjection (derived from ExecutionEventLog) against
the corresponding RuntimeState (produced by graph node mutations).

Consistency means: the event log carries the same information that RuntimeState
holds via direct mutations.  A clean report is the prerequisite for removing any
RuntimeState field and replacing it with an event-derived projection.

This module is the verification layer for the event-sourced RuntimeState
migration roadmap (see DAILY_TASKS.md LATER section and CLAUDE.md).

Design:
  - Zero side effects — never mutates state or emits events
  - Float fields compared with relative tolerance (1e-6) to avoid fp noise
  - eval checks are skipped when evaluation_result is None (pre-evaluation)
  - None → empty-string normalisation for optional string fields
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from reforge.runtime.events.projection import RuntimeStateProjection
from reforge.runtime.domain.state.models import RuntimeState

# ---------------------------------------------------------------------------
# Report types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldMismatch:
    """A single field whose projected value disagrees with the state value."""

    field_name: str
    projected_value: Any  # value from RuntimeStateProjection
    state_value: Any      # corresponding value from RuntimeState


@dataclass(frozen=True)
class ConsistencyReport:
    """Result of comparing a RuntimeStateProjection against a RuntimeState."""

    session_id: str
    mismatches: tuple[FieldMismatch, ...]

    @property
    def is_consistent(self) -> bool:
        return len(self.mismatches) == 0

    def mismatch_fields(self) -> list[str]:
        return [m.field_name for m in self.mismatches]


# ---------------------------------------------------------------------------
# Consistency check
# ---------------------------------------------------------------------------


def check_state_consistency(
    projection: RuntimeStateProjection,
    state: RuntimeState,
) -> ConsistencyReport:
    """Compare *projection* fields against their *state* counterparts.

    Returns a ConsistencyReport.  Empty mismatches → views are consistent.

    Checked mappings
    ----------------
    projection.retry_count           ← state.control_state.retry_count
    projection.last_policy_decision  ← state.control_state.retry_decision_action
    projection.last_eval_score       ← state.semantic_state.evaluation_result.score  (if present)
    projection.last_eval_passed      ← state.semantic_state.evaluation_result.passed (if present)
    projection.last_reflection       ← state.semantic_state.reflection_summary
    projection.last_execution_outcome← derived from state.exec_state.exit_code
    projection.current_attempt       ← len(state.attempts)
    """
    issues: list[FieldMismatch] = []

    def _check(field: str, proj_val: Any, state_val: Any) -> None:
        if isinstance(proj_val, float) and isinstance(state_val, float):
            if not math.isclose(proj_val, state_val, rel_tol=1e-6, abs_tol=1e-9):
                issues.append(FieldMismatch(field, proj_val, state_val))
        elif proj_val != state_val:
            issues.append(FieldMismatch(field, proj_val, state_val))

    # 1. retry_count
    _check("retry_count", projection.retry_count, state.control_state.retry_count)

    # 2. last_policy_decision — retry_decision_action may be str, str+Enum mixin, or None.
    #    For a `class Foo(str, Enum)` mixin, str(instance) yields "Foo.VALUE" in Python
    #    ≤3.10 and "Foo.VALUE" in 3.11–3.13; only .value gives the bare string.
    raw = state.control_state.retry_decision_action
    state_policy = str(raw) if raw is not None else ""
    if hasattr(raw, "value"):
        state_policy = str(raw.value)
    _check("last_policy_decision", projection.last_policy_decision, state_policy)

    # 3 & 4. eval score / passed — only checked when evaluation has run
    er = state.semantic_state.evaluation_result
    if er is not None:
        _check("last_eval_score", projection.last_eval_score, er.score)
        _check("last_eval_passed", projection.last_eval_passed, er.passed)

    # 5. reflection summary
    state_reflection = state.semantic_state.reflection_summary or ""
    _check("last_reflection", projection.last_reflection, state_reflection)

    # 6. last_execution_outcome — derived from exec_state.exit_code
    ec = state.exec_state.exit_code
    if ec is None:
        state_outcome = ""
    elif ec == 0:
        state_outcome = "succeeded"
    else:
        state_outcome = "failed"
    _check("last_execution_outcome", projection.last_execution_outcome, state_outcome)

    # 7. current_attempt — each execution appends one AttemptRecord
    _check("current_attempt", projection.current_attempt, len(state.attempts))

    # 8. task_completed_outcome — only checked when a TASK_COMPLETED event exists
    #    so that pipelines that don't run final_response_node stay consistent
    if projection.task_completed_outcome:
        state_task_outcome = state.outcome_state.task_outcome or ""
        _check("task_completed_outcome", projection.task_completed_outcome, state_task_outcome)

    return ConsistencyReport(
        session_id=projection.session_id,
        mismatches=tuple(issues),
    )
