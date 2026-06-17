"""Data models for the benchmark suite."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Difficulty = Literal["easy", "medium", "hard"]
ExpectedOutcome = Literal["SUCCESS", "RECOVERED", "EXPECTED_FAILURE", "DENIED", "FAILED"]


@dataclass(frozen=True)
class BenchmarkCase:
    """One reproducible benchmark task."""

    id: str
    category: str
    difficulty: Difficulty
    request: str
    expected_outcome: ExpectedOutcome
    expected_keywords: list[str] = field(default_factory=list)
    description: str = ""


@dataclass(frozen=True)
class BenchmarkRun:
    """Result of running one case once."""

    case_id: str
    category: str
    difficulty: str
    expected_outcome: str
    actual_outcome: str
    duration_ms: float
    attempts: int
    eval_score: float
    memory_recalls: int
    keywords_matched: bool
    timestamp: str
    error: str = ""

    @property
    def passed(self) -> bool:
        """A run passes when the actual outcome matches the expected outcome.

        Keyword match is required for SUCCESS / RECOVERED to filter out runs
        that returned the right outcome label but with nonsense output.
        """
        if self.actual_outcome != self.expected_outcome:
            return False
        if self.expected_outcome in {"SUCCESS", "RECOVERED"}:
            return self.keywords_matched
        return True


@dataclass(frozen=True)
class BenchmarkReport:
    """Aggregated metrics across many BenchmarkRuns."""

    runs: list[BenchmarkRun]

    # ------------------------------------------------------------------
    # Headline rates

    @property
    def total(self) -> int:
        return len(self.runs)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.runs if r.passed)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def first_shot_success_rate(self) -> float:
        """Fraction of passing runs that succeeded on the very first attempt."""
        if not self.runs:
            return 0.0
        fs = [r for r in self.runs if r.passed and r.attempts == 1 and r.actual_outcome == "SUCCESS"]
        return len(fs) / self.total

    @property
    def recovery_rate(self) -> float:
        """Fraction of runs that took ≥2 attempts but still ended OK."""
        if not self.runs:
            return 0.0
        rec = [r for r in self.runs if r.actual_outcome == "RECOVERED"]
        return len(rec) / self.total

    @property
    def hard_failure_rate(self) -> float:
        """Fraction that ended in FAILED (expected_outcome != FAILED)."""
        if not self.runs:
            return 0.0
        hf = [r for r in self.runs if r.actual_outcome == "FAILED" and r.expected_outcome != "FAILED"]
        return len(hf) / self.total

    @property
    def average_attempts(self) -> float:
        if not self.runs:
            return 0.0
        return sum(r.attempts for r in self.runs) / self.total

    @property
    def average_eval_score(self) -> float:
        scored = [r.eval_score for r in self.runs if r.eval_score > 0]
        return sum(scored) / len(scored) if scored else 0.0

    @property
    def average_duration_ms(self) -> float:
        if not self.runs:
            return 0.0
        return sum(r.duration_ms for r in self.runs) / self.total

    # ------------------------------------------------------------------
    # Breakdowns

    def by_category(self) -> dict[str, "BenchmarkReport"]:
        groups: dict[str, list[BenchmarkRun]] = {}
        for r in self.runs:
            groups.setdefault(r.category, []).append(r)
        return {k: BenchmarkReport(runs=v) for k, v in groups.items()}

    def by_difficulty(self) -> dict[str, "BenchmarkReport"]:
        groups: dict[str, list[BenchmarkRun]] = {}
        for r in self.runs:
            groups.setdefault(r.difficulty, []).append(r)
        return {k: BenchmarkReport(runs=v) for k, v in groups.items()}

    # ------------------------------------------------------------------
    # Learning curve

    def learning_curve(self) -> dict[str, list[float]]:
        """For each case ID, the eval_score sequence across rounds.

        Useful when a single case was run multiple times to observe whether
        memory substrate accumulation improved success/score.
        """
        curve: dict[str, list[float]] = {}
        for r in self.runs:
            curve.setdefault(r.case_id, []).append(r.eval_score)
        return curve
