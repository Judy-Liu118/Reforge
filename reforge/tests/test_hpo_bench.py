"""HPO benchmark — unit tests using a mock RuntimeRunner.

We pin the public contract:
  * CV-score parser handles plain / scientific / negative / trailing whitespace
  * Plateau detection stops after `patience` non-improving trials
  * Trial grading distinguishes ok / parse_error / runtime_error
  * Best-trial tracking picks the max CV score across attempts
  * Parallel run preserves case order in the report
"""

from __future__ import annotations

import time as _t
from types import SimpleNamespace

import pytest

from reforge.runtime.hpo import (
    HpoBenchReport,
    HpoCase,
    HpoSession,
    HpoTrial,
    parse_cv_score,
    render_markdown,
)
from reforge.runtime.hpo.prompt import build_prompt, summarise_pipeline
from reforge.runtime.hpo.session import _hit_plateau


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _case(case_id="iris", *, max_trials=5, patience=3, scoring="accuracy", task="classification"):
    return HpoCase(
        case_id=case_id,
        dataset_loader="sklearn.datasets.load_iris(return_X_y=True)",
        task=task,
        n_samples=150,
        n_features=4,
        target_summary="3 classes",
        scoring=scoring,
        max_trials=max_trials,
        plateau_patience=patience,
        baseline_score=0.333,
    )


def _state(*, outcome="SUCCESS", final_answer="", retry_count=0, score=1.0, stderr=""):
    return SimpleNamespace(
        outcome_state=SimpleNamespace(task_outcome=outcome, final_answer=final_answer),
        control_state=SimpleNamespace(retry_count=retry_count),
        semantic_state=SimpleNamespace(evaluation_result=SimpleNamespace(score=score)),
        exec_state=SimpleNamespace(stderr=stderr),
    )


def _trial(idx, *, status="ok", score=None):
    return HpoTrial(
        trial_index=idx,
        status=status,
        cv_score=score,
        pipeline_summary=f"pipeline-{idx}",
        duration_ms=10.0,
        attempts=1,
    )


class _ScriptedRunner:
    """Returns pre-canned states for each .run() call (per-thread queue)."""

    def __init__(self, states):
        self._states = list(states)
        self.prompts = []

    def run(self, prompt):
        self.prompts.append(prompt)
        return self._states.pop(0) if self._states else _state()


# ---------------------------------------------------------------------------
# parse_cv_score
# ---------------------------------------------------------------------------


class TestParseCvScore:
    def test_plain(self):
        assert parse_cv_score("PIPELINE=rf\nCV_SCORE=0.9667") == pytest.approx(0.9667)

    def test_scientific(self):
        assert parse_cv_score("CV_SCORE=1.5e-2") == pytest.approx(0.015)

    def test_negative(self):
        assert parse_cv_score("CV_SCORE=-0.42") == pytest.approx(-0.42)

    def test_whitespace_around_equals(self):
        assert parse_cv_score("CV_SCORE = 0.5") == pytest.approx(0.5)

    def test_last_match_wins(self):
        # If the LLM debug-prints CV_SCORE earlier, the final value wins.
        text = "CV_SCORE=0.1\n...debug...\nCV_SCORE=0.9"
        assert parse_cv_score(text) == pytest.approx(0.9)

    def test_no_match(self):
        assert parse_cv_score("hello world") is None

    def test_empty(self):
        assert parse_cv_score("") is None


# ---------------------------------------------------------------------------
# summarise_pipeline
# ---------------------------------------------------------------------------


class TestSummarisePipeline:
    def test_takes_pipeline_line(self):
        text = "PIPELINE=RandomForest n_estimators=200\nCV_SCORE=0.85"
        assert summarise_pipeline(text) == "RandomForest n_estimators=200"

    def test_falls_back_when_missing(self):
        text = "CV_SCORE=0.5"
        assert summarise_pipeline(text, fallback="LogisticRegression") == "LogisticRegression"


# ---------------------------------------------------------------------------
# build_prompt
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    def test_contains_required_fields(self):
        text = build_prompt(_case(), history=[], trial_index=1)
        assert "iris" in text
        assert "sklearn.datasets.load_iris" in text
        assert "scoring metric: `accuracy`" in text
        assert "trial 1 of at most 5" in text
        assert "CV_SCORE=" in text

    def test_history_includes_prior_scores(self):
        history = [_trial(1, score=0.95), _trial(2, status="parse_error")]
        text = build_prompt(_case(), history=history, trial_index=3)
        assert "trial 1: score=0.9500" in text
        assert "trial 2: NO CV_SCORE printed" in text


# ---------------------------------------------------------------------------
# _hit_plateau
# ---------------------------------------------------------------------------


class TestPlateau:
    def test_no_best_yet_never_stops(self):
        history = [_trial(1, status="parse_error"), _trial(2, status="parse_error")]
        assert _hit_plateau(history, patience=2, best_idx=None) is False

    def test_triggers_after_patience_non_improving(self):
        history = [_trial(1, score=0.9), _trial(2, score=0.8), _trial(3, score=0.7)]
        # best at idx 1, then 2 trials with no improvement, patience=2 → stop
        assert _hit_plateau(history, patience=2, best_idx=1) is True

    def test_not_triggered_below_patience(self):
        history = [_trial(1, score=0.9), _trial(2, score=0.85)]
        assert _hit_plateau(history, patience=2, best_idx=1) is False

    def test_patience_zero_disables(self):
        history = [_trial(1, score=0.5), _trial(2, score=0.4), _trial(3, score=0.3)]
        assert _hit_plateau(history, patience=0, best_idx=1) is False


# ---------------------------------------------------------------------------
# Session grading + trial loop
# ---------------------------------------------------------------------------


class TestSessionSingleCase:
    def test_single_successful_trial_then_plateau(self):
        # trial 1 returns 0.95 then 2 mediocre trials → plateau with patience=2
        case = _case(max_trials=5, patience=2)
        states = [
            _state(final_answer="PIPELINE=RandomForest\nCV_SCORE=0.95"),
            _state(final_answer="PIPELINE=KNN\nCV_SCORE=0.92"),
            _state(final_answer="PIPELINE=NaiveBayes\nCV_SCORE=0.90"),
        ]
        runner = _ScriptedRunner(states)
        sess = HpoSession(runner_factory=lambda: runner)
        report = sess.run([case])

        run = report.runs[0]
        assert len(run.trials) == 3
        assert run.best_trial_index == 1
        assert run.best_cv_score == pytest.approx(0.95)
        assert run.stopped_reason == "plateau"

    def test_all_failed_when_no_score_parsed(self):
        case = _case(max_trials=3, patience=10)
        states = [
            _state(final_answer="oops no score"),
            _state(final_answer="still nothing", outcome="FAILED", stderr="NameError"),
            _state(final_answer="really nothing"),
        ]
        runner = _ScriptedRunner(states)
        sess = HpoSession(runner_factory=lambda: runner)
        report = sess.run([case])
        run = report.runs[0]
        assert run.best_cv_score is None
        assert run.stopped_reason == "all_failed"
        assert {t.status for t in run.trials} == {"parse_error", "runtime_error"}

    def test_runs_full_budget_when_each_trial_improves(self):
        case = _case(max_trials=3, patience=3)
        states = [
            _state(final_answer=f"CV_SCORE={s}") for s in (0.5, 0.6, 0.7)
        ]
        runner = _ScriptedRunner(states)
        report = HpoSession(runner_factory=lambda: runner).run([case])
        run = report.runs[0]
        assert run.best_trial_index == 3
        assert run.best_cv_score == pytest.approx(0.7)
        assert run.stopped_reason == "max_trials"

    def test_aggregate_metrics(self):
        case = _case(max_trials=3, patience=10)
        states = [
            _state(final_answer="CV_SCORE=0.8"),
            _state(final_answer="no score"),
            _state(final_answer="CV_SCORE=0.85"),
        ]
        runner = _ScriptedRunner(states)
        report = HpoSession(runner_factory=lambda: runner).run([case])
        assert report.total_trials == 3
        assert report.total_successful_trials == 2
        assert report.trial_success_rate == pytest.approx(2 / 3)
        assert report.cases_with_a_score == 1


# ---------------------------------------------------------------------------
# Parallel execution
# ---------------------------------------------------------------------------


class TestParallel:
    def test_preserves_case_order(self):
        cases = [_case(f"c{i}", max_trials=1, patience=10) for i in range(4)]

        class _SlowRunner:
            def run(self, prompt):
                _t.sleep(0.05)
                return _state(final_answer="CV_SCORE=0.5")

        sess = HpoSession(runner_factory=lambda: _SlowRunner(), max_workers=4)
        started = _t.perf_counter()
        report = sess.run(cases)
        elapsed = _t.perf_counter() - started

        assert [r.case_id for r in report.runs] == ["c0", "c1", "c2", "c3"]
        assert all(r.best_cv_score == pytest.approx(0.5) for r in report.runs)
        # Sequential would be ~0.2s; parallel x4 should be well under that.
        assert elapsed < 0.15


# ---------------------------------------------------------------------------
# Reporter
# ---------------------------------------------------------------------------


class TestReporter:
    def test_markdown_has_required_sections(self):
        case = _case(max_trials=2, patience=10)
        states = [
            _state(final_answer="PIPELINE=RF\nCV_SCORE=0.9"),
            _state(final_answer="PIPELINE=KNN\nCV_SCORE=0.7"),
        ]
        runner = _ScriptedRunner(states)
        report = HpoSession(runner_factory=lambda: runner).run([case])
        md = render_markdown(report, title="Test report")

        assert md.startswith("# Test report")
        assert "## Summary" in md
        assert "## Per case" in md
        assert "## Trial details" in md
        assert "`iris`" in md
        assert "0.9000" in md  # best score

    def test_empty_report(self):
        md = render_markdown(HpoBenchReport())
        assert "Cases: **0**" in md
