"""Data models for the SQL benchmark."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Difficulty = Literal["easy", "medium", "hard", "challenging"]
SqlRunStatus = Literal["correct", "recovered", "wrong", "error"]


@dataclass(frozen=True)
class SqlCase:
    """One reproducible NL -> SQL question.

    db_path           — absolute path to a SQLite file
    schema_ddl        — `CREATE TABLE` statements injected into the LLM prompt
    question          — the natural-language question
    gold_sql          — reference SQL whose result is the ground truth
    evidence          — optional hint (BIRD-style); empty for toy cases
    difficulty        — informational; not used by the runner
    expects_ordering  — when True the comparator preserves row order, used for
                        questions with ORDER BY in the gold SQL
    """

    case_id: str
    db_path: str
    schema_ddl: str
    question: str
    gold_sql: str
    evidence: str = ""
    difficulty: Difficulty = "easy"
    expects_ordering: bool = False


@dataclass(frozen=True)
class SqlRun:
    """Outcome of running one case once through the runtime."""

    case_id: str
    difficulty: str
    status: SqlRunStatus
    attempts: int
    duration_ms: float
    eval_score: float = 0.0
    predicted_output: str = ""
    expected_output: str = ""
    error: str = ""
    notes: str = ""

    @property
    def passed(self) -> bool:
        return self.status in {"correct", "recovered"}


@dataclass(frozen=True)
class SqlBenchReport:
    """Aggregate result of running many SqlCases."""

    runs: list[SqlRun] = field(default_factory=list)
    total_duration_ms: float = 0.0

    @property
    def total(self) -> int:
        return len(self.runs)

    @property
    def correct_count(self) -> int:
        return sum(1 for r in self.runs if r.status == "correct")

    @property
    def recovered_count(self) -> int:
        return sum(1 for r in self.runs if r.status == "recovered")

    @property
    def wrong_count(self) -> int:
        return sum(1 for r in self.runs if r.status == "wrong")

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.runs if r.status == "error")

    @property
    def execution_accuracy(self) -> float:
        """The canonical Text-to-SQL metric: % of runs whose result matches gold."""
        return (self.correct_count + self.recovered_count) / self.total if self.total else 0.0

    @property
    def first_shot_accuracy(self) -> float:
        """Strict baseline: % correct without any retry."""
        return self.correct_count / self.total if self.total else 0.0

    @property
    def recovery_rate(self) -> float:
        """Fraction of cases that failed on attempt #1 but recovered later."""
        return self.recovered_count / self.total if self.total else 0.0

    @property
    def total_attempts(self) -> int:
        return sum(r.attempts for r in self.runs)
