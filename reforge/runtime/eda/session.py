"""EdaSession — runs each EDA stage as a Reforge code-as-action task.

This is application code on top of the runtime. Every stage is a discrete
RuntimeRunner.run() call, so:

  - stages are isolated (one stage's failure does not poison others)
  - each stage gets the full self-healing loop (governor + reflection + retry)
  - failure metrics aggregate per-dataset (how many stages needed retry?)

We deliberately use a *fresh* RuntimeRunner per stage so memory recall is
shared via the substrate but session_id (and therefore the event log
correlation) is one per stage — easier to inspect in the dashboard later.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from reforge.memory.substrate import CompositeMemorySubstrate, MemorySubstrate
from reforge.runtime.eda.models import (
    EdaReport,
    EdaStage,
    EdaStageResult,
    EdaStageStatus,
)
from reforge.runtime.eda.stages import DEFAULT_STAGES

RunnerFactory = Callable[[], "RuntimeRunner"]  # noqa: F821 — forward ref


class EdaSession:
    """Run a sequence of EdaStages over one CSV path, return an EdaReport.

    Parameters
    ----------
    runner_factory
        Callable that returns a fresh RuntimeRunner. Defaults to one that
        reuses `memory_substrate` so cross-stage memory accumulation works.
        Inject a mock factory in tests to avoid hitting real LLMs.
    memory_substrate
        Optional pre-built substrate; defaults to a fresh
        CompositeMemorySubstrate so each session is isolated unless the
        caller threads one in.
    stages
        Defaults to DEFAULT_STAGES. Pass a subset for cheaper test runs.
    """

    def __init__(
        self,
        runner_factory: RunnerFactory | None = None,
        memory_substrate: MemorySubstrate | None = None,
        stages: list[EdaStage] | None = None,
    ) -> None:
        self._memory = memory_substrate or CompositeMemorySubstrate()
        self._runner_factory = runner_factory
        self._stages = stages or list(DEFAULT_STAGES)

    # ------------------------------------------------------------------

    def run(self, csv_path: str | Path) -> EdaReport:
        csv_path = Path(csv_path).resolve()
        if not csv_path.exists():
            raise FileNotFoundError(f"CSV not found: {csv_path}")

        results: list[EdaStageResult] = []
        started = time.perf_counter()

        for stage in self._stages:
            results.append(self._run_stage(stage, csv_path))

        total_ms = (time.perf_counter() - started) * 1000
        return EdaReport(
            dataset_path=str(csv_path),
            stages=results,
            total_duration_ms=round(total_ms, 2),
        )

    # ------------------------------------------------------------------

    def _run_stage(self, stage: EdaStage, csv_path: Path) -> EdaStageResult:
        prompt = stage.prompt_template.format(csv=str(csv_path).replace("\\", "/"))
        runner = self._make_runner()

        start = time.perf_counter()
        try:
            state = runner.run(prompt)
        except Exception as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            return EdaStageResult(
                stage_id=stage.id,
                status="failed",
                attempts=0,
                duration_ms=round(duration_ms, 2),
                error=f"{type(exc).__name__}: {exc}",
            )
        duration_ms = (time.perf_counter() - start) * 1000

        return _result_from_state(stage, state, duration_ms)

    def _make_runner(self):
        if self._runner_factory is not None:
            return self._runner_factory()
        # Late import: don't drag the LangGraph build into module import time.
        from reforge.runtime.orchestration.engine.runner import RuntimeRunner
        return RuntimeRunner(memory_substrate=self._memory)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _result_from_state(
    stage: EdaStage, state, duration_ms: float
) -> EdaStageResult:
    outcome = _safe_get(state, "outcome_state", "task_outcome", default="UNKNOWN")
    retry_count = _safe_get(state, "control_state", "retry_count", default=0)
    attempts = int(retry_count) + 1
    eval_result = _safe_get(state, "semantic_state", "evaluation_result", default=None)
    eval_score = float(getattr(eval_result, "score", 0.0)) if eval_result else 0.0
    final_answer = _safe_get(state, "outcome_state", "final_answer", default="") or ""
    stderr = _safe_get(state, "exec_state", "stderr", default="") or ""

    status: EdaStageStatus
    outcome_str = str(outcome)
    if outcome_str == "SUCCESS":
        status = "ok"
    elif outcome_str == "RECOVERED":
        status = "recovered"
    elif outcome_str in {"DENIED", "EXPECTED_FAILURE"}:
        status = "failed"
    elif outcome_str == "FAILED":
        status = "failed"
    else:
        status = "failed"

    return EdaStageResult(
        stage_id=stage.id,
        status=status,
        attempts=attempts,
        duration_ms=round(duration_ms, 2),
        output=final_answer.strip(),
        error=stderr[:500] if status == "failed" else "",
        eval_score=eval_score,
    )


def _safe_get(state, *attrs, default=None):
    cur = state
    for attr in attrs:
        cur = getattr(cur, attr, None)
        if cur is None:
            return default
    return cur
