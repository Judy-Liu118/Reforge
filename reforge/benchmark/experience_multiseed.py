"""Multi-seed extension to the Experience Memory Benchmark.

Why a separate module: the single-seed `ExperienceReport` answers
"did memory help in *this* run?" — but a single LLM run can flip on
nondeterminism (v0 P2 → EXPECTED_FAILURE; v1 P2 → SUCCESS, same prompt).
A robust answer needs the same Cold/Warm protocol repeated across N
independent seeds with mean ± std + a 95% confidence interval per KPI.

`MultiSeedDriver` thinly wraps `ExperienceDriver`: each seed is a fully
independent `run_all()`, so isolation guarantees and the `_scoped_env`
context still apply per-leg. Aggregation happens on the resulting list
of `ExperienceReport`s.

No new dependencies. Uses the Student-t inverse from `statistics` so we
don't pull in scipy just for one CI calculation.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

from reforge.benchmark.experience_cases import PAIRED_CASES, PairedCase
from reforge.benchmark.experience_driver import (
    ExperienceDriver,
    PairResult,
    ProgressCallback,
    pair_passed,
)


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------


# Student-t critical values for two-tailed 95% CI, df = n − 1.
# Hard-coded for small N to avoid a scipy dependency. Falls back to 1.96
# (z, large-sample) for N ≥ 30.
_T_CRIT_95 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447,
    7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228, 15: 2.131, 20: 2.093,
    25: 2.064, 29: 2.045,
}


def _t_critical_95(n: int) -> float:
    if n <= 1:
        return float("nan")
    df = n - 1
    if df in _T_CRIT_95:
        return _T_CRIT_95[df]
    # interpolate roughly for df between table points; fall back to z
    if df >= 30:
        return 1.96
    closest = min(_T_CRIT_95.keys(), key=lambda k: abs(k - df))
    return _T_CRIT_95[closest]


@dataclass(frozen=True)
class StatSummary:
    """Mean ± std + 95% CI for one KPI across N seeds."""

    n: int
    mean: float
    std: float
    ci95_half_width: float  # ± value to subtract/add from mean

    @property
    def ci95_low(self) -> float:
        return self.mean - self.ci95_half_width

    @property
    def ci95_high(self) -> float:
        return self.mean + self.ci95_half_width

    @property
    def excludes_zero(self) -> bool:
        """True iff the 95% CI sits entirely on one side of zero.

        Useful for transfer-style deltas (`first_try_delta`,
        `attempts_reduction`): if zero is inside the CI, the effect is
        not statistically distinguishable from null at α=0.05.
        """
        if self.n <= 1 or math.isnan(self.ci95_half_width):
            return False
        return self.ci95_low > 0 or self.ci95_high < 0


def summarise(values: list[float]) -> StatSummary:
    n = len(values)
    if n == 0:
        return StatSummary(n=0, mean=0.0, std=0.0, ci95_half_width=float("nan"))
    mean = statistics.fmean(values)
    if n == 1:
        return StatSummary(n=1, mean=mean, std=0.0, ci95_half_width=float("nan"))
    std = statistics.stdev(values)
    se = std / math.sqrt(n)
    half = _t_critical_95(n) * se
    return StatSummary(n=n, mean=mean, std=std, ci95_half_width=half)


# ---------------------------------------------------------------------------
# Multi-seed aggregate types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PairMultiSeed:
    """All seeds' PairResult for one pair, plus per-pair stat summaries."""

    pair_id: str
    fingerprint_axis: str
    seeds: list[PairResult]

    @property
    def n(self) -> int:
        return len(self.seeds)

    @property
    def cold_pass_rate(self) -> StatSummary:
        return summarise([1.0 if pair_passed(s.cold_a_prime) else 0.0
                          for s in self.seeds])

    @property
    def warm_pass_rate(self) -> StatSummary:
        return summarise([1.0 if pair_passed(s.warm_a_prime) else 0.0
                          for s in self.seeds])

    @property
    def cold_first_try_rate(self) -> StatSummary:
        return summarise([1.0 if s.cold_first_try else 0.0 for s in self.seeds])

    @property
    def warm_first_try_rate(self) -> StatSummary:
        return summarise([1.0 if s.warm_first_try else 0.0 for s in self.seeds])

    @property
    def first_try_delta(self) -> StatSummary:
        return summarise([
            (1.0 if s.warm_first_try else 0.0)
            - (1.0 if s.cold_first_try else 0.0)
            for s in self.seeds
        ])

    @property
    def attempt_delta(self) -> StatSummary:
        return summarise([float(s.attempt_delta) for s in self.seeds])


@dataclass(frozen=True)
class MultiSeedReport:
    """Aggregate across pairs × seeds with statistical headline KPIs."""

    pairs: list[PairMultiSeed] = field(default_factory=list)

    @property
    def n_seeds(self) -> int:
        return self.pairs[0].n if self.pairs else 0

    @property
    def n_pairs(self) -> int:
        return len(self.pairs)

    @property
    def total_runs(self) -> int:
        return self.n_seeds * self.n_pairs * 4   # 4 legs per pair

    @property
    def transfer_success_rate(self) -> StatSummary:
        """Per-seed (warm − cold) pass-rate delta, averaged across seeds."""
        deltas: list[float] = []
        for seed_idx in range(self.n_seeds):
            warm = sum(
                1.0 for p in self.pairs
                if pair_passed(p.seeds[seed_idx].warm_a_prime)
            ) / self.n_pairs
            cold = sum(
                1.0 for p in self.pairs
                if pair_passed(p.seeds[seed_idx].cold_a_prime)
            ) / self.n_pairs
            deltas.append(warm - cold)
        return summarise(deltas)

    @property
    def first_try_delta(self) -> StatSummary:
        """Per-seed (warm − cold) first-try-rate delta."""
        deltas: list[float] = []
        for seed_idx in range(self.n_seeds):
            warm = sum(
                1.0 for p in self.pairs
                if p.seeds[seed_idx].warm_first_try
            ) / self.n_pairs
            cold = sum(
                1.0 for p in self.pairs
                if p.seeds[seed_idx].cold_first_try
            ) / self.n_pairs
            deltas.append(warm - cold)
        return summarise(deltas)

    @property
    def attempts_reduction(self) -> StatSummary:
        """Per-seed (avg cold attempts − avg warm attempts)."""
        deltas: list[float] = []
        for seed_idx in range(self.n_seeds):
            cold = sum(p.seeds[seed_idx].cold_a_prime.attempts
                       for p in self.pairs) / self.n_pairs
            warm = sum(p.seeds[seed_idx].warm_a_prime.attempts
                       for p in self.pairs) / self.n_pairs
            deltas.append(cold - warm)
        return summarise(deltas)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


class MultiSeedDriver:
    """Run the Experience Memory Benchmark across N independent seeds.

    Each seed is a full `ExperienceDriver.run_all()` with its own tmp
    root, so per-leg substrate and project-dir isolation are unchanged.
    """

    def __init__(
        self,
        runner_factory=None,
        *,
        progress: ProgressCallback | None = None,
    ) -> None:
        self._runner_factory = runner_factory
        self._progress = progress

    def run_all(
        self,
        n_seeds: int,
        pairs: list[PairedCase] | None = None,
        *,
        keep_tmp: bool = False,
    ) -> MultiSeedReport:
        if n_seeds < 1:
            raise ValueError("n_seeds must be ≥ 1")
        pairs = pairs or PAIRED_CASES
        # Map[pair_id] -> list of PairResult, one per seed
        by_pair: dict[str, list[PairResult]] = {p.pair_id: [] for p in pairs}
        axis_by_pair: dict[str, str] = {p.pair_id: p.fingerprint_axis for p in pairs}

        for seed_idx in range(n_seeds):
            driver = ExperienceDriver(
                runner_factory=self._runner_factory,
                progress=_seed_prefixed_progress(self._progress, seed_idx),
            )
            seed_report = driver.run_all(pairs, keep_tmp=keep_tmp)
            for pr in seed_report.pairs:
                by_pair[pr.pair_id].append(pr)

        return MultiSeedReport(pairs=[
            PairMultiSeed(
                pair_id=pid,
                fingerprint_axis=axis_by_pair[pid],
                seeds=runs,
            )
            for pid, runs in by_pair.items()
        ])


def _seed_prefixed_progress(
    inner: ProgressCallback | None,
    seed_idx: int,
) -> ProgressCallback | None:
    if inner is None:
        return None

    def wrapped(label: str, run) -> None:
        inner(f"seed{seed_idx + 1}.{label}", run)

    return wrapped
