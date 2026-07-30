"""Dataset-agnostic governor-efficacy driver — Phase 1's skeleton, no SQL.

Reuses unchanged everything in Phase 1 that never depended on SQL:
  - governor / naive arm switching via REFORGE_GOVERNOR_BYPASS (_scoped_env);
  - per-(mode, seed) cold-start memory isolation (_isolated_memory_scope) —
    critically, memory *accrues within a leg*, so a repeated-failure
    sequence exercises the governor's repair_hint recall while naive,
    which never reads it, cannot benefit;
  - token accounting;
  - attempt-level (stdout <-> evaluation verdict) capture, now graded by
    EvalTask.grade (gold oracle) instead of the SQL comparator;
  - the Student-t paired-CI machinery from experience_multiseed.summarise.

Only the three EvalTask seams vary by dataset. Durability/resume is left
minimal here (optional JSONL append); Phase 1's leg-granular resume can be
lifted in verbatim if a long run needs it.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from statistics import fmean

from reforge.benchmark.experience_multiseed import StatSummary, summarise
from reforge.benchmark.phase0.driver import (
    _BYPASS_ENV,
    _extract_top_exception,
    _isolated_memory_scope,
    _scoped_env,
)
from reforge.benchmark.targeted.task import EvalCase, EvalTask
from reforge.observability.llm_events import token_accounting
from reforge.runtime.orchestration.engine.runner import RuntimeRunner

logger = logging.getLogger(__name__)

_MODES = ("governor", "naive")

# Factory so tests can inject a stub runner without a live LLM.
RunnerFactory = Callable[[], object]


@dataclass(frozen=True)
class AttemptObservation:
    """One (execution stdout, evaluation verdict) pair, oracle-graded."""

    attempt: int
    exit_code: int | None
    eval_passed: bool
    eval_score: float
    oracle_correct: bool
    stdout_head: str


@dataclass(frozen=True)
class TargetedRecord:
    """One (case, mode, seed) outcome. `passed` is the gold oracle verdict."""

    case_id: str
    bucket: str
    difficulty: str
    mode: str
    seed: int

    passed: bool
    first_try: bool
    recovered: bool
    attempts: int

    failure_mode: str
    policy_reason: str
    runtime_outcome: str

    duration_ms: float
    tokens_prompt: int
    tokens_completion: int
    tokens_unknown: bool
    n_llm_calls: int

    top_level_exception: str
    attempt_observations: list[AttemptObservation] = field(default_factory=list)
    notes: str = ""

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, line: str) -> "TargetedRecord":
        raw = json.loads(line)
        raw["attempt_observations"] = [
            AttemptObservation(**obs) for obs in raw.get("attempt_observations", [])
        ]
        return cls(**raw)


def _safe_grade(
    task: EvalTask,
    case: EvalCase,
    stdout: str,
    exit_code: int | None,
    generated_code: str = "",
) -> bool:
    """Never let an oracle exception abort a run — a broken grade is a False."""
    try:
        return bool(task.grade(case, stdout, exit_code, generated_code))
    except Exception:  # noqa: BLE001 — oracle robustness, mirrors Phase 1's grade guard
        return False


def run_one_case(
    task: EvalTask,
    case: EvalCase,
    *,
    mode: str,
    seed: int,
    runner_factory: RunnerFactory,
) -> TargetedRecord:
    """Run one case via stream(), grading each attempt and the final answer."""
    observations: list[AttemptObservation] = []
    pending_stdout: str | None = None
    pending_exit: int | None = None
    pending_code: str = ""
    state = None

    start = time.perf_counter()
    with token_accounting(case_id=case.case_id, seed=seed) as ledger:
        try:
            runner = runner_factory()
            prompt = task.build_prompt(case)
            for node_name, node_state in runner.stream(prompt):
                state = node_state
                if node_name == "execution":
                    pending_stdout = node_state.exec_state.stdout or ""
                    pending_exit = node_state.exec_state.exit_code
                    # The source that produced this stdout — a program-graded
                    # oracle (HumanEval) needs it; output-graded ones ignore it.
                    pending_code = node_state.generated_code or ""
                elif node_name == "evaluation" and pending_stdout is not None:
                    evaluation = node_state.semantic_state.evaluation_result
                    correct = _safe_grade(
                        task, case, pending_stdout, pending_exit, pending_code
                    )
                    observations.append(AttemptObservation(
                        attempt=len(observations) + 1,
                        exit_code=pending_exit,
                        eval_passed=bool(getattr(evaluation, "passed", False)),
                        eval_score=float(getattr(evaluation, "score", 0.0)),
                        oracle_correct=correct,
                        stdout_head=pending_stdout[:200],
                    ))
                    pending_stdout = None
        except Exception as exc:  # noqa: BLE001 — harness must record, not crash the sweep
            duration_ms = (time.perf_counter() - start) * 1000
            return _build_record(
                case=case, mode=mode, seed=seed, passed=False, state=state,
                observations=observations, duration_ms=duration_ms, ledger=ledger,
                notes=f"harness_error: {type(exc).__name__}: {exc}",
            )
    duration_ms = (time.perf_counter() - start) * 1000

    final_answer = (state.outcome_state.final_answer or "") if state is not None else ""
    final_code = (state.generated_code or "") if state is not None else ""
    # exit_code is None for the aggregated final answer — no single process code.
    passed = _safe_grade(task, case, final_answer, None, final_code)
    return _build_record(
        case=case, mode=mode, seed=seed, passed=passed, state=state,
        observations=observations, duration_ms=duration_ms, ledger=ledger, notes="",
    )


def _build_record(
    *,
    case: EvalCase,
    mode: str,
    seed: int,
    passed: bool,
    state,
    observations: list[AttemptObservation],
    duration_ms: float,
    ledger,
    notes: str,
) -> TargetedRecord:
    if state is not None:
        classification = state.classification_result
        retry_count = state.control_state.retry_count
        policy_reason = state.control_state.policy_reason or ""
        failure_mode = classification.failure_mode if classification else ""
        runtime_outcome = state.outcome_state.task_outcome or ""
        top_exc = _extract_top_exception(state.exec_state.stderr or "")
    else:
        retry_count = 0
        policy_reason = failure_mode = runtime_outcome = top_exc = ""
    attempts = len(observations) or (retry_count + 1)
    return TargetedRecord(
        case_id=case.case_id,
        bucket=case.bucket,
        difficulty=case.difficulty,
        mode=mode,
        seed=seed,
        passed=passed,
        first_try=passed and attempts == 1,
        recovered=passed and attempts > 1,
        attempts=attempts,
        failure_mode=failure_mode,
        policy_reason=policy_reason,
        runtime_outcome=runtime_outcome,
        duration_ms=round(duration_ms, 2),
        tokens_prompt=ledger.prompt_tokens,
        tokens_completion=ledger.completion_tokens,
        tokens_unknown=ledger.unknown,
        n_llm_calls=ledger.calls,
        top_level_exception=top_exc,
        attempt_observations=observations,
        notes=notes,
    )


class TargetedDriver:
    """Run one EvalTask across governor/naive × seeds, memory-isolated per leg."""

    def __init__(
        self,
        task: EvalTask,
        *,
        n_seeds: int = 5,
        runner_factory: RunnerFactory | None = None,
        records_path: Path | None = None,
    ) -> None:
        if n_seeds < 1:
            raise ValueError("n_seeds must be >= 1")
        self._task = task
        self._n_seeds = n_seeds
        self._runner_factory = runner_factory or RuntimeRunner
        self._records_path = records_path

    def run(self) -> list[TargetedRecord]:
        cases = self._task.load_cases()
        records: list[TargetedRecord] = []
        for mode in _MODES:
            bypass_value = "1" if mode == "naive" else None
            with _scoped_env(_BYPASS_ENV, bypass_value):
                for seed in range(self._n_seeds):
                    # One cold memory leg per (mode, seed); cases within the
                    # leg share it, so bucket sequences accrue repair_hints.
                    with _isolated_memory_scope():
                        for case in cases:
                            logger.info(
                                "targeted[%s]: %s seed=%d case=%s",
                                self._task.name, mode, seed, case.case_id,
                            )
                            record = run_one_case(
                                self._task, case, mode=mode, seed=seed,
                                runner_factory=self._runner_factory,
                            )
                            records.append(record)
                            if self._records_path is not None:
                                self._append(record)
        return records

    def _append(self, record: TargetedRecord) -> None:
        self._records_path.parent.mkdir(parents=True, exist_ok=True)
        with self._records_path.open("a", encoding="utf-8") as fh:
            fh.write(record.to_json() + "\n")


# ---------------------------------------------------------------------------
# Paired metrics (reuse the exact CI machinery Phase 1 / the memory-ablation
# harness ship). Every metric is a per-leg scalar; deltas are paired by seed.
# ---------------------------------------------------------------------------


def _leg(records: Sequence[TargetedRecord], mode: str, seed: int) -> list[TargetedRecord]:
    return [r for r in records if r.mode == mode and r.seed == seed]


def success_rate(leg: Sequence[TargetedRecord]) -> float | None:
    return fmean(1.0 if r.passed else 0.0 for r in leg) if leg else None


def recovery_rate(leg: Sequence[TargetedRecord]) -> float | None:
    """Of the cases NOT solved first try, how many were recovered by retry."""
    denom = [r for r in leg if not r.first_try]
    if not denom:
        return None
    return sum(1 for r in denom if r.recovered) / len(denom)


def attempts_per_case(leg: Sequence[TargetedRecord]) -> float | None:
    return fmean(r.attempts for r in leg) if leg else None


_METRICS: dict[str, Callable[[Sequence[TargetedRecord]], float | None]] = {
    "success_rate": success_rate,
    "recovery_rate": recovery_rate,
    "attempts_per_case": attempts_per_case,
}


def paired_delta(
    records: Sequence[TargetedRecord],
    metric: Callable[[Sequence[TargetedRecord]], float | None],
    n_seeds: int,
) -> StatSummary:
    """Per-seed governor−naive deltas → Student-t 95% CI. Seeds N/A in either arm dropped."""
    deltas: list[float] = []
    for seed in range(n_seeds):
        gov = metric(_leg(records, "governor", seed))
        nai = metric(_leg(records, "naive", seed))
        if gov is None or nai is None:
            continue
        deltas.append(gov - nai)
    return summarise(deltas)


def headline(records: Sequence[TargetedRecord], n_seeds: int) -> dict[str, StatSummary]:
    """Whole-corpus paired deltas, one StatSummary per metric."""
    return {name: paired_delta(records, fn, n_seeds) for name, fn in _METRICS.items()}


def per_bucket(
    records: Sequence[TargetedRecord], n_seeds: int
) -> dict[str, dict[str, StatSummary]]:
    """The targeted view: paired deltas within each failure bucket.

    This is where the governor's contribution shows up when it exists —
    recovery on an observable-failure bucket is not diluted by first-try
    solves elsewhere, which is exactly what BIRD's whole-corpus number hid.
    """
    buckets = sorted({r.bucket for r in records})
    out: dict[str, dict[str, StatSummary]] = {}
    for bucket in buckets:
        subset = [r for r in records if r.bucket == bucket]
        out[bucket] = {
            name: paired_delta(subset, fn, n_seeds) for name, fn in _METRICS.items()
        }
    return out
