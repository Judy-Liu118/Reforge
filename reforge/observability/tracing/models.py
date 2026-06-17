"""Structured trace event models. Decoupled from runtime internals."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EventType(str, Enum):
    PLAN_STARTED = "PLAN_STARTED"
    PLAN_COMPLETED = "PLAN_COMPLETED"
    CODEGEN_STARTED = "CODEGEN_STARTED"
    CODEGEN_COMPLETED = "CODEGEN_COMPLETED"
    EXECUTION_STARTED = "EXECUTION_STARTED"
    EXECUTION_COMPLETED = "EXECUTION_COMPLETED"
    REFLECTION_STARTED = "REFLECTION_STARTED"
    REFLECTION_COMPLETED = "REFLECTION_COMPLETED"
    EVALUATION_STARTED = "EVALUATION_STARTED"
    EVALUATION_COMPLETED = "EVALUATION_COMPLETED"
    RETRY_TRIGGERED = "RETRY_TRIGGERED"
    TASK_COMPLETED = "TASK_COMPLETED"
    AGENT_ACTION_STARTED = "AGENT_ACTION_STARTED"
    AGENT_ACTION_COMPLETED = "AGENT_ACTION_COMPLETED"
    AGENT_ACTION_FAILED = "AGENT_ACTION_FAILED"


class OutcomeType(str, Enum):
    SUCCESS = "SUCCESS"
    RECOVERED = "RECOVERED"
    EXPECTED_FAILURE = "EXPECTED_FAILURE"
    FAILED = "FAILED"


class TraceEvent(BaseModel):
    """A single structured trace event emitted during runtime execution."""

    event_id: str = Field(default="")
    session_id: str = Field(default="")
    event_type: EventType
    timestamp: str = Field(default="")
    attempt: int = Field(default=0)
    duration_ms: float = Field(default=0.0)
    status: str = Field(default="")
    input_summary: str = Field(default="")
    output_summary: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def create(
        cls,
        session_id: str,
        event_type: EventType,
        attempt: int = 0,
        duration_ms: float = 0.0,
        status: str = "",
        input_summary: str = "",
        output_summary: str = "",
        metadata: dict | None = None,
    ) -> TraceEvent:
        import uuid

        return cls(
            event_id=uuid.uuid4().hex[:12],
            session_id=session_id,
            event_type=event_type,
            timestamp=datetime.now(timezone.utc).isoformat(),
            attempt=attempt,
            duration_ms=duration_ms,
            status=status,
            input_summary=input_summary,
            output_summary=output_summary,
            metadata=metadata or {},
        )
