"""Data models for the HPO / AutoML benchmark.

An `HpoCase` is one (dataset, task) pair. The runtime drives N trials per
case — each trial is an independent LLM attempt to write a sklearn
pipeline that prints `CV_SCORE=<float>` to stdout. The trial whose
parsed CV score is highest wins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

TaskKind = Literal["classification", "regression"]
HpoTrialStatus = Literal["ok", "parse_error", "runtime_error"]


@dataclass(frozen=True)
class HpoCase:
    """One reproducible HPO problem.

    case_id           — short slug, also used in report filenames
    dataset_loader    — Python expression eval'd inside the sandbox that
                        returns `(X, y)` ndarrays. e.g.
                        "sklearn.datasets.load_iris(return_X_y=True)"
    task              — "classification" or "regression"
    n_samples / n_features / target_summary — informational, surfaced in
                        the prompt so the LLM can pick a model family
    scoring           — sklearn scoring string, e.g. "accuracy" / "r2".
                        The LLM is asked to use this in cross_val_score.
    max_trials        — budget for trials per case
    plateau_patience  — stop early after this many trials with no
                        improvement over the running best
    baseline_score    — informational; rendered next to the best result
                        so a reader can tell at a glance whether the
                        agent beat the trivial baseline
    """

    case_id: str
    dataset_loader: str
    task: TaskKind
    n_samples: int
    n_features: int
    target_summary: str
    scoring: str = "accuracy"
    max_trials: int = 5
    plateau_patience: int = 3
    baseline_score: float | None = None


@dataclass(frozen=True)
class HpoTrial:
    """Outcome of one trial inside an HpoCase."""

    trial_index: int
    status: HpoTrialStatus
    cv_score: float | None
    pipeline_summary: str
    duration_ms: float
    attempts: int
    eval_score: float = 0.0
    runtime_outcome: str = ""
    error: str = ""


@dataclass(frozen=True)
class HpoRun:
    """Aggregate outcome of N trials against one HpoCase."""

    case_id: str
    task: TaskKind
    trials: list[HpoTrial]
    best_trial_index: int | None
    best_cv_score: float | None
    duration_ms: float
    stopped_reason: str  # "max_trials" | "plateau" | "all_failed"

    @property
    def successful_trials(self) -> int:
        return sum(1 for t in self.trials if t.status == "ok")

    @property
    def first_success_index(self) -> int | None:
        for t in self.trials:
            if t.status == "ok":
                return t.trial_index
        return None


@dataclass(frozen=True)
class HpoBenchReport:
    """Aggregate outcome of an entire HPO benchmark run."""

    runs: list[HpoRun] = field(default_factory=list)
    total_duration_ms: float = 0.0

    @property
    def total_cases(self) -> int:
        return len(self.runs)

    @property
    def cases_with_a_score(self) -> int:
        return sum(1 for r in self.runs if r.best_cv_score is not None)

    @property
    def total_trials(self) -> int:
        return sum(len(r.trials) for r in self.runs)

    @property
    def total_successful_trials(self) -> int:
        return sum(r.successful_trials for r in self.runs)

    @property
    def trial_success_rate(self) -> float:
        return self.total_successful_trials / self.total_trials if self.total_trials else 0.0
