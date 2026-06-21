"""Driver for the Experience Memory Benchmark.

For each PairedCase (seed A, transfer A'):

  Cold leg     — Run A on a fresh substrate, then A' on *another* fresh
                 substrate. Verifies the runtime cannot transfer a lesson
                 it has never seen.
  Warm leg     — Run A on a fresh substrate so it seeds memory, then run
                 A' against the *same* (now-populated) substrate.

The transfer signal is the per-pair delta between `cold_a_prime` and
`warm_a_prime`: same model, same task, only difference is whether memory
exists. Aggregated headline KPIs live in `experience_reporter.py`.

`pair_passed()` overrides BenchmarkRun's strict-equality `passed` rule:
SUCCESS *or* RECOVERED both count, because a Warm-A' first-try success is
exactly the outcome we want to observe.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from reforge.benchmark.experience_cases import PAIRED_CASES, PairedCase
from reforge.benchmark.experience_substrate import (
    ExperienceTmpRoot,
    FreshSubstrateFactory,
    StickySubstrateFactory,
)
from reforge.benchmark.models import BenchmarkCase, BenchmarkRun
from reforge.benchmark.runner import BenchmarkRunner

# Optional progress callback — driver fires it after each individual run so
# CLIs can stream "[P1.warm.A'] PASS attempts=1 score=0.92" without the
# driver itself knowing about stderr.
ProgressCallback = Callable[[str, BenchmarkRun], None]


def pair_passed(run: BenchmarkRun) -> bool:
    """Pass if outcome is SUCCESS or RECOVERED and keywords (if any) match.

    Differs from BenchmarkRun.passed which requires actual==expected
    exactly. Here Warm-A' may legitimately end in SUCCESS (no retry needed
    because memory injected the right approach up front), so we accept both.
    """
    if run.actual_outcome not in ("SUCCESS", "RECOVERED"):
        return False
    return run.keywords_matched


@dataclass(frozen=True)
class PairResult:
    """Four BenchmarkRuns for one paired case (cold A, cold A', warm A, warm A')."""

    pair_id: str
    fingerprint_axis: str
    cold_a: BenchmarkRun
    cold_a_prime: BenchmarkRun
    warm_a: BenchmarkRun
    warm_a_prime: BenchmarkRun

    @property
    def transfer_passed(self) -> bool:
        """Warm-A' passed but Cold-A' did not — the cleanest transfer signal."""
        return pair_passed(self.warm_a_prime) and not pair_passed(self.cold_a_prime)

    @property
    def attempt_delta(self) -> int:
        """Cold-A'.attempts − Warm-A'.attempts. Positive == memory saved retries."""
        return self.cold_a_prime.attempts - self.warm_a_prime.attempts

    @property
    def cold_first_try(self) -> bool:
        """Did Cold-A' pass on attempt #1? Reference point for warm comparison."""
        return pair_passed(self.cold_a_prime) and self.cold_a_prime.attempts == 1

    @property
    def warm_first_try(self) -> bool:
        """Did Warm-A' pass on attempt #1?"""
        return pair_passed(self.warm_a_prime) and self.warm_a_prime.attempts == 1

    @property
    def warm_recall_hit(self) -> bool:
        """Did Warm-A' actually pull memory?"""
        return self.warm_a_prime.memory_recalls > 0


@dataclass(frozen=True)
class ExperienceReport:
    """Aggregate of all PairResults plus headline KPIs."""

    pairs: list[PairResult] = field(default_factory=list)

    @property
    def total_pairs(self) -> int:
        return len(self.pairs)

    @property
    def cold_a_prime_pass_rate(self) -> float:
        if not self.pairs:
            return 0.0
        return sum(1 for p in self.pairs if pair_passed(p.cold_a_prime)) / self.total_pairs

    @property
    def warm_a_prime_pass_rate(self) -> float:
        if not self.pairs:
            return 0.0
        return sum(1 for p in self.pairs if pair_passed(p.warm_a_prime)) / self.total_pairs

    @property
    def transfer_success_rate(self) -> float:
        """Warm-A' pass rate − Cold-A' pass rate. The headline number."""
        return self.warm_a_prime_pass_rate - self.cold_a_prime_pass_rate

    @property
    def avg_cold_attempts(self) -> float:
        if not self.pairs:
            return 0.0
        return sum(p.cold_a_prime.attempts for p in self.pairs) / self.total_pairs

    @property
    def avg_warm_attempts(self) -> float:
        if not self.pairs:
            return 0.0
        return sum(p.warm_a_prime.attempts for p in self.pairs) / self.total_pairs

    @property
    def attempts_reduction(self) -> float:
        """avg_cold_attempts − avg_warm_attempts. Positive == memory helped."""
        return self.avg_cold_attempts - self.avg_warm_attempts

    @property
    def cold_first_try_rate(self) -> float:
        if not self.pairs:
            return 0.0
        return sum(1 for p in self.pairs if p.cold_first_try) / self.total_pairs

    @property
    def warm_first_try_rate(self) -> float:
        if not self.pairs:
            return 0.0
        return sum(1 for p in self.pairs if p.warm_first_try) / self.total_pairs

    @property
    def first_try_delta(self) -> float:
        """warm_first_try_rate − cold_first_try_rate.

        Sensitive to "memory saved an attempt even when both legs end up
        passing" — exactly the scenario `transfer_success_rate` blinds out.
        """
        return self.warm_first_try_rate - self.cold_first_try_rate

    @property
    def warm_recall_hit_rate(self) -> float:
        if not self.pairs:
            return 0.0
        return sum(1 for p in self.pairs if p.warm_recall_hit) / self.total_pairs


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


class ExperienceDriver:
    """Run paired cases through Cold/Warm legs and assemble an ExperienceReport.

    Tests can inject a stub `runner_factory` that returns a mock RuntimeRunner
    to avoid hitting the real LLM. Production callers pass nothing — the
    BenchmarkRunner defaults to RuntimeRunner with the live model config.
    """

    def __init__(
        self,
        runner_factory: Callable | None = None,
        *,
        progress: ProgressCallback | None = None,
    ) -> None:
        self._runner_factory = runner_factory
        self._progress = progress

    def _run_one(
        self,
        case: BenchmarkCase,
        sub_factory: Callable,
        project_dir: Path,
    ) -> BenchmarkRun:
        """Run one case with both substrate AND project-scope state isolated.

        `project_dir` becomes `.reforge/` for this run via REFORGE_PROJECT_DIR,
        so ExecutionMemory's jsonl is sandboxed too — without this, cold-A'
        reads the lesson cold-A wrote (see exp_v0_all_pairs.md root-cause
        analysis: ExecutionMemory.recall_similar bypasses the MemorySubstrate
        isolation entirely).
        """
        project_dir.mkdir(parents=True, exist_ok=True)
        with _scoped_env("REFORGE_PROJECT_DIR", str(project_dir)):
            runner = BenchmarkRunner(
                runner_factory=self._runner_factory,
                substrate_factory=sub_factory,
            )
            return runner.run_case(case)

    def run_pair(self, pair: PairedCase, tmp_root: Path) -> PairResult:
        pair_dir = tmp_root / pair.pair_id
        pair_dir.mkdir(parents=True, exist_ok=True)

        # --- Cold leg: A and A' on independent fresh substrates AND fresh
        #     project dirs so ExecutionMemory.jsonl is also clean -----------
        cold_a = self._run_one(
            pair.seed,
            FreshSubstrateFactory(pair_dir / "cold_a_sub"),
            pair_dir / "cold_a_proj",
        )
        self._emit(f"{pair.pair_id}.cold.A", cold_a)

        cold_aprime = self._run_one(
            pair.transfer,
            FreshSubstrateFactory(pair_dir / "cold_a_prime_sub"),
            pair_dir / "cold_a_prime_proj",
        )
        self._emit(f"{pair.pair_id}.cold.A'", cold_aprime)

        # --- Warm leg: A seeds both substrate AND project state, A' uses
        #     the same project dir + the sticky substrate ------------------
        warm_factory = StickySubstrateFactory(pair_dir / "warm_sub")
        warm_proj = pair_dir / "warm_proj"
        warm_a = self._run_one(pair.seed, warm_factory, warm_proj)
        self._emit(f"{pair.pair_id}.warm.A", warm_a)

        warm_aprime = self._run_one(pair.transfer, warm_factory, warm_proj)
        self._emit(f"{pair.pair_id}.warm.A'", warm_aprime)

        return PairResult(
            pair_id=pair.pair_id,
            fingerprint_axis=pair.fingerprint_axis,
            cold_a=cold_a,
            cold_a_prime=cold_aprime,
            warm_a=warm_a,
            warm_a_prime=warm_aprime,
        )

    def run_all(
        self,
        pairs: list[PairedCase] | None = None,
        *,
        keep_tmp: bool = False,
    ) -> ExperienceReport:
        pairs = pairs or PAIRED_CASES
        with ExperienceTmpRoot(keep=keep_tmp) as tmp_root:
            results = [self.run_pair(p, tmp_root) for p in pairs]
        return ExperienceReport(pairs=results)

    def _emit(self, label: str, run: BenchmarkRun) -> None:
        if self._progress is not None:
            self._progress(label, run)


@contextlib.contextmanager
def _scoped_env(name: str, value: str) -> Iterator[None]:
    """Temporarily set os.environ[name], restoring the previous value on exit."""
    prev = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = prev
