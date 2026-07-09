"""EDA application — unit tests using a mock RuntimeRunner.

We never hit a real LLM here; the live integration is exercised by the
docs/eda_*.md sample reports generated separately. These tests pin:

  - EdaStage / EdaStageResult / EdaReport shape
  - EdaSession dispatch via mock runner
  - reporter Markdown structure
  - CLI subset filter via --stages
  - failure / recovery / hard-fail mapping from RuntimeState outcome
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from reforge.runtime.eda import (
    DEFAULT_STAGES,
    EdaReport,
    EdaSession,
    EdaStageResult,
    render_markdown,
)
from reforge.runtime.eda.session import _result_from_state


# ---------------------------------------------------------------------------
# Mock runner: returns a RuntimeState-shaped object whose nested attrs match
# what _result_from_state inspects. We deliberately mimic the duck shape
# instead of importing RuntimeState so the test is fast + isolated.
# ---------------------------------------------------------------------------


def _state(
    *,
    outcome: str = "SUCCESS",
    retry_count: int = 0,
    score: float = 1.0,
    final_answer: str = "ok",
    stderr: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        outcome_state=SimpleNamespace(
            task_outcome=outcome,
            final_answer=final_answer,
        ),
        control_state=SimpleNamespace(retry_count=retry_count),
        semantic_state=SimpleNamespace(
            evaluation_result=SimpleNamespace(score=score),
        ),
        exec_state=SimpleNamespace(stderr=stderr),
    )


class _ScriptedRunner:
    """Returns a queued sequence of RuntimeStates, one per run() call."""

    def __init__(self, queue: list) -> None:
        self._queue = list(queue)
        self.prompts: list[str] = []

    def run(self, prompt: str):
        self.prompts.append(prompt)
        if not self._queue:
            return _state()
        return self._queue.pop(0)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestModels:
    def test_default_stages_count(self) -> None:
        # If someone removes a stage we want a test to fail loudly.
        assert len(DEFAULT_STAGES) == 8
        ids = [s.id for s in DEFAULT_STAGES]
        assert ids == [
            "overview",
            "dtypes",
            "missing",
            "numeric_stats",
            "categorical_freq",
            "correlation",
            "outliers",
            "quality_warnings",
        ]

    def test_each_stage_has_prompt(self) -> None:
        for s in DEFAULT_STAGES:
            assert "{csv}" in s.prompt_template, f"{s.id} missing {{csv}} placeholder"
            assert s.title and s.description

    def test_report_aggregations(self) -> None:
        rep = EdaReport(
            dataset_path="x.csv",
            stages=[
                EdaStageResult(stage_id="a", status="ok", attempts=1, duration_ms=10),
                EdaStageResult(stage_id="b", status="recovered", attempts=2, duration_ms=20),
                EdaStageResult(stage_id="c", status="failed", attempts=4, duration_ms=30),
            ],
            total_duration_ms=60.0,
        )
        assert rep.stage_count == 3
        assert rep.ok_count == 1
        assert rep.recovered_count == 1
        assert rep.failed_count == 1
        assert rep.total_attempts == 7


# ---------------------------------------------------------------------------
# _result_from_state — outcome mapping
# ---------------------------------------------------------------------------


class TestOutcomeMapping:
    def test_success_maps_to_ok(self) -> None:
        stage = DEFAULT_STAGES[0]
        r = _result_from_state(stage, _state(outcome="SUCCESS"), duration_ms=100)
        assert r.status == "ok"

    def test_recovered_maps_to_recovered(self) -> None:
        stage = DEFAULT_STAGES[0]
        r = _result_from_state(stage, _state(outcome="RECOVERED", retry_count=2), duration_ms=100)
        assert r.status == "recovered"
        assert r.attempts == 3

    def test_failed_maps_to_failed(self) -> None:
        stage = DEFAULT_STAGES[0]
        r = _result_from_state(
            stage,
            _state(outcome="FAILED", stderr="ValueError: oops"),
            duration_ms=100,
        )
        assert r.status == "failed"
        assert "oops" in r.error

    def test_denied_treated_as_failed(self) -> None:
        stage = DEFAULT_STAGES[0]
        r = _result_from_state(stage, _state(outcome="DENIED"), duration_ms=100)
        assert r.status == "failed"


# ---------------------------------------------------------------------------
# EdaSession dispatch
# ---------------------------------------------------------------------------


class TestEdaSession:
    def test_run_uses_each_stage_prompt(self, tmp_path: Path) -> None:
        csv = tmp_path / "x.csv"
        csv.write_text("a,b\n1,2\n", encoding="utf-8")

        runner = _ScriptedRunner([_state(final_answer=f"out{i}") for i in range(8)])
        session = EdaSession(runner_factory=lambda: runner)
        report = session.run(csv)

        assert len(runner.prompts) == 8
        # Every prompt mentions the resolved CSV path
        for p in runner.prompts:
            assert str(csv.resolve()).replace("\\", "/") in p
        # Outputs threaded through
        assert [r.output for r in report.stages] == [f"out{i}" for i in range(8)]
        assert report.failed_count == 0

    def test_missing_csv_raises(self) -> None:
        session = EdaSession(runner_factory=lambda: _ScriptedRunner([]))
        with pytest.raises(FileNotFoundError):
            session.run("does_not_exist.csv")

    def test_stage_subset(self, tmp_path: Path) -> None:
        csv = tmp_path / "x.csv"
        csv.write_text("a\n1\n", encoding="utf-8")

        subset = [DEFAULT_STAGES[0], DEFAULT_STAGES[2]]
        runner = _ScriptedRunner([_state() for _ in subset])
        session = EdaSession(runner_factory=lambda: runner, stages=subset)
        report = session.run(csv)

        assert [r.stage_id for r in report.stages] == ["overview", "missing"]

    def test_recovered_outcome_propagates(self, tmp_path: Path) -> None:
        csv = tmp_path / "x.csv"
        csv.write_text("a\n1\n", encoding="utf-8")

        states = [_state(outcome="RECOVERED", retry_count=1) for _ in DEFAULT_STAGES]
        runner = _ScriptedRunner(states)
        report = EdaSession(runner_factory=lambda: runner).run(csv)
        assert report.recovered_count == len(DEFAULT_STAGES)
        assert report.total_attempts == 2 * len(DEFAULT_STAGES)


# ---------------------------------------------------------------------------
# Reporter
# ---------------------------------------------------------------------------


class TestReporter:
    def test_markdown_contains_all_sections(self) -> None:
        rep = EdaReport(
            dataset_path="data/x.csv",
            stages=[
                EdaStageResult(
                    stage_id="overview",
                    status="ok",
                    attempts=1,
                    duration_ms=120.0,
                    output="rows: 10",
                    eval_score=1.0,
                ),
                EdaStageResult(
                    stage_id="missing",
                    status="recovered",
                    attempts=2,
                    duration_ms=300.0,
                    output="No missing values.",
                    eval_score=1.0,
                ),
            ],
            total_duration_ms=420.0,
        )
        md = render_markdown(rep)
        assert "# EDA report: x.csv" in md
        assert "## Overview" in md
        assert "## Per stage" in md
        assert "## Stage outputs" in md
        # both stage outputs surface
        assert "rows: 10" in md
        assert "No missing values." in md
        # self-healing footer reports retry count
        assert "**1** extra attempt(s)" in md

    def test_failed_stage_renders_error_block(self) -> None:
        rep = EdaReport(
            dataset_path="x.csv",
            stages=[
                EdaStageResult(
                    stage_id="missing",
                    status="failed",
                    attempts=4,
                    duration_ms=30.0,
                    error="UnicodeDecodeError: gbk",
                ),
            ],
            total_duration_ms=30.0,
        )
        md = render_markdown(rep)
        assert "FAILED" in md
        assert "UnicodeDecodeError" in md


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCli:
    def test_stages_flag_filters(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from reforge.runtime.eda import __main__ as cli

        csv = tmp_path / "x.csv"
        csv.write_text("a\n1\n", encoding="utf-8")
        out = tmp_path / "report.md"

        captured: dict = {}

        class _Session:
            def __init__(self, **kw): captured["kw"] = kw
            def run(self, path):
                stages = captured["kw"]["stages"]
                return EdaReport(
                    dataset_path=str(path),
                    stages=[
                        EdaStageResult(
                            stage_id=s.id, status="ok",
                            attempts=1, duration_ms=1.0, output="ok",
                            eval_score=1.0,
                        )
                        for s in stages
                    ],
                    total_duration_ms=1.0,
                )

        monkeypatch.setattr(cli, "EdaSession", _Session)
        rc = cli.main([str(csv), "--out", str(out), "--stages", "overview,missing"])
        assert rc == 0
        assert out.exists()

        kw_stages = captured["kw"]["stages"]
        assert [s.id for s in kw_stages] == ["overview", "missing"]
