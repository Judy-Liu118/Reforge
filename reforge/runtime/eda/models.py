"""Data models for the EDA application."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

EdaStageStatus = Literal["pending", "ok", "recovered", "failed", "skipped"]


@dataclass(frozen=True)
class EdaStage:
    """One reproducible EDA sub-task.

    `prompt_template` is filled with the absolute CSV path at run time and
    becomes the user_request fed to RuntimeRunner. The stage is responsible
    for generating Python that prints the result; the session captures
    stdout as the stage's output.
    """

    id: str
    title: str
    prompt_template: str
    description: str = ""


@dataclass(frozen=True)
class EdaStageResult:
    """Outcome of running one stage through Reforge."""

    stage_id: str
    status: EdaStageStatus
    attempts: int
    duration_ms: float
    output: str = ""
    error: str = ""
    eval_score: float = 0.0


@dataclass(frozen=True)
class EdaReport:
    """Aggregate result of running all stages over one dataset."""

    dataset_path: str
    stages: list[EdaStageResult] = field(default_factory=list)
    total_duration_ms: float = 0.0

    @property
    def stage_count(self) -> int:
        return len(self.stages)

    @property
    def ok_count(self) -> int:
        return sum(1 for s in self.stages if s.status == "ok")

    @property
    def recovered_count(self) -> int:
        return sum(1 for s in self.stages if s.status == "recovered")

    @property
    def failed_count(self) -> int:
        return sum(1 for s in self.stages if s.status == "failed")

    @property
    def total_attempts(self) -> int:
        return sum(s.attempts for s in self.stages)
