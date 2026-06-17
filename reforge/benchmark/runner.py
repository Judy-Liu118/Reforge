"""BenchmarkRunner — orchestrate cases through a RuntimeRunner and collect metrics.

The runner takes a *factory* not a *runner*. Each case gets a fresh
RuntimeRunner so:
  - session_id is unique per case (event log + trace separation)
  - memory substrate state is observable per round (for learning-curve mode)

Mock the factory in tests to avoid hitting real LLMs.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable, Iterable

from reforge.benchmark.models import BenchmarkCase, BenchmarkReport, BenchmarkRun
from reforge.memory.substrate import MemorySubstrate

RunnerFactory = Callable[[], "RuntimeRunner"]  # noqa: F821 — forward ref


class _CountingSubstrate:
    """Wrap a MemorySubstrate and count recall() calls.

    Used so the benchmark can report how often the runtime hit memory while
    solving a case — a proxy for memory accumulation impact. Implements the
    full MemorySubstrate Protocol (write / recall / recall_for_planning).
    """

    def __init__(self, inner: MemorySubstrate) -> None:
        self._inner = inner
        self.recall_count = 0

    def recall(self, *args, **kwargs):
        self.recall_count += 1
        return self._inner.recall(*args, **kwargs)

    def recall_for_planning(self, *args, **kwargs):
        self.recall_count += 1
        return self._inner.recall_for_planning(*args, **kwargs)

    def write(self, *args, **kwargs):
        return self._inner.write(*args, **kwargs)


class BenchmarkRunner:
    """Run a sequence of BenchmarkCases and produce a BenchmarkReport."""

    def __init__(
        self,
        runner_factory: RunnerFactory | None = None,
        substrate_factory: Callable[[], MemorySubstrate] | None = None,
    ) -> None:
        self._runner_factory = runner_factory
        self._substrate_factory = substrate_factory

    # ------------------------------------------------------------------

    def _make_substrate(self) -> _CountingSubstrate:
        if self._substrate_factory is not None:
            inner = self._substrate_factory()
        else:
            from reforge.memory.substrate import CompositeMemorySubstrate
            inner = CompositeMemorySubstrate()
        return _CountingSubstrate(inner)

    def _make_runner(self, substrate: _CountingSubstrate):
        if self._runner_factory is not None:
            runner = self._runner_factory()
            # When the caller pre-built a RuntimeRunner we still try to swap
            # substrate in for the recall counter — fall back silently if
            # they froze it.
            if hasattr(runner, "_memory_substrate"):
                runner._memory_substrate = substrate
            return runner
        from reforge.runtime.orchestration.engine.runner import RuntimeRunner
        return RuntimeRunner(memory_substrate=substrate)

    def run_case(self, case: BenchmarkCase) -> BenchmarkRun:
        """Run one case once, returning structured metrics."""
        substrate = self._make_substrate()
        runner = self._make_runner(substrate)

        start = time.perf_counter()
        error_msg = ""
        try:
            state = runner.run(case.request)
        except Exception as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            return _build_failed_run(case, duration_ms, str(exc), substrate.recall_count)

        duration_ms = (time.perf_counter() - start) * 1000

        outcome = _safe_get(state, "outcome_state", "task_outcome", default="UNKNOWN")
        attempts = _safe_get(state, "control_state", "retry_count", default=0) + 1
        eval_result = _safe_get(state, "semantic_state", "evaluation_result", default=None)
        eval_score = float(getattr(eval_result, "score", 0.0)) if eval_result else 0.0
        final_answer = _safe_get(state, "outcome_state", "final_answer", default="") or ""

        keywords_matched = (
            all(kw.lower() in final_answer.lower() for kw in case.expected_keywords)
            if case.expected_keywords
            else True
        )

        return BenchmarkRun(
            case_id=case.id,
            category=case.category,
            difficulty=case.difficulty,
            expected_outcome=case.expected_outcome,
            actual_outcome=str(outcome),
            duration_ms=round(duration_ms, 2),
            attempts=int(attempts),
            eval_score=eval_score,
            memory_recalls=substrate.recall_count,
            keywords_matched=keywords_matched,
            timestamp=datetime.now(timezone.utc).isoformat(),
            error=error_msg,
        )

    # ------------------------------------------------------------------

    def run_all(self, cases: Iterable[BenchmarkCase]) -> BenchmarkReport:
        """Run each case once, return the aggregated report."""
        runs = [self.run_case(c) for c in cases]
        return BenchmarkReport(runs=runs)

    def run_rounds(
        self, case: BenchmarkCase, rounds: int
    ) -> BenchmarkReport:
        """Run a single case multiple times — for cross-session learning-curve study.

        The substrate factory should be sticky (e.g. a shared MemoryStore) so
        the second round sees what the first round wrote. Pass a fresh
        substrate factory if you want round independence.
        """
        runs = [self.run_case(case) for _ in range(rounds)]
        return BenchmarkReport(runs=runs)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_get(state, *attrs, default=None):
    """Walk attributes; return default on any AttributeError."""
    cur = state
    for attr in attrs:
        cur = getattr(cur, attr, None)
        if cur is None:
            return default
    return cur


def _build_failed_run(
    case: BenchmarkCase, duration_ms: float, error: str, recalls: int
) -> BenchmarkRun:
    return BenchmarkRun(
        case_id=case.id,
        category=case.category,
        difficulty=case.difficulty,
        expected_outcome=case.expected_outcome,
        actual_outcome="FAILED",
        duration_ms=round(duration_ms, 2),
        attempts=0,
        eval_score=0.0,
        memory_recalls=recalls,
        keywords_matched=False,
        timestamp=datetime.now(timezone.utc).isoformat(),
        error=error,
    )
