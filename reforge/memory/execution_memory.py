"""ExecutionMemory — records and recalls runtime execution experiences.

Independent from Governor/workflow. JSONL-based, no new dependencies.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from reforge.memory.fingerprint import extract_fingerprint
from reforge.paths import execution_memory_path


class ExecutionRecord(BaseModel):
    """A single runtime execution experience — request, resolution, repair strategy."""

    timestamp: str = Field(default="")
    request: str = Field(default="")
    outcome: str = Field(default="")
    failure_mode: str = Field(default="")
    retryable: bool = Field(default=False)
    repair_strategy: str = Field(default="")
    task_intent: str = Field(default="")
    problem_signature: dict = Field(default_factory=dict)
    error_type: str = Field(default="")


class ExecutionMemory:
    """Stores and retrieves execution experiences. JSONL-backed, scored search."""

    def __init__(self, path: Path | None = None) -> None:
        # Resolve at call time so REFORGE_PROJECT_DIR / chdir from test
        # isolation harnesses take effect.
        self._path = path or execution_memory_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        request: str,
        outcome: str,
        failure_mode: str,
        retryable: bool = False,
        repair_strategy: str = "",
        task_intent: str = "",
        problem_signature: dict | None = None,
        error_type: str = "",
        traceback: str = "",
    ) -> None:
        sig = problem_signature
        if sig is None:
            fp = extract_fingerprint(traceback, error_type)
            sig = fp.to_dict()
        rec = ExecutionRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            request=request,
            outcome=outcome,
            failure_mode=failure_mode,
            retryable=retryable,
            repair_strategy=repair_strategy,
            task_intent=task_intent,
            problem_signature=sig,
            error_type=error_type,
        )
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(rec.model_dump_json() + "\n")

    def recall_similar(
        self,
        request: str,
        failure_mode: str,
        problem_signature: dict | None = None,
    ) -> list[ExecutionRecord]:
        """Recall the top-3 most similar past execution experiences.

        Uses weighted scoring instead of hard failure_mode filtering,
        so partial matches on problem_signature still surface useful records.
        """
        if not self._path.exists():
            return []

        query_words = set(request.lower().split())
        sig = problem_signature or {}
        results: list[tuple[float, ExecutionRecord]] = []

        with open(self._path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = ExecutionRecord.model_validate_json(line)
                score = _score(rec, query_words, failure_mode, sig)
                if score > 0:
                    results.append((score, rec))

        results.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in results[:3]]


def _score(
    rec: ExecutionRecord,
    query_words: set[str],
    failure_mode: str,
    sig: dict,
) -> float:
    score = 0.0
    rec_sig = rec.problem_signature

    # failure_mode match
    if rec.failure_mode == failure_mode:
        score += 5.0
    elif failure_mode and (failure_mode in rec.failure_mode or rec.failure_mode in failure_mode):
        score += 2.0

    # Structured fingerprint exact matches (highest precision)
    _pairs = [
        ("error_class", 4.0),
        ("error_type", 3.0),
        ("missing_module", 5.0),
        ("missing_key", 4.0),
        ("missing_file", 3.0),
        ("undefined_name", 3.0),
    ]
    for key, weight in _pairs:
        qv = sig.get(key)
        rv = rec_sig.get(key)
        if qv and rv and qv == rv:
            score += weight

    # domain + root_cause structural match
    if sig.get("root_cause") and sig["root_cause"] == rec_sig.get("root_cause"):
        score += 3.0
    if sig.get("domain") and sig["domain"] == rec_sig.get("domain"):
        score += 2.0

    # Keyword overlap
    rec_words = set(rec.request.lower().split())
    score += len(query_words & rec_words) * 0.5

    return score
