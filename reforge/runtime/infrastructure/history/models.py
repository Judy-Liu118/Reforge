"""Lightweight session persistence models.  Not a long-term memory system."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from reforge.runtime.domain.state.models import AttemptRecord

if TYPE_CHECKING:
    from reforge.runtime.domain.state.models import RuntimeState


class SessionRecord(BaseModel):
    """A single runtime execution session, persisted as one JSONL line."""

    session_id: str = Field(default="")
    timestamp: str = Field(default="")
    user_request: str = Field(default="")
    execution_status: str = Field(default="")  # "OK" or "FAIL"
    retry_count: int = Field(default=0)
    total_duration_ms: float = Field(default=0.0)
    attempts: list[AttemptRecord] = Field(default_factory=list)
    final_answer: str = Field(default="")

    @classmethod
    def from_state(
        cls,
        state: "RuntimeState",
        session_id: str,
    ) -> "SessionRecord":
        total_dur = sum(a.duration_ms for a in state.attempts)
        status = "OK" if state.exec_state.exit_code == 0 else "FAIL"
        return cls(
            session_id=session_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            user_request=state.user_request,
            execution_status=status,
            retry_count=state.control_state.retry_count,
            total_duration_ms=round(total_dur, 2),
            attempts=state.attempts,
            final_answer=state.outcome_state.final_answer,
        )
