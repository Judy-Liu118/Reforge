"""Runtime experience memory models. NOT chat memory, NOT RAG."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from reforge.memory.fingerprint import FailureFingerprint, extract_fingerprint


class MemoryType(str, Enum):
    RECOVERY = "RECOVERY"
    FAILURE = "FAILURE"
    SUCCESS_PATTERN = "SUCCESS_PATTERN"


def should_persist_memory(
    outcome: str,
    decision_reason: str,
    error_type: str,
    retry_count: int,
    is_intentional: bool = False,
    requires_recovery: bool = False,
) -> bool:
    """Filter out fake / terminal-intentional memories. Keep genuine runtime learning.

    Skip: terminal intentional demos ("故意让它报错"), evaluator-driven fake retries.
    Keep: recoverable intentional (error → recovery), genuine errors, clean successes.
    """
    # Terminal intentional — user wanted error as final result, no recovery
    if is_intentional and not requires_recovery:
        return False
    if decision_reason in ("intentional_failure_accepted", "task_fidelity_achieved"):
        return False

    # Genuine or recoverable recovery: error + retry + recovered
    if outcome == "RECOVERED" and error_type and retry_count > 0:
        return True

    # Genuine failure: retries exhausted on real error
    if outcome == "FAILED" and error_type:
        return True

    # Clean success without tricks
    if outcome == "SUCCESS" and retry_count == 0:
        return True

    # Exec recovered after genuine retry
    if outcome == "SUCCESS" and retry_count > 0 and error_type:
        return True

    return False


class MemoryRecord(BaseModel):
    """A single runtime experience record — error → recovery, failure, or success pattern."""

    memory_id: str = Field(default="")
    session_id: str = Field(default="")
    timestamp: str = Field(default="")
    memory_type: MemoryType = Field(default=MemoryType.RECOVERY)
    user_request: str = Field(default="")
    error_type: str = Field(default="")
    reflection_summary: str = Field(default="")
    recovery_action: str = Field(default="")
    outcome: str = Field(default="")
    retry_count: int = Field(default=0)
    tags: list[str] = Field(default_factory=list)
    problem_signature: dict = Field(default_factory=dict)

    @classmethod
    def from_session(
        cls,
        session_id: str,
        user_request: str,
        outcome: str,
        retry_count: int,
        error_type: str = "",
        reflection_summary: str = "",
        recovery_action: str = "",
        tags: list[str] | None = None,
        traceback: str = "",
    ) -> MemoryRecord:
        import uuid

        fp = extract_fingerprint(traceback, error_type)
        mem_type = _classify_type(outcome, retry_count, error_type)
        return cls(
            memory_id=uuid.uuid4().hex[:12],
            session_id=session_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            memory_type=mem_type,
            user_request=user_request,
            error_type=error_type or fp.error_class,
            reflection_summary=reflection_summary,
            recovery_action=recovery_action,
            outcome=outcome,
            retry_count=retry_count,
            tags=tags or _extract_tags(user_request, error_type or fp.error_class),
            problem_signature=fp.to_dict(),
        )



def _classify_type(outcome: str, retry_count: int, error_type: str) -> MemoryType:
    if outcome == "EXPECTED_FAILURE":
        return MemoryType.FAILURE
    if error_type and retry_count > 0 and "RECOVERED" in outcome.upper():
        return MemoryType.RECOVERY
    if outcome == "SUCCESS" and retry_count == 0:
        return MemoryType.SUCCESS_PATTERN
    if outcome == "FAILED":
        return MemoryType.FAILURE
    return MemoryType.RECOVERY if retry_count > 0 else MemoryType.SUCCESS_PATTERN


def _extract_tags(user_request: str, error_type: str) -> list[str]:
    tags: list[str] = []
    lowered = user_request.lower()
    if "csv" in lowered or "sales" in lowered:
        tags.append("csv")
    if "pandas" in lowered or "dataframe" in lowered or "df" in lowered:
        tags.append("pandas")
    if "plot" in lowered or "matplotlib" in lowered or "chart" in lowered:
        tags.append("visualization")
    if "平均" in user_request or "mean" in lowered or "average" in lowered:
        tags.append("statistics")
    if error_type:
        tags.append(error_type.lower())
    return list(set(tags))
