"""TrajectoryStore — JSONL-backed storage for execution trajectories.

Append-only, mirrors ExecutionMemory and HistoryStorage patterns.
Each line is one TrajectoryRecord (JSON). find_similar uses keyword +
problem_signature scoring to surface relevant past sessions for planners.
MultiStepTrajectory records are stored in a separate JSONL file.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from reforge.paths import multistep_trajectories_path, trajectories_path
from reforge.runtime.infrastructure.trajectory.models import MultiStepTrajectory, TrajectoryRecord

_DEFAULT_PATH = trajectories_path()
_MULTISTEP_PATH = multistep_trajectories_path()


class TrajectoryStore:
    """Append-only JSONL store for per-session execution trajectories.

    When a custom path is provided, the multistep trajectory file is placed
    alongside it (stem + "_multistep" + suffix) so tests stay isolated.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _DEFAULT_PATH
        if path is None:
            self._multistep_path = _MULTISTEP_PATH
        else:
            self._multistep_path = path.parent / (path.stem + "_multistep" + path.suffix)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, record: TrajectoryRecord) -> None:
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(record.model_dump_json() + "\n")

    def list_all(self) -> list[TrajectoryRecord]:
        if not self._path.exists():
            return []
        records: list[TrajectoryRecord] = []
        with open(self._path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(TrajectoryRecord.model_validate_json(line))
                    except Exception:
                        continue
        return records

    def find_by_session(self, session_id: str) -> TrajectoryRecord | None:
        for rec in self.list_all():
            if rec.session_id == session_id:
                return rec
        return None

    def find_similar(
        self,
        request: str,
        problem_signature: dict | None = None,
        limit: int = 3,
    ) -> list[TrajectoryRecord]:
        """Score trajectories by keyword overlap + problem_signature match.

        Only returns completed trajectories with a meaningful final_outcome.
        """
        all_records = self.list_all()
        if not all_records:
            return []

        query_words = set(request.lower().split())
        sig = problem_signature or {}
        scored: list[tuple[float, TrajectoryRecord]] = []

        for rec in all_records:
            if not rec.final_outcome:
                continue
            score = _score(rec, query_words, sig)
            if score > 0:
                scored.append((score, rec))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:limit]]


    def find_by_eval_pattern(
        self,
        failure_type: str,
        limit: int = 5,
    ) -> list[TrajectoryRecord]:
        """Find trajectories where any attempt had the given eval_failure_type.

        Useful for Governor ClassifyStage to detect recurring evaluation patterns
        and adjust retry hints accordingly.
        """
        if not failure_type:
            return []
        matches = [
            rec for rec in self.list_all()
            if any(s.eval_failure_type == failure_type for s in rec.steps)
        ]
        return matches[:limit]

    def count_by_eval_pattern(self, failure_type: str) -> int:
        """Count trajectories with at least one attempt matching *failure_type*.

        Counts without materializing the full TrajectoryRecord list — for
        callers that only need the cardinality (e.g. recurrence detection).
        """
        if not failure_type:
            return 0
        return sum(
            1
            for rec in self.list_all()
            if any(s.eval_failure_type == failure_type for s in rec.steps)
        )

    def save_multistep(
        self,
        original_request: str,
        subtask_session_ids: list[str],
        subtask_outcomes: list[str],
        subtask_descriptions: list[str],
        overall_outcome: str,
        total_attempts: int = 0,
    ) -> MultiStepTrajectory:
        """Persist an aggregated multi-step trajectory record."""
        record = MultiStepTrajectory(
            multistep_id=uuid.uuid4().hex[:12],
            timestamp=datetime.now(timezone.utc).isoformat(),
            original_request=original_request,
            subtask_session_ids=subtask_session_ids,
            subtask_outcomes=subtask_outcomes,
            subtask_descriptions=subtask_descriptions,
            overall_outcome=overall_outcome,
            total_attempts=total_attempts,
        )
        self._multistep_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._multistep_path, "a", encoding="utf-8") as f:
            f.write(record.model_dump_json() + "\n")
        return record

    def list_multistep(self) -> list[MultiStepTrajectory]:
        if not self._multistep_path.exists():
            return []
        records: list[MultiStepTrajectory] = []
        with open(self._multistep_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(MultiStepTrajectory.model_validate_json(line))
                    except Exception:
                        continue
        return records


def _score(rec: TrajectoryRecord, query_words: set[str], sig: dict) -> float:
    score = 0.0
    rec_sig = rec.problem_signature

    # problem_signature structural match
    if sig.get("root_cause") and sig["root_cause"] == rec_sig.get("root_cause"):
        score += 4.0
    if sig.get("domain") and sig["domain"] == rec_sig.get("domain"):
        score += 2.0
    if sig.get("error_type") and sig["error_type"] == rec_sig.get("error_type"):
        score += 3.0

    # Keyword overlap
    rec_words = set(rec.user_request.lower().split())
    score += len(query_words & rec_words) * 0.5

    # Favour recovered sessions (more useful than plain failures for planners)
    if rec.final_outcome in ("RECOVERED", "SUCCESS"):
        score += 1.0

    return score
