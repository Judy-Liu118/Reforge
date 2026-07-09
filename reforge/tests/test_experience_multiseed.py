"""Tests for the multi-seed Experience Memory Benchmark.

Covers:
  - StatSummary CI calculation against hand-checked values
  - `excludes_zero` logic (the actual signal-vs-noise verdict)
  - MultiSeedDriver fans out N seeds and aggregates correctly
  - MultiSeedReport per-seed delta aggregation (transfer / first-try / attempts)
  - Reporter renders headline-KPI table with CI cells and verdict column

LLM is mocked — same FakeRunner pattern as `test_experience_benchmark.py`.
"""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from reforge.benchmark.experience_cases import PAIRED_CASES
from reforge.benchmark.experience_multiseed import (
    MultiSeedDriver,
    MultiSeedReport,
    PairMultiSeed,
    summarise,
)
from reforge.benchmark.experience_multiseed_reporter import (
    render_multiseed_markdown,
)
from reforge.benchmark.experience_driver import PairResult
from reforge.benchmark.models import BenchmarkRun


# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------


def _fake_state(
    outcome: str = "SUCCESS",
    final_answer: str = "",
    retry_count: int = 0,
    eval_score: float = 1.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        outcome_state=SimpleNamespace(task_outcome=outcome, final_answer=final_answer),
        control_state=SimpleNamespace(retry_count=retry_count),
        semantic_state=SimpleNamespace(
            evaluation_result=SimpleNamespace(score=eval_score)
        ),
    )


class FakeRunner:
    def __init__(self, state) -> None:
        self._state = state
        self._memory_substrate = None

    def run(self, request: str):
        return self._state


def _scripted_factory(states: list):
    idx = {"i": 0}

    def factory():
        s = states[idx["i"]]
        idx["i"] += 1
        return FakeRunner(s)

    return factory


def _run(outcome="SUCCESS", attempts=1, recalls=0, kw_match=True) -> BenchmarkRun:
    return BenchmarkRun(
        case_id="x", category="experience_memory", difficulty="medium",
        expected_outcome="RECOVERED", actual_outcome=outcome,
        duration_ms=10.0, attempts=attempts, eval_score=1.0,
        memory_recalls=recalls, keywords_matched=kw_match, timestamp="2026-06-18",
    )


def _pair(
    *,
    cold_a_out="RECOVERED", cold_a_at=2,
    cold_ap_out="FAILED", cold_ap_at=3,
    warm_a_out="RECOVERED", warm_a_at=2,
    warm_ap_out="SUCCESS", warm_ap_at=1,
) -> PairResult:
    return PairResult(
        pair_id="P1",
        fingerprint_axis="KeyError + missing_key",
        cold_a=_run(outcome=cold_a_out, attempts=cold_a_at),
        cold_a_prime=_run(outcome=cold_ap_out, attempts=cold_ap_at),
        warm_a=_run(outcome=warm_a_out, attempts=warm_a_at),
        warm_a_prime=_run(outcome=warm_ap_out, attempts=warm_ap_at, recalls=2),
    )


# ---------------------------------------------------------------------------
# Stat helpers
# ---------------------------------------------------------------------------


class TestStatSummary:
    def test_single_seed_is_handled_gracefully(self) -> None:
        s = summarise([0.5])
        assert s.n == 1
        assert s.mean == 0.5
        assert s.std == 0.0
        assert math.isnan(s.ci95_half_width)
        assert s.excludes_zero is False

    def test_uniform_values_give_zero_std(self) -> None:
        s = summarise([1.0, 1.0, 1.0, 1.0, 1.0])
        assert s.n == 5
        assert s.mean == 1.0
        assert s.std == 0.0
        # CI half-width = t * (std / sqrt(n)) = t * 0 = 0
        assert s.ci95_half_width == pytest.approx(0.0)
        # CI is exactly [1, 1], doesn't strictly exclude zero (≥ 0 is the rule)
        # but excludes_zero requires strict inequality
        assert s.excludes_zero is True

    def test_ci_excludes_zero_when_uniformly_positive(self) -> None:
        s = summarise([0.2, 0.3, 0.25, 0.28, 0.22])
        # mean ~0.25, std small → CI tight around 0.25, excludes 0
        assert s.mean == pytest.approx(0.25, abs=0.01)
        assert s.excludes_zero is True

    def test_ci_includes_zero_when_noisy_around_zero(self) -> None:
        s = summarise([-0.5, 0.5, -0.3, 0.4, 0.0])
        # Wide spread straddling zero → CI must include zero
        assert s.excludes_zero is False

    def test_ci_excludes_zero_negative_side(self) -> None:
        s = summarise([-0.4, -0.5, -0.3, -0.45, -0.35])
        assert s.mean < 0
        assert s.excludes_zero is True
        assert s.ci95_high < 0


# ---------------------------------------------------------------------------
# PairMultiSeed aggregates
# ---------------------------------------------------------------------------


class TestPairMultiSeed:
    def test_pass_rate_aggregates_over_seeds(self) -> None:
        seeds = [
            _pair(cold_ap_out="FAILED"),
            _pair(cold_ap_out="SUCCESS", cold_ap_at=1),
            _pair(cold_ap_out="FAILED"),
        ]
        p = PairMultiSeed(pair_id="P1", fingerprint_axis="x", seeds=seeds)
        assert p.cold_pass_rate.n == 3
        # 1/3 passed
        assert p.cold_pass_rate.mean == pytest.approx(1 / 3)

    def test_warm_first_try_rate(self) -> None:
        seeds = [
            _pair(warm_ap_out="SUCCESS", warm_ap_at=1),  # first-try
            _pair(warm_ap_out="RECOVERED", warm_ap_at=2),
            _pair(warm_ap_out="SUCCESS", warm_ap_at=1),  # first-try
        ]
        p = PairMultiSeed(pair_id="P1", fingerprint_axis="x", seeds=seeds)
        assert p.warm_first_try_rate.mean == pytest.approx(2 / 3)

    def test_first_try_delta_per_seed(self) -> None:
        # Two seeds where warm hits first-try, cold doesn't
        seeds = [
            _pair(cold_ap_at=3, warm_ap_at=1),
            _pair(cold_ap_at=3, warm_ap_at=1),
        ]
        p = PairMultiSeed(pair_id="P1", fingerprint_axis="x", seeds=seeds)
        d = p.first_try_delta
        # Each seed contributes +1 - 0 = +1
        assert d.mean == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# MultiSeedReport — per-seed delta aggregation
# ---------------------------------------------------------------------------


class TestMultiSeedReportAggregation:
    def _report(self, all_seeds: list[list[PairResult]]) -> MultiSeedReport:
        """all_seeds[pair_idx] = list of seeds for that pair."""
        pair_ids = [f"P{i + 1}" for i in range(len(all_seeds))]
        return MultiSeedReport(pairs=[
            PairMultiSeed(pair_id=pid, fingerprint_axis="x", seeds=runs)
            for pid, runs in zip(pair_ids, all_seeds)
        ])

    def test_total_runs(self) -> None:
        # 3 pairs × 2 seeds × 4 legs
        seeds_one_pair = [_pair(), _pair()]
        report = self._report([seeds_one_pair] * 3)
        assert report.total_runs == 24
        assert report.n_seeds == 2
        assert report.n_pairs == 3

    def test_transfer_success_rate_zero_when_both_legs_pass(self) -> None:
        # 3 pairs × 3 seeds, both cold and warm always pass
        seeds = [_pair(cold_ap_out="RECOVERED", warm_ap_out="RECOVERED")
                 for _ in range(3)]
        report = self._report([seeds, seeds, seeds])
        # Each seed: cold pass rate = 1, warm pass rate = 1, delta = 0
        assert report.transfer_success_rate.mean == 0.0
        assert report.transfer_success_rate.std == 0.0

    def test_transfer_success_rate_per_seed_delta(self) -> None:
        # 2 pairs × 2 seeds
        # Seed 1: cold P1 pass, cold P2 fail, warm both pass → delta = 1 - 0.5 = 0.5
        # Seed 2: cold both pass, warm both pass → delta = 0
        # mean delta = 0.25
        pair1_seeds = [
            _pair(cold_ap_out="RECOVERED", warm_ap_out="SUCCESS"),  # seed1: cold pass
            _pair(cold_ap_out="RECOVERED", warm_ap_out="SUCCESS"),  # seed2: cold pass
        ]
        pair2_seeds = [
            _pair(cold_ap_out="FAILED", warm_ap_out="SUCCESS"),     # seed1: cold fail
            _pair(cold_ap_out="RECOVERED", warm_ap_out="SUCCESS"),  # seed2: cold pass
        ]
        report = self._report([pair1_seeds, pair2_seeds])
        # seed1: warm 2/2=1.0, cold 1/2=0.5, delta=0.5
        # seed2: warm 2/2=1.0, cold 2/2=1.0, delta=0
        # mean = 0.25
        assert report.transfer_success_rate.mean == pytest.approx(0.25)

    def test_attempts_reduction(self) -> None:
        # 1 pair × 3 seeds, cold attempts = 3, warm attempts = 1 always
        seeds = [_pair(cold_ap_at=3, warm_ap_at=1) for _ in range(3)]
        report = self._report([seeds])
        # Each seed: avg_cold=3, avg_warm=1, delta=+2.0
        assert report.attempts_reduction.mean == pytest.approx(2.0)
        assert report.attempts_reduction.std == 0.0
        # All seeds equal → CI half-width = 0; mean=2 strictly > 0 → excludes 0
        assert report.attempts_reduction.excludes_zero is True


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


class TestMultiSeedDriver:
    def test_runs_n_seeds_per_pair(self, tmp_path: Path, monkeypatch) -> None:
        # 1 pair, 3 seeds → 12 fake states
        states = [_fake_state() for _ in range(12)]
        driver = MultiSeedDriver(runner_factory=_scripted_factory(states))
        # Pin tmp dir so test isolation is deterministic
        report = driver.run_all(n_seeds=3, pairs=PAIRED_CASES[:1])
        assert report.n_pairs == 1
        assert report.n_seeds == 3
        assert report.total_runs == 12

    def test_rejects_zero_seeds(self) -> None:
        driver = MultiSeedDriver(runner_factory=_scripted_factory([]))
        with pytest.raises(ValueError):
            driver.run_all(n_seeds=0, pairs=PAIRED_CASES[:1])


# ---------------------------------------------------------------------------
# Reporter
# ---------------------------------------------------------------------------


class TestMultiSeedReporter:
    def test_renders_headline_and_per_pair_sections(self) -> None:
        seeds = [_pair(cold_ap_out="FAILED", warm_ap_out="SUCCESS")
                 for _ in range(3)]
        report = MultiSeedReport(pairs=[
            PairMultiSeed(pair_id="P1", fingerprint_axis="KeyError", seeds=seeds),
            PairMultiSeed(pair_id="P2", fingerprint_axis="ImportError", seeds=seeds),
        ])
        md = render_multiseed_markdown(report)
        assert "## Headline KPIs" in md
        assert "## Per pair" in md
        assert "Transfer success rate" in md
        assert "First-try rate delta" in md
        assert "Attempts reduction" in md
        assert "`P1`" in md and "`P2`" in md

    def test_empty_pairs_does_not_crash(self) -> None:
        md = render_multiseed_markdown(MultiSeedReport(pairs=[]))
        assert "Pairs" in md

    def test_verdict_column_uses_ci_excludes_zero(self) -> None:
        # 5 seeds, identical results → uniform positive transfer signal
        seeds = [_pair(cold_ap_out="FAILED", warm_ap_out="SUCCESS")
                 for _ in range(5)]
        report = MultiSeedReport(pairs=[
            PairMultiSeed(pair_id="P1", fingerprint_axis="x", seeds=seeds),
        ])
        md = render_multiseed_markdown(report)
        # Per-seed delta is always +1.0 (warm passes, cold fails)
        # std=0, CI=[1,1] excludes zero → verdict "positive effect"
        assert "positive effect" in md
