"""Phase 1 BIRD ablation driver.

Runs the 20 locked picks (``docs/eval/PHASE1_CORPUS.md``) under both
``REFORGE_GOVERNOR_BYPASS`` arms across N seeds (default 5, the locked
headline-axis budget), inside :func:`token_accounting`, with the same
per-(mode, seed) cold-start memory isolation the Phase 0 driver uses.

Two things distinguish this driver from Phase 0's:

- **Attempt-level capture.** It consumes ``RuntimeRunner.stream()`` and
  pairs each ``execution`` node's stdout with the following
  ``evaluation`` node's verdict, so the v4 §4 evaluator-false-negative
  sensitivity appendix can grade every attempt with the SQL comparator.
  Observation only — the runtime is untouched.
- **Durability.** Each completed run is appended to a JSONL file
  immediately. Resume granularity is the full (mode, seed) leg: a leg
  with fewer records than cases is discarded and re-run, because a
  mid-leg restart would reset the within-leg memory accrual that
  completed legs carry (run-protocol lock in PHASE1_CORPUS.md).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from reforge.benchmark.phase0.driver import (
    _BYPASS_ENV,
    _extract_top_exception,
    _isolated_memory_scope,
    _scoped_env,
)
from reforge.benchmark.phase1.corpus import PHASE1_CASE_IDS
from reforge.observability.llm_events import TokenLedgerEntry, token_accounting
from reforge.runtime.orchestration.engine.runner import RuntimeRunner
from reforge.runtime.sql.bird_loader import load_bird_dev
from reforge.runtime.sql.comparator import compare_results, run_sql
from reforge.runtime.sql.models import SqlCase
from reforge.runtime.sql.prompt import build_prompt as build_sql_prompt
from reforge.runtime.sql.prompt import parse_rows

logger = logging.getLogger(__name__)

_MODES = ("governor", "naive")


@dataclass(frozen=True)
class AttemptObservation:
    """One (execution stdout, evaluation verdict) pair, comparator-graded."""

    attempt: int                 # 1-based
    exit_code: int | None
    eval_passed: bool
    eval_score: float
    comparator_correct: bool
    stdout_head: str             # first 200 chars, debugging aid only


@dataclass(frozen=True)
class Phase1Record:
    """One (case, mode, seed) outcome — field meanings per PHASE0_METRICS v4."""

    case_id: str
    difficulty: str
    mode: str                    # "governor" / "naive"
    seed: int

    passed: bool                 # SQL comparator on final_answer (field-of-record)
    first_try: bool              # passed AND attempts == 1
    recovered: bool              # passed AND attempts > 1

    attempts: int
    action: str                  # terminal retry_decision_action
    policy_reason: str
    failure_mode: str
    runtime_outcome: str
    retry_count: int

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
    def from_json(cls, line: str) -> "Phase1Record":
        raw = json.loads(line)
        raw["attempt_observations"] = [
            AttemptObservation(**obs) for obs in raw.get("attempt_observations", [])
        ]
        return cls(**raw)


def load_records(path: Path) -> list[Phase1Record]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(Phase1Record.from_json(line))
    return records


def _run_case(
    case: SqlCase,
    expected_rows: list[tuple],
    *,
    mode: str,
    seed: int,
) -> Phase1Record:
    """Run one case via stream(), grading attempts and the final answer."""
    observations: list[AttemptObservation] = []
    pending_stdout: str | None = None
    pending_exit: int | None = None
    state = None

    start = time.perf_counter()
    with token_accounting(case_id=case.case_id, seed=seed) as ledger:
        try:
            runner = RuntimeRunner()
            prompt = build_sql_prompt(case)
            for node_name, node_state in runner.stream(prompt):
                state = node_state
                if node_name == "execution":
                    pending_stdout = node_state.exec_state.stdout or ""
                    pending_exit = node_state.exec_state.exit_code
                elif node_name == "evaluation" and pending_stdout is not None:
                    evaluation = node_state.semantic_state.evaluation_result
                    try:
                        correct = compare_results(
                            parse_rows(pending_stdout),
                            expected_rows,
                            order_sensitive=case.expects_ordering,
                        )
                    except Exception:
                        correct = False
                    observations.append(AttemptObservation(
                        attempt=len(observations) + 1,
                        exit_code=pending_exit,
                        eval_passed=bool(getattr(evaluation, "passed", False)),
                        eval_score=float(getattr(evaluation, "score", 0.0)),
                        comparator_correct=correct,
                        stdout_head=pending_stdout[:200],
                    ))
                    pending_stdout = None
        except Exception as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            return _record(
                case=case, mode=mode, seed=seed, passed=False, state=state,
                observations=observations, duration_ms=duration_ms, ledger=ledger,
                notes=f"harness_error: {type(exc).__name__}: {exc}",
            )
    duration_ms = (time.perf_counter() - start) * 1000

    final_answer = state.outcome_state.final_answer or ""
    try:
        passed = compare_results(
            parse_rows(final_answer), expected_rows,
            order_sensitive=case.expects_ordering,
        )
        notes = ""
    except Exception as exc:
        passed = False
        notes = f"grade_error: {type(exc).__name__}: {exc}"
    return _record(
        case=case, mode=mode, seed=seed, passed=passed, state=state,
        observations=observations, duration_ms=duration_ms, ledger=ledger,
        notes=notes,
    )


def _record(
    *,
    case: SqlCase,
    mode: str,
    seed: int,
    passed: bool,
    state,
    observations: list[AttemptObservation],
    duration_ms: float,
    ledger: TokenLedgerEntry,
    notes: str,
) -> Phase1Record:
    if state is not None:
        classification = state.classification_result
        retry_count = state.control_state.retry_count
        action = state.control_state.retry_decision_action or ""
        policy_reason = state.control_state.policy_reason or ""
        failure_mode = classification.failure_mode if classification else ""
        runtime_outcome = state.outcome_state.task_outcome or ""
        top_exc = _extract_top_exception(state.exec_state.stderr or "")
    else:
        retry_count = 0
        action = policy_reason = failure_mode = runtime_outcome = top_exc = ""
    attempts = len(observations) or (retry_count + 1)
    return Phase1Record(
        case_id=case.case_id,
        difficulty=case.difficulty,
        mode=mode,
        seed=seed,
        passed=passed,
        first_try=passed and attempts == 1,
        recovered=passed and attempts > 1,
        attempts=attempts,
        action=action,
        policy_reason=policy_reason,
        failure_mode=failure_mode,
        runtime_outcome=runtime_outcome,
        retry_count=retry_count,
        duration_ms=round(duration_ms, 2),
        tokens_prompt=ledger.prompt_tokens,
        tokens_completion=ledger.completion_tokens,
        tokens_unknown=ledger.unknown,
        n_llm_calls=ledger.calls,
        top_level_exception=top_exc,
        attempt_observations=observations,
        notes=notes,
    )


class PhaseOneDriver:
    """Runs the locked Phase 1 corpus across modes × seeds, resumably."""

    def __init__(
        self,
        *,
        n_seeds: int = 5,
        bird_root: str | None = None,
        records_path: Path,
    ) -> None:
        if n_seeds < 1:
            raise ValueError("n_seeds must be >= 1")
        self._n_seeds = n_seeds
        self._bird_root = bird_root
        self._records_path = records_path

    def run(self) -> list[Phase1Record]:
        cases = self._load_cases()
        expected: dict[str, list[tuple]] = {
            c.case_id: run_sql(c.db_path, c.gold_sql) for c in cases
        }
        records = self._load_resumable(n_cases=len(cases))
        done_legs = {(r.mode, r.seed) for r in records}

        for mode in _MODES:
            bypass_value = "1" if mode == "naive" else None
            with _scoped_env(_BYPASS_ENV, bypass_value):
                for seed in range(self._n_seeds):
                    if (mode, seed) in done_legs:
                        logger.info("phase1: leg %s seed=%d already complete", mode, seed)
                        continue
                    with _isolated_memory_scope():
                        for case in cases:
                            logger.info("phase1: %s seed=%d case=%s", mode, seed, case.case_id)
                            record = _run_case(
                                case, expected[case.case_id], mode=mode, seed=seed,
                            )
                            records.append(record)
                            self._append(record)
        return records

    def _load_cases(self) -> list[SqlCase]:
        kwargs: dict = {}
        if self._bird_root:
            kwargs["root"] = self._bird_root
        by_id = {c.case_id: c for c in load_bird_dev(**kwargs)}
        missing = [cid for cid in PHASE1_CASE_IDS if cid not in by_id]
        if missing:
            raise RuntimeError(
                f"Phase 1 picks missing from loaded corpus: {missing}. "
                "Re-run scripts/prepare_bird.py and confirm dev_databases is intact."
            )
        return [by_id[cid] for cid in PHASE1_CASE_IDS]

    def _load_resumable(self, *, n_cases: int) -> list[Phase1Record]:
        """Keep only complete legs; rewrite the file without partial ones."""
        if not self._records_path.exists():
            return []
        records = load_records(self._records_path)
        by_leg: dict[tuple[str, int], list[Phase1Record]] = {}
        for r in records:
            by_leg.setdefault((r.mode, r.seed), []).append(r)
        kept: list[Phase1Record] = []
        dropped_legs = []
        for leg, leg_records in by_leg.items():
            if len(leg_records) >= n_cases:
                kept.extend(leg_records)
            else:
                dropped_legs.append(leg)
        if dropped_legs:
            logger.warning(
                "phase1: discarding partial legs %s (%d records) — resume is leg-granular",
                dropped_legs, len(records) - len(kept),
            )
            tmp = self._records_path.with_suffix(".jsonl.tmp")
            tmp.write_text(
                "".join(r.to_json() + "\n" for r in kept), encoding="utf-8",
            )
            tmp.replace(self._records_path)
        return kept

    def _append(self, record: Phase1Record) -> None:
        self._records_path.parent.mkdir(parents=True, exist_ok=True)
        with self._records_path.open("a", encoding="utf-8") as fh:
            fh.write(record.to_json() + "\n")
