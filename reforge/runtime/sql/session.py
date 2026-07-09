"""SqlBenchSession — drive a list of SqlCases through Reforge runtime.

Each case is one RuntimeRunner.run() call. After the run we:

  1. parse predicted stdout into rows (prompt.parse_rows)
  2. execute the gold SQL directly to get the ground truth
  3. compare row sets (comparator.compare_results)
  4. classify the run as correct / recovered / wrong / error

We do NOT consult `task_outcome` alone — a SUCCESS outcome with wrong
SQL is still wrong. The comparator is the source of truth.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed

from reforge.memory.substrate import CompositeMemorySubstrate, MemorySubstrate
from reforge.runtime.sql.comparator import compare_results, run_sql
from reforge.runtime.sql.models import (
    SqlBenchReport,
    SqlCase,
    SqlRun,
    SqlRunStatus,
)
from reforge.runtime.sql.prompt import build_prompt, parse_rows

logger = logging.getLogger(__name__)

RunnerFactory = Callable[[], "RuntimeRunner"]  # noqa: F821 — forward ref


class SqlBenchSession:
    """Run a list of SqlCases and collect a SqlBenchReport.

    Parameters
    ----------
    runner_factory
        Callable returning a fresh RuntimeRunner. Defaults to one that
        threads `memory_substrate` so per-session memory pattern recall
        works inside one benchmark run.
    memory_substrate
        Optional pre-built substrate. Defaults to a fresh
        CompositeMemorySubstrate so each session is isolated. When running
        with `max_workers > 1` AND no explicit substrate is given, each
        worker gets its own fresh substrate to avoid in-memory dict races.
        Pass a thread-safe substrate (e.g. `SqliteMemorySubstrate`) if you
        want cross-worker memory sharing during parallel runs.
    max_workers
        Cases-per-thread cap. `1` (default) preserves the original
        sequential behaviour. >1 dispatches `run_case()` calls through a
        `ThreadPoolExecutor` — case-level parallelism, LLM round-trips
        overlap. The healing loop inside each case stays sequential.
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

    def run(self, cases: Iterable[SqlCase]) -> SqlBenchReport:
        cases = list(cases)
        started = time.perf_counter()

        if self._max_workers == 1 or len(cases) <= 1:
            runs = [self.run_case(case) for case in cases]
        else:
            runs = self._run_parallel(cases)

        total_ms = (time.perf_counter() - started) * 1000
        return SqlBenchReport(runs=runs, total_duration_ms=round(total_ms, 2))

    def _run_parallel(self, cases: list[SqlCase]) -> list[SqlRun]:
        workers = min(self._max_workers, len(cases))
        logger.info(
            "SqlBenchSession running %d cases across %d worker threads",
            len(cases),
            workers,
        )
        results: dict[int, SqlRun] = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(self.run_case, case): idx
                for idx, case in enumerate(cases)
            }
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    results[idx] = fut.result()
                except Exception as exc:  # pragma: no cover — defensive
                    logger.exception("SqlBenchSession worker crashed on case index %d", idx)
                    case = cases[idx]
                    results[idx] = SqlRun(
                        case_id=case.case_id,
                        difficulty=case.difficulty,
                        status="error",
                        attempts=0,
                        duration_ms=0.0,
                        error=f"{type(exc).__name__}: {exc}",
                    )
        return [results[i] for i in range(len(cases))]

    def run_case(self, case: SqlCase) -> SqlRun:
        prompt = build_prompt(case)
        runner = self._make_runner()

        start = time.perf_counter()
        try:
            state = runner.run(prompt)
        except Exception as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            return SqlRun(
                case_id=case.case_id,
                difficulty=case.difficulty,
                status="error",
                attempts=0,
                duration_ms=round(duration_ms, 2),
                error=f"{type(exc).__name__}: {exc}",
            )
        duration_ms = (time.perf_counter() - start) * 1000

        return _grade(case, state, duration_ms)

    # ------------------------------------------------------------------

    def _make_runner(self):
        if self._runner_factory is not None:
            return self._runner_factory()
        from reforge.runtime.orchestration.engine.runner import RuntimeRunner

        # Parallel + default (in-memory) substrate → fresh per worker to
        # avoid CompositeMemorySubstrate dict races. If the caller passed
        # an explicit substrate we trust them (e.g. SqliteMemorySubstrate
        # has its own threading.Lock).
        if self._max_workers > 1 and not self._explicit_memory:
            substrate: MemorySubstrate = CompositeMemorySubstrate()
        else:
            substrate = self._memory
        return RuntimeRunner(memory_substrate=substrate)


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


def _grade(case: SqlCase, state, duration_ms: float) -> SqlRun:
    outcome = _safe_get(state, "outcome_state", "task_outcome", default="UNKNOWN")
    retry_count = int(_safe_get(state, "control_state", "retry_count", default=0) or 0)
    attempts = retry_count + 1
    eval_result = _safe_get(state, "semantic_state", "evaluation_result", default=None)
    eval_score = float(getattr(eval_result, "score", 0.0)) if eval_result else 0.0
    final_answer = _safe_get(state, "outcome_state", "final_answer", default="") or ""
    stderr = _safe_get(state, "exec_state", "stderr", default="") or ""

    # First, compute the expected gold result.
    try:
        expected_rows = run_sql(case.db_path, case.gold_sql)
    except Exception as exc:
        # Gold SQL itself fails — treat as harness error, not the model's fault.
        return SqlRun(
            case_id=case.case_id,
            difficulty=case.difficulty,
            status="error",
            attempts=attempts,
            duration_ms=round(duration_ms, 2),
            eval_score=eval_score,
            error=f"gold SQL failed: {type(exc).__name__}: {exc}",
        )

    predicted_rows = parse_rows(final_answer)
    expected_summary = _rows_summary(expected_rows)
    predicted_summary = final_answer.strip()
    outcome_str = str(outcome)

    correct = compare_results(
        predicted_rows,
        expected_rows,
        order_sensitive=case.expects_ordering,
    )

    # Result is the source of truth. Outcome only re-classifies a wrong
    # result so we can tell apart "model gave wrong answer" from "runtime
    # gave up before reaching a confident answer".
    status: SqlRunStatus
    if correct:
        status = "correct" if attempts == 1 else "recovered"
        error = ""
    else:
        if outcome_str in {"DENIED", "FAILED", "EXPECTED_FAILURE"}:
            status = "error"
            error = (stderr or "")[:500]
        else:
            status = "wrong"
            error = ""

    return SqlRun(
        case_id=case.case_id,
        difficulty=case.difficulty,
        status=status,
        attempts=attempts,
        duration_ms=round(duration_ms, 2),
        eval_score=eval_score,
        predicted_output=predicted_summary[:1500],
        expected_output=expected_summary[:1500],
        error=error,
        notes=f"runtime_outcome={outcome_str}",
    )


def _rows_summary(rows: list[tuple]) -> str:
    lines = [" | ".join("NULL" if c is None else str(c) for c in r) for r in rows]
    return "\n".join(lines)


def _safe_get(state, *attrs, default=None):
    cur = state
    for attr in attrs:
        cur = getattr(cur, attr, None)
        if cur is None:
            return default
    return cur
