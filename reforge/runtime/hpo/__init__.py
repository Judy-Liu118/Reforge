"""HPO / AutoML benchmark — Reforge runtime application.

Each :class:`HpoCase` is a (dataset, task) pair. :class:`HpoSession`
drives N trials per case; each trial is one runtime run that asks the
LLM to write a sklearn pipeline and print its cross-validated score.

Public surface:

    from reforge.runtime.hpo import HpoSession, HpoCase, render_markdown
    report = HpoSession().run(cases)
"""

from reforge.runtime.hpo.models import (
    HpoBenchReport,
    HpoCase,
    HpoRun,
    HpoTrial,
    HpoTrialStatus,
    TaskKind,
)
from reforge.runtime.hpo.prompt import build_prompt, parse_cv_score, summarise_pipeline
from reforge.runtime.hpo.reporter import render_markdown
from reforge.runtime.hpo.session import HpoSession

__all__ = [
    "HpoBenchReport",
    "HpoCase",
    "HpoRun",
    "HpoSession",
    "HpoTrial",
    "HpoTrialStatus",
    "TaskKind",
    "build_prompt",
    "parse_cv_score",
    "render_markdown",
    "summarise_pipeline",
]
