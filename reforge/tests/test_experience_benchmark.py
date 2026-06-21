"""Tests for the Experience Memory Benchmark.

Mocks the LLM via a FakeRunner. Asserts:
  - Paired fixtures are shaped correctly
  - Cold/Warm substrate factories isolate / share state as designed
  - pair_passed() relaxes BenchmarkRun.passed to accept SUCCESS or RECOVERED
  - PairResult and ExperienceReport KPIs compute correctly
  - ExperienceDriver runs four legs per pair and threads the right substrate

No real LLM is hit. No real disk outside tmpdir.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from reforge.benchmark.experience_cases import (
    PAIRED_CASES,
    PairedCase,
    all_cases,
    pair_by_id,
)
from reforge.benchmark.experience_driver import (
    ExperienceDriver,
    ExperienceReport,
    PairResult,
    pair_passed,
)
from reforge.benchmark.experience_reporter import render_experience_markdown
from reforge.benchmark.experience_substrate import (
    ExperienceTmpRoot,
    FreshSubstrateFactory,
    StickySubstrateFactory,
)
from reforge.benchmark.models import BenchmarkRun
from reforge.memory.models import MemoryRecord, MemoryType


# ---------------------------------------------------------------------------
# Fake RuntimeRunner — same shape as test_benchmark.FakeRunner
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
    """Return a runner_factory that yields a FakeRunner per scripted state."""
    idx = {"i": 0}

    def factory():
        s = states[idx["i"]]
        idx["i"] += 1
        return FakeRunner(s)

    return factory


def _record(error_type: str = "KeyError", request: str = "x") -> MemoryRecord:
    return MemoryRecord(
        memory_type=MemoryType.RECOVERY,
        session_id="t",
        user_request=request,
        error_type=error_type,
        outcome="RECOVERED",
    )


# ---------------------------------------------------------------------------
# Paired fixtures
# ---------------------------------------------------------------------------


class TestPairedCases:
    def test_five_pairs_present(self) -> None:
        assert len(PAIRED_CASES) == 5
        assert {p.pair_id for p in PAIRED_CASES} == {"P1", "P2", "P3", "P4", "P5"}

    def test_each_pair_has_axis_and_two_cases(self) -> None:
        for p in PAIRED_CASES:
            assert p.fingerprint_axis
            assert p.seed.id != p.transfer.id
            assert p.seed.category == "experience_memory"
            assert p.transfer.category == "experience_memory"
            assert p.seed.expected_outcome == "RECOVERED"
            assert p.transfer.expected_outcome == "RECOVERED"

    def test_pair_by_id_lookup(self) -> None:
        assert pair_by_id("P3") is not None
        assert pair_by_id("nope") is None

    def test_all_cases_flattens_to_10(self) -> None:
        cases = all_cases()
        assert len(cases) == 10


# ---------------------------------------------------------------------------
# Substrate factories
# ---------------------------------------------------------------------------


class TestSubstrateFactories:
    def test_fresh_factory_isolates_writes(self, tmp_path: Path) -> None:
        f = FreshSubstrateFactory(tmp_path / "fresh")
        sub_a = f()
        sub_b = f()
        sub_a.write(_record("KeyError", "A request"))
        # sub_b is a brand-new substrate — sub_a's write must not be visible
        assert sub_b.recall("A request") == []

    def test_sticky_factory_shares_writes(self, tmp_path: Path) -> None:
        f = StickySubstrateFactory(tmp_path / "warm")
        sub_first = f()
        sub_second = f()
        sub_first.write(_record("KeyError", "shared experience"))
        # Sticky factory returns the same underlying substrate
        results = sub_second.recall("shared experience")
        assert len(results) >= 1
        assert results[0].user_request == "shared experience"

    def test_tmp_root_cleans_up(self) -> None:
        with ExperienceTmpRoot() as root:
            assert root.exists()
            captured = root
        assert not captured.exists()

    def test_tmp_root_keep_preserves(self) -> None:
        ctx = ExperienceTmpRoot(keep=True)
        with ctx as root:
            (root / "marker").write_text("x")
            captured = root
        try:
            assert (captured / "marker").exists()
        finally:
            import shutil
            shutil.rmtree(captured, ignore_errors=True)


# ---------------------------------------------------------------------------
# pair_passed semantics
# ---------------------------------------------------------------------------


def _benchmark_run(
    *,
    outcome: str = "SUCCESS",
    attempts: int = 1,
    kw_match: bool = True,
    recalls: int = 0,
) -> BenchmarkRun:
    return BenchmarkRun(
        case_id="x",
        category="experience_memory",
        difficulty="medium",
        expected_outcome="RECOVERED",
        actual_outcome=outcome,
        duration_ms=10.0,
        attempts=attempts,
        eval_score=1.0,
        memory_recalls=recalls,
        keywords_matched=kw_match,
        timestamp="2026-06-18",
    )


class TestPairPassed:
    def test_success_counts_as_pass(self) -> None:
        assert pair_passed(_benchmark_run(outcome="SUCCESS")) is True

    def test_recovered_counts_as_pass(self) -> None:
        assert pair_passed(_benchmark_run(outcome="RECOVERED")) is True

    def test_failed_is_not_pass(self) -> None:
        assert pair_passed(_benchmark_run(outcome="FAILED")) is False

    def test_kw_mismatch_fails_even_on_success(self) -> None:
        assert pair_passed(_benchmark_run(outcome="SUCCESS", kw_match=False)) is False


# ---------------------------------------------------------------------------
# PairResult derived fields
# ---------------------------------------------------------------------------


def _pair_result(
    *,
    cold_a_out: str = "RECOVERED",
    cold_aprime_out: str = "FAILED",
    warm_a_out: str = "RECOVERED",
    warm_aprime_out: str = "SUCCESS",
    cold_aprime_attempts: int = 3,
    warm_aprime_attempts: int = 1,
    warm_aprime_recalls: int = 2,
) -> PairResult:
    return PairResult(
        pair_id="P1",
        fingerprint_axis="KeyError + missing_key",
        cold_a=_benchmark_run(outcome=cold_a_out, attempts=2),
        cold_a_prime=_benchmark_run(outcome=cold_aprime_out, attempts=cold_aprime_attempts),
        warm_a=_benchmark_run(outcome=warm_a_out, attempts=2),
        warm_a_prime=_benchmark_run(
            outcome=warm_aprime_out,
            attempts=warm_aprime_attempts,
            recalls=warm_aprime_recalls,
        ),
    )


class TestPairResult:
    def test_transfer_passed_when_warm_passes_and_cold_fails(self) -> None:
        assert _pair_result().transfer_passed is True

    def test_transfer_not_counted_when_both_pass(self) -> None:
        # Cold also passed — transfer signal is weaker; we mark it as not the
        # *transfer* outcome (memory wasn't the difference-maker).
        assert _pair_result(cold_aprime_out="SUCCESS").transfer_passed is False

    def test_attempt_delta_is_cold_minus_warm(self) -> None:
        p = _pair_result(cold_aprime_attempts=4, warm_aprime_attempts=1)
        assert p.attempt_delta == 3

    def test_warm_first_try(self) -> None:
        p = _pair_result(warm_aprime_attempts=1, warm_aprime_out="SUCCESS")
        assert p.warm_first_try is True

    def test_warm_first_try_false_when_retried(self) -> None:
        p = _pair_result(warm_aprime_attempts=2)
        assert p.warm_first_try is False

    def test_warm_recall_hit_true(self) -> None:
        assert _pair_result(warm_aprime_recalls=3).warm_recall_hit is True

    def test_warm_recall_hit_false(self) -> None:
        assert _pair_result(warm_aprime_recalls=0).warm_recall_hit is False


# ---------------------------------------------------------------------------
# ExperienceReport aggregation
# ---------------------------------------------------------------------------


class TestExperienceReport:
    def test_pass_rates(self) -> None:
        report = ExperienceReport(pairs=[
            _pair_result(cold_aprime_out="FAILED", warm_aprime_out="SUCCESS"),
            _pair_result(cold_aprime_out="RECOVERED", warm_aprime_out="SUCCESS"),
            _pair_result(cold_aprime_out="FAILED", warm_aprime_out="FAILED"),
        ])
        assert report.cold_a_prime_pass_rate == pytest.approx(1 / 3)
        assert report.warm_a_prime_pass_rate == pytest.approx(2 / 3)
        assert report.transfer_success_rate == pytest.approx(1 / 3)

    def test_attempts_reduction(self) -> None:
        report = ExperienceReport(pairs=[
            _pair_result(cold_aprime_attempts=3, warm_aprime_attempts=1),
            _pair_result(cold_aprime_attempts=2, warm_aprime_attempts=2),
        ])
        assert report.avg_cold_attempts == pytest.approx(2.5)
        assert report.avg_warm_attempts == pytest.approx(1.5)
        assert report.attempts_reduction == pytest.approx(1.0)

    def test_warm_recall_hit_rate(self) -> None:
        report = ExperienceReport(pairs=[
            _pair_result(warm_aprime_recalls=2),
            _pair_result(warm_aprime_recalls=0),
            _pair_result(warm_aprime_recalls=5),
        ])
        assert report.warm_recall_hit_rate == pytest.approx(2 / 3)

    def test_empty_report_is_safe(self) -> None:
        empty = ExperienceReport(pairs=[])
        assert empty.cold_a_prime_pass_rate == 0.0
        assert empty.transfer_success_rate == 0.0
        assert empty.attempts_reduction == 0.0


# ---------------------------------------------------------------------------
# ExperienceDriver end-to-end (mock runner)
# ---------------------------------------------------------------------------


class TestProjectDirIsolation:
    """REFORGE_PROJECT_DIR scopes ExecutionMemory.jsonl per-leg.

    Without this, cold-A would write a lesson to .reforge/execution_memory.jsonl
    that cold-A' picks up via ExecutionMemory.recall_similar — invalidating the
    Cold baseline. This test verifies the driver actually sets the env so each
    case sees a fresh project dir.
    """

    def test_env_set_during_run_and_restored_after(self, tmp_path: Path) -> None:
        import os

        observed: list[str | None] = []

        class WatchingRunner:
            _memory_substrate = None

            def __init__(self) -> None:
                observed.append(os.environ.get("REFORGE_PROJECT_DIR"))

            def run(self, request: str):
                return _fake_state()

        before = os.environ.get("REFORGE_PROJECT_DIR")
        pair = PAIRED_CASES[0]
        driver = ExperienceDriver(runner_factory=WatchingRunner)
        driver.run_pair(pair, tmp_path)
        after = os.environ.get("REFORGE_PROJECT_DIR")

        # Four legs observed four distinct project dirs (warm A & A' share)
        assert len(observed) == 4
        assert all(v is not None for v in observed)
        # cold_a / cold_a' / warm shared dirs:
        cold_a_proj, cold_aprime_proj, warm_a_proj, warm_aprime_proj = observed
        assert cold_a_proj != cold_aprime_proj
        assert warm_a_proj == warm_aprime_proj
        # Env restored to its prior value
        assert os.environ.get("REFORGE_PROJECT_DIR") == before


class TestExperienceDriverWiring:
    def test_run_pair_produces_four_runs(self, tmp_path: Path) -> None:
        pair = PAIRED_CASES[0]
        # Script 4 states: cold.A, cold.A', warm.A, warm.A'
        states = [
            _fake_state(outcome="RECOVERED", retry_count=2),
            _fake_state(outcome="FAILED", eval_score=0.2),
            _fake_state(outcome="RECOVERED", retry_count=1),
            _fake_state(outcome="SUCCESS", eval_score=1.0),
        ]
        driver = ExperienceDriver(runner_factory=_scripted_factory(states))
        result = driver.run_pair(pair, tmp_path)
        assert result.pair_id == "P1"
        assert result.cold_a.actual_outcome == "RECOVERED"
        assert result.cold_a_prime.actual_outcome == "FAILED"
        assert result.warm_a_prime.actual_outcome == "SUCCESS"
        # transfer_passed: warm A' passed, cold A' didn't
        # but pair_passed requires kw_match — and our case has no expected_keywords,
        # so keywords_matched defaults to True. Verify:
        assert pair_passed(result.warm_a_prime)
        assert not pair_passed(result.cold_a_prime)
        assert result.transfer_passed is True

    def test_progress_callback_fires_for_each_leg(self, tmp_path: Path) -> None:
        pair = PAIRED_CASES[0]
        states = [_fake_state() for _ in range(4)]
        labels: list[str] = []

        def progress(label, run):
            labels.append(label)

        driver = ExperienceDriver(
            runner_factory=_scripted_factory(states),
            progress=progress,
        )
        driver.run_pair(pair, tmp_path)
        assert labels == ["P1.cold.A", "P1.cold.A'", "P1.warm.A", "P1.warm.A'"]

    def test_run_all_with_subset(self, tmp_path: Path) -> None:
        single_pair = PAIRED_CASES[:1]
        states = [_fake_state() for _ in range(4)]
        driver = ExperienceDriver(runner_factory=_scripted_factory(states))
        report = driver.run_all(single_pair, keep_tmp=False)
        assert report.total_pairs == 1


# ---------------------------------------------------------------------------
# Reporter
# ---------------------------------------------------------------------------


class TestReporter:
    def test_renders_overview_and_per_pair(self) -> None:
        report = ExperienceReport(pairs=[
            _pair_result(cold_aprime_out="FAILED", warm_aprime_out="SUCCESS"),
        ])
        md = render_experience_markdown(report)
        assert "# Reforge Experience Memory Benchmark" in md
        assert "Transfer success rate" in md
        assert "## Cold vs Warm" in md
        assert "## Per pair" in md
        assert "## Per run trace" in md
        assert "`P1`" in md

    def test_renders_zero_pairs_without_crash(self) -> None:
        md = render_experience_markdown(ExperienceReport(pairs=[]))
        assert "Pairs run" in md and "**0**" in md
