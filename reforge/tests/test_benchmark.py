"""Tests for the benchmark suite (P5).

All tests use a fake RuntimeRunner that returns pre-canned state objects, so
no real LLM is called. The intent is to validate metrics computation,
report rendering, and learning-curve aggregation — not to exercise the
runtime itself (that's covered by 1000+ other tests).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from reforge.benchmark import (
    DEFAULT_CASES,
    BenchmarkCase,
    BenchmarkReport,
    BenchmarkRun,
    BenchmarkRunner,
    get_cases_by_category,
    render_markdown,
)
from reforge.benchmark.reporter import render_learning_curve_markdown


# ---------------------------------------------------------------------------
# Fake RuntimeRunner
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
    """Configurable fake — returns a pre-set state when .run() is called."""

    def __init__(self, state, *, raise_exc: Exception | None = None) -> None:
        self._state = state
        self._raise_exc = raise_exc
        self._memory_substrate = None
        self.run_calls: list[str] = []

    def run(self, request: str):
        self.run_calls.append(request)
        if self._raise_exc:
            raise self._raise_exc
        return self._state


# ---------------------------------------------------------------------------
# BenchmarkRun.passed semantics
# ---------------------------------------------------------------------------


class TestBenchmarkRunPassed:
    def _run(self, **kwargs) -> BenchmarkRun:
        defaults = dict(
            case_id="x",
            category="c",
            difficulty="easy",
            expected_outcome="SUCCESS",
            actual_outcome="SUCCESS",
            duration_ms=0.0,
            attempts=1,
            eval_score=1.0,
            memory_recalls=0,
            keywords_matched=True,
            timestamp="2026-06-13",
        )
        defaults.update(kwargs)
        return BenchmarkRun(**defaults)

    def test_outcome_match_and_keywords_match_passes(self) -> None:
        assert self._run().passed is True

    def test_outcome_mismatch_fails(self) -> None:
        assert not self._run(actual_outcome="FAILED").passed

    def test_success_with_no_keyword_match_fails(self) -> None:
        assert not self._run(keywords_matched=False).passed

    def test_denied_outcome_does_not_require_keywords(self) -> None:
        # DENIED runs have no expected_keywords; keywords_matched is True by default
        r = self._run(
            expected_outcome="DENIED", actual_outcome="DENIED", keywords_matched=False
        )
        # For DENIED, keywords matching is irrelevant — only outcome match counts
        assert r.passed is True

    def test_recovered_requires_keywords(self) -> None:
        r = self._run(
            expected_outcome="RECOVERED",
            actual_outcome="RECOVERED",
            keywords_matched=False,
        )
        assert r.passed is False


# ---------------------------------------------------------------------------
# Cases inventory
# ---------------------------------------------------------------------------


class TestCases:
    def test_default_cases_present(self) -> None:
        assert len(DEFAULT_CASES) >= 10

    def test_each_case_has_required_fields(self) -> None:
        for c in DEFAULT_CASES:
            assert c.id and c.request and c.expected_outcome
            assert c.category in {
                "csv_basic",
                "csv_recovery",
                "intentional",
                "denied",
                "robustness",
            }
            assert c.difficulty in {"easy", "medium", "hard"}

    def test_filter_by_category(self) -> None:
        denied = get_cases_by_category("denied")
        assert denied and all(c.category == "denied" for c in denied)

    def test_robustness_category_present(self) -> None:
        cases = get_cases_by_category("robustness")
        assert len(cases) >= 4, "robustness must cover timeout / partial / malformed / injection"
        ids = {c.id for c in cases}
        assert "robust_timeout_recovery" in ids
        assert "robust_double_column_miss" in ids
        assert "robust_malformed_constraint" in ids
        assert "robust_prompt_injection" in ids


# ---------------------------------------------------------------------------
# BenchmarkRunner
# ---------------------------------------------------------------------------


def _case(
    id="t1",
    expected_outcome="SUCCESS",
    expected_keywords: list[str] | None = None,
    category="csv_basic",
) -> BenchmarkCase:
    return BenchmarkCase(
        id=id,
        category=category,
        difficulty="easy",
        request="some request",
        expected_outcome=expected_outcome,
        expected_keywords=expected_keywords or [],
    )


class TestBenchmarkRunnerSingleCase:
    def test_success_case_collects_metrics(self) -> None:
        state = _fake_state(outcome="SUCCESS", final_answer="value=42", eval_score=1.0)
        runner = BenchmarkRunner(runner_factory=lambda: FakeRunner(state))
        case = _case(expected_keywords=["42"])
        run = runner.run_case(case)
        assert run.actual_outcome == "SUCCESS"
        assert run.eval_score == 1.0
        assert run.attempts == 1
        assert run.keywords_matched is True
        assert run.passed is True

    def test_recovery_case_with_retries(self) -> None:
        state = _fake_state(
            outcome="RECOVERED", final_answer="profit not found", retry_count=2
        )
        runner = BenchmarkRunner(runner_factory=lambda: FakeRunner(state))
        case = _case(expected_outcome="RECOVERED", expected_keywords=["profit"])
        run = runner.run_case(case)
        assert run.actual_outcome == "RECOVERED"
        assert run.attempts == 3  # retry_count=2 means 3 total attempts
        assert run.passed is True

    def test_runner_exception_becomes_failed_run(self) -> None:
        runner = BenchmarkRunner(
            runner_factory=lambda: FakeRunner(None, raise_exc=RuntimeError("boom"))
        )
        run = runner.run_case(_case())
        assert run.actual_outcome == "FAILED"
        assert "boom" in run.error
        assert not run.passed

    def test_memory_recall_counter(self) -> None:
        """The counting substrate proxy must increment when recall() is called."""
        from reforge.memory.substrate import CompositeMemorySubstrate

        recall_calls: list[str] = []

        class StubInner:
            def recall(self, *a, **kw):
                recall_calls.append("hit")
                return []

            def store(self, *a, **kw):
                pass

        state = _fake_state()
        runner = BenchmarkRunner(
            runner_factory=lambda: FakeRunner(state),
            substrate_factory=lambda: StubInner(),
        )
        # The FakeRunner doesn't actually use the substrate, so recall stays 0
        # We exercise the counter directly:
        from reforge.benchmark.runner import _CountingSubstrate

        cs = _CountingSubstrate(StubInner())
        cs.recall("query")
        cs.recall("query2")
        assert cs.recall_count == 2


class TestBenchmarkRunnerBatch:
    def test_run_all_aggregates(self) -> None:
        # All cases succeed
        runner = BenchmarkRunner(
            runner_factory=lambda: FakeRunner(
                _fake_state(outcome="SUCCESS", final_answer="ok")
            )
        )
        cases = [_case(id=f"c{i}") for i in range(3)]
        report = runner.run_all(cases)
        assert report.total == 3
        assert report.passed == 3
        assert report.pass_rate == 1.0
        assert report.first_shot_success_rate == 1.0
        assert report.recovery_rate == 0.0

    def test_mixed_outcomes_reported(self) -> None:
        states = [
            _fake_state(outcome="SUCCESS", final_answer="42"),
            _fake_state(outcome="RECOVERED", final_answer="recovered", retry_count=1),
            _fake_state(outcome="FAILED", final_answer="", eval_score=0.3),
        ]
        cases = [
            _case(id="ok", expected_outcome="SUCCESS", expected_keywords=["42"]),
            _case(id="rec", expected_outcome="RECOVERED", expected_keywords=["recovered"]),
            _case(id="bad", expected_outcome="SUCCESS"),
        ]
        idx = [0]

        def factory():
            r = FakeRunner(states[idx[0]])
            idx[0] += 1
            return r

        runner = BenchmarkRunner(runner_factory=factory)
        report = runner.run_all(cases)
        assert report.total == 3
        assert report.passed == 2  # SUCCESS + RECOVERED both pass
        assert report.recovery_rate == pytest.approx(1 / 3)
        assert report.hard_failure_rate == pytest.approx(1 / 3)
        assert report.average_attempts == pytest.approx((1 + 2 + 1) / 3)

    def test_per_category_breakdown(self) -> None:
        state = _fake_state(outcome="SUCCESS", final_answer="ok")
        cases = [
            _case(id="a", category="csv_basic"),
            _case(id="b", category="csv_basic"),
            _case(id="c", category="denied", expected_outcome="DENIED"),
        ]
        # Override the third case's state: should be DENIED
        states = [state, state, _fake_state(outcome="DENIED")]
        idx = [0]

        def factory():
            r = FakeRunner(states[idx[0]])
            idx[0] += 1
            return r

        runner = BenchmarkRunner(runner_factory=factory)
        report = runner.run_all(cases)
        breakdown = report.by_category()
        assert breakdown["csv_basic"].total == 2
        assert breakdown["denied"].total == 1
        assert breakdown["denied"].pass_rate == 1.0


class TestBenchmarkRunnerRounds:
    def test_run_rounds_collects_all(self) -> None:
        state = _fake_state(outcome="SUCCESS", final_answer="ok")
        runner = BenchmarkRunner(runner_factory=lambda: FakeRunner(state))
        report = runner.run_rounds(_case(), rounds=4)
        assert report.total == 4

    def test_learning_curve(self) -> None:
        # Simulate 3 rounds with improving scores
        states = [
            _fake_state(outcome="RECOVERED", final_answer="ok", retry_count=2, eval_score=0.5),
            _fake_state(outcome="RECOVERED", final_answer="ok", retry_count=1, eval_score=0.7),
            _fake_state(outcome="SUCCESS", final_answer="ok", retry_count=0, eval_score=1.0),
        ]
        idx = [0]

        def factory():
            r = FakeRunner(states[idx[0]])
            idx[0] += 1
            return r

        runner = BenchmarkRunner(runner_factory=factory)
        report = runner.run_rounds(_case(), rounds=3)
        curve = report.learning_curve()
        assert curve == {"t1": [0.5, 0.7, 1.0]}


# ---------------------------------------------------------------------------
# Reporter
# ---------------------------------------------------------------------------


class TestReporterMarkdown:
    def _report(self) -> BenchmarkReport:
        runs = [
            BenchmarkRun(
                case_id="c1", category="csv_basic", difficulty="easy",
                expected_outcome="SUCCESS", actual_outcome="SUCCESS",
                duration_ms=120.0, attempts=1, eval_score=1.0,
                memory_recalls=0, keywords_matched=True,
                timestamp="2026-06-13",
            ),
            BenchmarkRun(
                case_id="c2", category="csv_recovery", difficulty="medium",
                expected_outcome="RECOVERED", actual_outcome="RECOVERED",
                duration_ms=550.0, attempts=2, eval_score=0.8,
                memory_recalls=1, keywords_matched=True,
                timestamp="2026-06-13",
            ),
            BenchmarkRun(
                case_id="c3", category="csv_recovery", difficulty="hard",
                expected_outcome="SUCCESS", actual_outcome="FAILED",
                duration_ms=2000.0, attempts=3, eval_score=0.2,
                memory_recalls=0, keywords_matched=False,
                timestamp="2026-06-13",
                error="ValueError",
            ),
        ]
        return BenchmarkReport(runs=runs)

    def test_markdown_contains_overview(self) -> None:
        md = render_markdown(self._report())
        assert "# Reforge Benchmark" in md
        assert "Total cases" in md
        assert "Passed" in md and "(67%)" in md  # 2/3

    def test_markdown_per_category_table(self) -> None:
        md = render_markdown(self._report())
        assert "## Per category" in md
        assert "csv_basic" in md and "csv_recovery" in md

    def test_markdown_per_case_table(self) -> None:
        md = render_markdown(self._report())
        assert "## Per case" in md
        assert "`c1`" in md and "`c2`" in md and "`c3`" in md
        assert "PASS" in md and "FAIL" in md

    def test_learning_curve_markdown(self) -> None:
        runs = [
            BenchmarkRun(
                case_id="case",
                category="x", difficulty="easy",
                expected_outcome="SUCCESS", actual_outcome="RECOVERED",
                duration_ms=100, attempts=2, eval_score=0.5,
                memory_recalls=0, keywords_matched=True, timestamp="t1",
            ),
            BenchmarkRun(
                case_id="case",
                category="x", difficulty="easy",
                expected_outcome="SUCCESS", actual_outcome="SUCCESS",
                duration_ms=100, attempts=1, eval_score=1.0,
                memory_recalls=2, keywords_matched=True, timestamp="t2",
            ),
        ]
        md = render_learning_curve_markdown(BenchmarkReport(runs=runs))
        assert "0.50 → 1.00" in md
        assert "Round" in md
