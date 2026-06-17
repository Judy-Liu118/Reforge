"""TraceCollector — hooks into runtime stream, produces structured trace events.

Decoupled from graph nodes. Observes (node_name, RuntimeState) stream only.
"""

from __future__ import annotations

import time

from reforge.observability.tracing.models import EventType, OutcomeType, TraceEvent
from reforge.runtime.domain.state.models import RuntimeState


class TraceCollector:
    """Collects TraceEvents by observing the runtime node execution stream."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.events: list[TraceEvent] = []
        self._prev_time: float = time.time()
        self._outcome: OutcomeType = OutcomeType.FAILED
        self._prev_retry_count: int = 0

    def on_node(self, node_name: str, state: RuntimeState) -> None:
        """Called for each node execution. Produces STARTED + COMPLETED events."""
        now = time.time()
        attempt = state.control_state.retry_count + 1

        if node_name == "planner":
            self._emit(EventType.PLAN_STARTED, attempt, now,
                       input_summary=_summarize(state.user_request, 60))
            self._emit(EventType.PLAN_COMPLETED, attempt, now,
                       output_summary=_summarize(state.generated_code, 60))

        elif node_name == "code_generation":
            self._emit(EventType.CODEGEN_STARTED, attempt, now,
                       input_summary=_summarize(state.user_request, 60))
            self._emit(EventType.CODEGEN_COMPLETED, attempt, now,
                       output_summary=f"{len(state.generated_code)} chars of code",
                       metadata=_codegen_meta(state))

        elif node_name == "execution":
            es = state.exec_state
            dur = es.duration_ms or 0.0
            exit_code = es.exit_code if es.exit_code is not None else -1
            self._emit(EventType.EXECUTION_STARTED, attempt, now - dur / 1000,
                       input_summary=_summarize(state.generated_code, 40))
            self._emit(EventType.EXECUTION_COMPLETED, attempt, now,
                       duration_ms=dur,
                       status="OK" if exit_code == 0 else "FAIL",
                       output_summary=f"exit_code={exit_code}, stdout={len(es.stdout)} chars",
                       metadata={"exit_code": exit_code})

        elif node_name == "reflection":
            rr = state.semantic_state.reflection_result
            self._emit(EventType.REFLECTION_STARTED, attempt, now,
                       input_summary=_summarize(state.traceback, 60))
            self._emit(EventType.REFLECTION_COMPLETED, attempt, now,
                       output_summary=rr.error_summary if rr else "",
                       metadata=_reflection_meta(state))

        elif node_name == "evaluation":
            er = state.semantic_state.evaluation_result
            self._emit(EventType.EVALUATION_STARTED, attempt, now)
            self._emit(EventType.EVALUATION_COMPLETED, attempt, now,
                       status="PASS" if (er and er.passed) else "FAIL",
                       output_summary=er.summary if er else "",
                       metadata=_eval_meta(state))

        elif node_name == "retry_decision":
            # Only emit if a retry was actually triggered (retry_count incremented)
            if state.control_state.retry_count > self._prev_retry_count:
                reason = "error"
                er = state.semantic_state.evaluation_result
                if er and not er.passed:
                    reason = "eval_fail"
                self._emit(EventType.RETRY_TRIGGERED, attempt, now,
                           status="RETRY",
                           output_summary=f"retry_count={state.control_state.retry_count}",
                           metadata={"reason": reason})
            self._prev_retry_count = state.control_state.retry_count

        elif node_name == "final_response":
            self._outcome = _classify_outcome(state)
            self._emit(EventType.TASK_COMPLETED, attempt, now,
                       status=state.outcome_state.task_outcome,
                       output_summary=_summarize(state.outcome_state.final_answer, 60),
                       metadata={
                           "outcome": self._outcome.value,
                           "task_outcome": state.outcome_state.task_outcome,
                           "outcome_reason": state.outcome_state.outcome_reason,
                           "task_intent": state.semantic_state.task_intent,
                           # Nested state snapshot for trace readability
                           "exec_state": state.exec_state.model_dump(),
                           "control_state": state.control_state.model_dump(),
                       })

        self._prev_time = now

    def _emit(
        self, event_type: EventType, attempt: int, ts: float,
        duration_ms: float = 0.0, status: str = "",
        input_summary: str = "", output_summary: str = "",
        metadata: dict | None = None,
    ) -> None:
        from datetime import datetime, timezone

        dt = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        self.events.append(TraceEvent(
            event_id=_short_id(),
            session_id=self.session_id,
            event_type=event_type,
            timestamp=dt,
            attempt=attempt,
            duration_ms=round(duration_ms, 2),
            status=status,
            input_summary=input_summary,
            output_summary=output_summary,
            metadata=metadata or {},
        ))

    @property
    def outcome(self) -> OutcomeType:
        return self._outcome


def _short_id() -> str:
    import uuid
    return uuid.uuid4().hex[:12]


def _summarize(text: str, max_len: int) -> str:
    t = (text or "").strip().replace("\n", " ")
    return t[:max_len] if len(t) > max_len else t


def _codegen_meta(state: RuntimeState) -> dict:
    m: dict = {"code_len": len(state.generated_code)}
    rr = state.semantic_state.reflection_result
    if rr and rr.error_summary:
        m["retry_reason"] = rr.error_summary
    return m


def _reflection_meta(state: RuntimeState) -> dict:
    rr = state.semantic_state.reflection_result
    if not rr:
        return {}
    return {
        "error_type": rr.error_type,
        "root_cause": rr.error_summary,
    }


def _eval_meta(state: RuntimeState) -> dict:
    er = state.semantic_state.evaluation_result
    if not er:
        return {}
    return {
        "score": er.score,
        "failure_type": er.failure_type,
        "checks_passed": sum(1 for c in er.checks if c.passed),
        "checks_total": len(er.checks),
    }


def _classify_outcome(state: RuntimeState) -> OutcomeType:
    # Primary: check task_status first
    if state.outcome_state.task_outcome == "TASK_SUCCESS":
        if state.outcome_state.outcome_reason in ("intentional_failure_accepted", "task_fidelity_achieved"):
            return OutcomeType.EXPECTED_FAILURE
        if state.outcome_state.outcome_reason == "execution_recovered":
            return OutcomeType.RECOVERED
        return OutcomeType.SUCCESS
    return OutcomeType.FAILED
