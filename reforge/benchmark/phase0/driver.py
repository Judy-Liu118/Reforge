"""Phase 0 calibration driver.

Runs the 5 BIRD picks + 4 toy/decoy cases under both
``REFORGE_GOVERNOR_BYPASS=0`` (governor) and ``=1`` (naive baseline)
across N seeds (default 3), inside the
:func:`reforge.observability.llm_events.token_accounting` context so
token usage is captured per (case, seed) outside the runtime's decision
loop.

Single output shape: ``list[CalibrationRecord]`` (one per case × mode ×
seed), consumed by :mod:`reforge.benchmark.phase0.report` to derive
the go / no-go markdown.

Calibration is instrument-only — no result-direction interpretation
beyond the four locked gate triggers (T2/T3 → execution_recovery; T1 →
eval_driven_recovery; D1″ → timeout-deliberate-STOP; ≥1 of BIRD
recoverability gate → first-try-fail-then-recover).
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from reforge.benchmark.phase0.corpus import (
    BIRD_CASE_IDS,
    TOY_CASES,
    ToyCase,
)
from reforge.config import config
from reforge.observability.llm_events import TokenLedgerEntry, token_accounting
from reforge.runtime.orchestration.engine.runner import RuntimeRunner
from reforge.runtime.sql.bird_loader import load_bird_dev
from reforge.runtime.sql.comparator import compare_results, run_sql
from reforge.runtime.sql.models import SqlCase
from reforge.runtime.sql.prompt import build_prompt as build_sql_prompt
from reforge.runtime.sql.prompt import parse_rows

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Record shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CalibrationRecord:
    """One (case, mode, seed) outcome with the fields go / no-go reads.

    All field meanings are pinned in PHASE0_METRICS.md / PHASE0_CORPUS.md.
    The grading interpretation (whether ``passed`` means correct, whether
    ``recovered`` is well-defined) is the same as the production
    ``SqlBenchSession``: comparator is the source of truth for BIRD, exact
    stripped stdout match for toy recovery cases, deliberate-STOP for
    decoys.
    """

    case_id: str
    kind: str            # "bird" / "recovery_eval" / "recovery_exec" / "decoy_timeout"
    mode: str            # "governor" / "naive"
    seed: int

    passed: bool
    first_try: bool      # passed AND attempts == 1
    recovered: bool      # passed AND attempts > 1

    attempts: int
    action: str          # last retry_decision_action ("STOP" / "ACCEPT" / "RETRY")
    policy_reason: str
    failure_mode: str
    runtime_outcome: str
    retry_count: int     # at terminal step

    duration_ms: float
    tokens_prompt: int
    tokens_completion: int
    tokens_unknown: bool
    n_llm_calls: int

    top_level_exception: str    # last attempt's top-level exception type or ""
    notes: str = ""              # one-line free text for the report


# ---------------------------------------------------------------------------
# Env / workspace helpers
# ---------------------------------------------------------------------------


_BYPASS_ENV = "REFORGE_GOVERNOR_BYPASS"


@contextlib.contextmanager
def _scoped_env(name: str, value: str | None) -> Iterator[None]:
    """Set/clear an env var for the duration of the context."""
    prev = os.environ.get(name)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = prev


_SENTINEL = object()


@contextlib.contextmanager
def _scoped_config_attr(name: str, value) -> Iterator[None]:
    """Mutate a ``reforge.config.config`` attribute for the context.

    Necessary because ``config`` is a singleton instance and downstream
    code (e.g. ``SandboxExecutor(timeout=config.execution_timeout)``)
    reads the attribute each call. Restoring the prior state honours
    whether the attribute was an instance override or a class default.
    """
    prev = config.__dict__.get(name, _SENTINEL)
    setattr(config, name, value)
    try:
        yield
    finally:
        if prev is _SENTINEL:
            try:
                delattr(config, name)
            except AttributeError:
                pass
        else:
            setattr(config, name, prev)


@contextlib.contextmanager
def _isolated_memory_scope() -> Iterator[None]:
    """Cold-start memory for one (mode, seed) leg.

    Points both the project ledger (execution_memory.jsonl — recalled by
    the governor's ClassifyStage) and the global substrate (reflection
    recall, on in both arms) at a fresh tmp dir, so seeds are independent
    and neither arm inherits records written by the other.
    """
    tmp = Path(tempfile.mkdtemp(prefix="phase0_mem_"))
    try:
        with _scoped_env("REFORGE_PROJECT_DIR", str(tmp / "project")), \
             _scoped_env("REFORGE_HOME", str(tmp / "home")):
            yield
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@contextlib.contextmanager
def _temp_workspace(fixture_paths: list[Path]) -> Iterator[Path]:
    """Create a tmp dir, copy fixture files in, return the path. Cleans up."""
    tmp = Path(tempfile.mkdtemp(prefix="phase0_"))
    try:
        for src in fixture_paths:
            shutil.copy2(src, tmp / src.name)
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# Matches the last "ExceptionName: ..." line of a typical Python traceback —
# accepts the *Error, *Exception, *Warning, KeyboardInterrupt, etc. shapes.
_EXC_LINE = re.compile(
    r"^([A-Z][A-Za-z_]*(?:Error|Exception|Warning|Interrupt)):"
)


def _extract_top_exception(stderr: str) -> str:
    """Return the bottom-most exception type name in a Python traceback, or ''.

    Walks lines in reverse so chained "During handling of the above ..."
    sections resolve to the *final* re-raised exception, which is what the
    runtime observed.
    """
    if not stderr:
        return ""
    for line in reversed(stderr.splitlines()):
        m = _EXC_LINE.match(line.strip())
        if m:
            return m.group(1)
    return ""


# ---------------------------------------------------------------------------
# Per-case runners
# ---------------------------------------------------------------------------


def _build_record(
    *,
    case_id: str,
    kind: str,
    mode: str,
    seed: int,
    passed: bool,
    state,
    duration_ms: float,
    ledger: TokenLedgerEntry,
    notes: str = "",
) -> CalibrationRecord:
    classification = state.classification_result
    retry_count = state.control_state.retry_count
    attempts = retry_count + 1
    return CalibrationRecord(
        case_id=case_id,
        kind=kind,
        mode=mode,
        seed=seed,
        passed=passed,
        first_try=passed and attempts == 1,
        recovered=passed and attempts > 1,
        attempts=attempts,
        action=state.control_state.retry_decision_action or "",
        policy_reason=state.control_state.policy_reason or "",
        failure_mode=(classification.failure_mode if classification else ""),
        runtime_outcome=state.outcome_state.task_outcome or "",
        retry_count=retry_count,
        duration_ms=round(duration_ms, 2),
        tokens_prompt=ledger.prompt_tokens,
        tokens_completion=ledger.completion_tokens,
        tokens_unknown=ledger.unknown,
        n_llm_calls=ledger.calls,
        top_level_exception=_extract_top_exception(state.exec_state.stderr or ""),
        notes=notes,
    )


def _error_record(
    *,
    case_id: str,
    kind: str,
    mode: str,
    seed: int,
    duration_ms: float,
    ledger: TokenLedgerEntry,
    exc: BaseException,
) -> CalibrationRecord:
    return CalibrationRecord(
        case_id=case_id,
        kind=kind,
        mode=mode,
        seed=seed,
        passed=False,
        first_try=False,
        recovered=False,
        attempts=0,
        action="",
        policy_reason="",
        failure_mode="",
        runtime_outcome="",
        retry_count=0,
        duration_ms=round(duration_ms, 2),
        tokens_prompt=ledger.prompt_tokens,
        tokens_completion=ledger.completion_tokens,
        tokens_unknown=ledger.unknown,
        n_llm_calls=ledger.calls,
        top_level_exception="",
        notes=f"harness_error: {type(exc).__name__}: {exc}",
    )


def _run_bird_case(case: SqlCase, *, mode: str, seed: int) -> CalibrationRecord:
    """Run one BIRD case, grade with the SQL comparator, return a record."""
    start = time.perf_counter()
    with token_accounting(case_id=case.case_id, seed=seed) as ledger:
        try:
            runner = RuntimeRunner()
            prompt = build_sql_prompt(case)
            state = runner.run(prompt)
        except Exception as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            return _error_record(
                case_id=case.case_id, kind="bird", mode=mode, seed=seed,
                duration_ms=duration_ms, ledger=ledger, exc=exc,
            )
    duration_ms = (time.perf_counter() - start) * 1000

    final_answer = state.outcome_state.final_answer or ""
    try:
        expected_rows = run_sql(case.db_path, case.gold_sql)
        predicted_rows = parse_rows(final_answer)
        passed = compare_results(
            predicted_rows, expected_rows, order_sensitive=case.expects_ordering,
        )
    except Exception as exc:
        passed = False
        notes = f"grade_error: {type(exc).__name__}: {exc}"
        return _build_record(
            case_id=case.case_id, kind="bird", mode=mode, seed=seed,
            passed=False, state=state, duration_ms=duration_ms, ledger=ledger,
            notes=notes,
        )
    return _build_record(
        case_id=case.case_id, kind="bird", mode=mode, seed=seed,
        passed=passed, state=state, duration_ms=duration_ms, ledger=ledger,
    )


def _run_toy_case(toy: ToyCase, *, mode: str, seed: int) -> CalibrationRecord:
    """Run one toy case in a tmp workspace; grade per toy.kind."""
    fixture_paths = toy.fixture_paths()
    start = time.perf_counter()
    timeout_ctx = (
        _scoped_config_attr("execution_timeout", toy.execution_timeout_s)
        if toy.execution_timeout_s is not None
        else contextlib.nullcontext()
    )
    with token_accounting(case_id=toy.case_id, seed=seed) as ledger:
        try:
            with timeout_ctx, _temp_workspace(fixture_paths) as workspace:
                # cwd-switch so prompts that say "current working directory"
                # land in the workspace; sandbox writes target.png etc. here.
                prev_cwd = Path.cwd()
                os.chdir(workspace)
                try:
                    runner = RuntimeRunner()
                    state = runner.run(toy.prompt)
                finally:
                    os.chdir(prev_cwd)
        except Exception as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            return _error_record(
                case_id=toy.case_id, kind=toy.kind, mode=mode, seed=seed,
                duration_ms=duration_ms, ledger=ledger, exc=exc,
            )
    duration_ms = (time.perf_counter() - start) * 1000

    # Recovery toys: grade by exact stripped stdout / final_answer match.
    # Decoy: passed=False; the gate logic checks deliberate-STOP separately.
    if toy.kind.startswith("recovery"):
        produced = (state.outcome_state.final_answer or state.exec_state.stdout or "").strip()
        passed = toy.expected_stdout is not None and produced == toy.expected_stdout
        notes = "" if passed else f"got={produced[:80]!r}"
        return _build_record(
            case_id=toy.case_id, kind=toy.kind, mode=mode, seed=seed,
            passed=passed, state=state, duration_ms=duration_ms, ledger=ledger,
            notes=notes,
        )
    # decoy: success is not the goal; instrument check happens in report.py
    return _build_record(
        case_id=toy.case_id, kind=toy.kind, mode=mode, seed=seed,
        passed=False, state=state, duration_ms=duration_ms, ledger=ledger,
        notes="decoy_run",
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


class PhaseZeroDriver:
    """Runs the locked calibration corpus across modes × seeds.

    Naive runs require ``REFORGE_GOVERNOR_BYPASS=1`` to be set for the
    duration of the run; governor runs require it unset / falsy. The
    driver scopes this via a local env contextmanager — no leak.
    """

    def __init__(self, *, n_seeds: int = 3, bird_root: str | None = None) -> None:
        if n_seeds < 1:
            raise ValueError("n_seeds must be >= 1")
        self._n_seeds = n_seeds
        self._bird_root = bird_root

    def run(self) -> list[CalibrationRecord]:
        bird_cases = self._load_bird_cases()
        records: list[CalibrationRecord] = []

        for mode in ("governor", "naive"):
            bypass_value = "1" if mode == "naive" else None
            with _scoped_env(_BYPASS_ENV, bypass_value):
                for seed in range(self._n_seeds):
                    with _isolated_memory_scope():
                        for case in bird_cases:
                            logger.info("calibration: %s seed=%d case=%s", mode, seed, case.case_id)
                            records.append(_run_bird_case(case, mode=mode, seed=seed))
                        for toy in TOY_CASES:
                            logger.info("calibration: %s seed=%d case=%s", mode, seed, toy.case_id)
                            records.append(_run_toy_case(toy, mode=mode, seed=seed))
        return records

    def _load_bird_cases(self) -> list[SqlCase]:
        kwargs: dict = {}
        if self._bird_root:
            kwargs["root"] = self._bird_root
        all_cases = load_bird_dev(**kwargs)
        by_id = {c.case_id: c for c in all_cases}
        missing = [cid for cid in BIRD_CASE_IDS if cid not in by_id]
        if missing:
            raise RuntimeError(
                f"BIRD calibration picks missing from loaded corpus: {missing}. "
                "Re-run scripts/prepare_bird.py and confirm dev_databases is intact."
            )
        return [by_id[cid] for cid in BIRD_CASE_IDS]
