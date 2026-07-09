"""HpoSession — drive N trials per HpoCase through the Reforge runtime.

Per case the loop is:

    for trial_index in 1..max_trials:
        prompt = build_prompt(case, history, trial_index)
        state  = runner.run(prompt)
        trial  = _grade(state)
        history.append(trial)
        if plateau(history): break

The runtime's internal self-healing (retries inside one trial) is
separate from our outer trial-budget loop:
  * inner = "this attempt crashed → fix the syntax and retry"
  * outer = "this pipeline scored 0.83 — pick a different one"

We do NOT consult `task_outcome` to decide success — the parsed
`CV_SCORE` value from stdout is the source of truth.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed

from reforge.memory.substrate import CompositeMemorySubstrate, MemorySubstrate
from reforge.runtime.hpo.models import (
    HpoBenchReport,
    HpoCase,
    HpoRun,
    HpoTrial,
    HpoTrialStatus,
)
from reforge.runtime.hpo.prompt import build_prompt, parse_cv_score, summarise_pipeline

logger = logging.getLogger(__name__)

RunnerFactory = Callable[[], "RuntimeRunner"]  # noqa: F821 — forward ref


class HpoSession:
    """Run N trials per case and collect an HpoBenchReport.

    Parameters mirror :class:`SqlBenchSession`.
    """

    def __init__(
        self,
        runner_factory: RunnerFactory | None = None,
        memory_substrate: MemorySubstrate | None = None,
        max_workers: int = 1,
    ) -> None:
        self._runner_factory = runner_factory
        self._explicit_memory = memory_substrate is not None
        self._memory = memory_substrate or CompositeMemorySubstrate()
        self._max_workers = max(1, int(max_workers))

    # ------------------------------------------------------------------

    def run(self, cases: Iterable[HpoCase]) -> HpoBenchReport:
        cases = list(cases)
        started = time.perf_counter()

        if self._max_workers == 1 or len(cases) <= 1:
            runs = [self.run_case(case) for case in cases]
        else:
            runs = self._run_parallel(cases)

        total_ms = (time.perf_counter() - started) * 1000
        return HpoBenchReport(runs=runs, total_duration_ms=round(total_ms, 2))

    def run_case(self, case: HpoCase) -> HpoRun:
        history: list[HpoTrial] = []
        best_idx: int | None = None
        best_score: float | None = None
        stopped_reason = "max_trials"
        case_started = time.perf_counter()

        for trial_index in range(1, case.max_trials + 1):
            trial = self._run_one_trial(case, history, trial_index)
            history.append(trial)
            if trial.status == "ok" and trial.cv_score is not None:
                if best_score is None or trial.cv_score > best_score:
                    best_score = trial.cv_score
                    best_idx = trial.trial_index
            if _hit_plateau(history, case.plateau_patience, best_idx):
                stopped_reason = "plateau"
                break

        if best_idx is None and all(t.status != "ok" for t in history):
            stopped_reason = "all_failed"

        duration_ms = (time.perf_counter() - case_started) * 1000
        return HpoRun(
            case_id=case.case_id,
            task=case.task,
            trials=history,
            best_trial_index=best_idx,
            best_cv_score=best_score,
            duration_ms=round(duration_ms, 2),
            stopped_reason=stopped_reason,
        )

    # ------------------------------------------------------------------

    def _run_one_trial(
        self,
        case: HpoCase,
        history: list[HpoTrial],
        trial_index: int,
    ) -> HpoTrial:
        prompt = build_prompt(case, history, trial_index=trial_index)
        runner = self._make_runner()

        start = time.perf_counter()
        try:
            state = runner.run(prompt)
        except Exception as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            return HpoTrial(
                trial_index=trial_index,
                status="runtime_error",
                cv_score=None,
                pipeline_summary="",
                duration_ms=round(duration_ms, 2),
                attempts=0,
                error=f"{type(exc).__name__}: {exc}",
            )
        duration_ms = (time.perf_counter() - start) * 1000
        return _grade(state, trial_index, duration_ms)

    def _run_parallel(self, cases: list[HpoCase]) -> list[HpoRun]:
        workers = min(self._max_workers, len(cases))
        logger.info(
            "HpoSession running %d cases across %d worker threads",
            len(cases),
            workers,
        )
        results: dict[int, HpoRun] = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(self.run_case, case): idx
                for idx, case in enumerate(cases)
            }
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    results[idx] = fut.result()
                except Exception:  # pragma: no cover — defensive
                    logger.exception("HpoSession worker crashed on case index %d", idx)
                    case = cases[idx]
                    results[idx] = HpoRun(
                        case_id=case.case_id,
                        task=case.task,
                        trials=[],
                        best_trial_index=None,
                        best_cv_score=None,
                        duration_ms=0.0,
                        stopped_reason="worker_crashed",
                    )
        return [results[i] for i in range(len(cases))]

    def _make_runner(self):
        if self._runner_factory is not None:
            return self._runner_factory()
        from reforge.runtime.orchestration.engine.runner import RuntimeRunner

        if self._max_workers > 1 and not self._explicit_memory:
            substrate: MemorySubstrate = CompositeMemorySubstrate()
        else:
            substrate = self._memory
        return RuntimeRunner(memory_substrate=substrate)


# ---------------------------------------------------------------------------
# Plateau detection
# ---------------------------------------------------------------------------


def _hit_plateau(
    history: list[HpoTrial],
    patience: int,
    best_idx: int | None,
) -> bool:
    """Stop when the best score hasn't improved in the last `patience` trials.

    Errored trials (parse_error / runtime_error) still count toward
    patience — we don't want to burn the whole budget on a model family
    the LLM keeps mis-coding.

    No successful trial yet → don't trigger; the LLM may still be
    finding its way.
    """
    if patience <= 0 or best_idx is None:
        return False
    return len(history) - best_idx >= patience


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


def _grade(state, trial_index: int, duration_ms: float) -> HpoTrial:
    final_answer = _safe_get(state, "outcome_state", "final_answer", default="") or ""
    outcome = str(_safe_get(state, "outcome_state", "task_outcome", default="UNKNOWN"))
    retry_count = int(_safe_get(state, "control_state", "retry_count", default=0) or 0)
    attempts = retry_count + 1
    eval_result = _safe_get(state, "semantic_state", "evaluation_result", default=None)
    eval_score = float(getattr(eval_result, "score", 0.0)) if eval_result else 0.0
    stderr = _safe_get(state, "exec_state", "stderr", default="") or ""

    cv = parse_cv_score(final_answer)
    pipeline = summarise_pipeline(final_answer, fallback=final_answer.splitlines()[0] if final_answer else "")

    status: HpoTrialStatus
    if cv is not None:
        status = "ok"
        error = ""
    else:
        if outcome in {"FAILED", "DENIED", "EXPECTED_FAILURE"}:
            status = "runtime_error"
            error = (stderr or "")[:500]
        else:
            status = "parse_error"
            error = "no CV_SCORE printed"

    return HpoTrial(
        trial_index=trial_index,
        status=status,
        cv_score=cv,
        pipeline_summary=pipeline,
        duration_ms=round(duration_ms, 2),
        attempts=attempts,
        eval_score=eval_score,
        runtime_outcome=outcome,
        error=error,
    )


def _safe_get(state, *attrs, default=None):
    cur = state
    for attr in attrs:
        cur = getattr(cur, attr, None)
        if cur is None:
            return default
    return cur
