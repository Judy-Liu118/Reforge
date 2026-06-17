"""EDA application — automatic exploratory data analysis on tabular CSV.

Built on top of Reforge runtime: each EDA stage is a code-as-action task
that goes through the full governance pipeline (sandbox + governor +
reflection + retry), so a malformed dataset triggers genuine self-healing
the same way a brittle business task does.
"""

from reforge.runtime.eda.models import (
    EdaReport,
    EdaStage,
    EdaStageResult,
    EdaStageStatus,
)
from reforge.runtime.eda.reporter import render_markdown
from reforge.runtime.eda.session import EdaSession
from reforge.runtime.eda.stages import DEFAULT_STAGES

__all__ = [
    "DEFAULT_STAGES",
    "EdaReport",
    "EdaSession",
    "EdaStage",
    "EdaStageResult",
    "EdaStageStatus",
    "render_markdown",
]
