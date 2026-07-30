"""Smoke test for the targeted governor-efficacy harness — no live LLM.

Verifies the dataset-agnostic skeleton end to end with a stub runner that
reacts to the REFORGE_GOVERNOR_BYPASS env the driver sets per arm:
  - naive arm  → one attempt, clean exit, WRONG answer (accepts on exit 0);
  - governor arm → attempt 1 wrong (eval rejects) → retry → attempt 2 right.

So the oracle should score naive as failed and governor as recovered, and
the paired success delta should come out +1.0. This exercises arm
switching, attempt capture, oracle grading, and the paired-CI reduction
without touching a model.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

from reforge.benchmark.targeted.driver import (
    TargetedDriver,
    headline,
    per_bucket,
    success_rate,
)
from reforge.benchmark.targeted.task import EvalCase


def _state(
    *,
    stdout: str = "",
    exit_code: int | None = 0,
    eval_passed: bool = True,
    eval_score: float = 1.0,
    final_answer: str = "",
    task_outcome: str = "",
    retry_count: int = 0,
    generated_code: str = "",
) -> SimpleNamespace:
    """A duck-typed RuntimeState exposing only the attrs the driver reads."""
    return SimpleNamespace(
        generated_code=generated_code,
        exec_state=SimpleNamespace(stdout=stdout, exit_code=exit_code, stderr=""),
        semantic_state=SimpleNamespace(
            evaluation_result=SimpleNamespace(passed=eval_passed, score=eval_score)
        ),
        outcome_state=SimpleNamespace(final_answer=final_answer, task_outcome=task_outcome),
        control_state=SimpleNamespace(
            retry_count=retry_count, retry_decision_action="", policy_reason=""
        ),
        classification_result=SimpleNamespace(failure_mode=""),
    )


class _StubRunner:
    """Replays a fixed node stream, branching on the arm the driver selected."""

    def stream(self, prompt: str):
        naive = bool(os.environ.get("REFORGE_GOVERNOR_BYPASS"))
        if naive:
            # One shot, exits clean but wrong; naive accepts exit 0.
            yield "execution", _state(stdout="wrong", exit_code=0)
            yield "evaluation", _state(stdout="wrong", eval_passed=True)
            yield "final_response", _state(final_answer="wrong", task_outcome="SUCCESS")
        else:
            # Attempt 1 wrong (evaluator rejects) → retry → attempt 2 right.
            yield "execution", _state(stdout="wrong", exit_code=0)
            yield "evaluation", _state(stdout="wrong", eval_passed=False, eval_score=0.4)
            yield "execution", _state(stdout="42", exit_code=0)
            yield "evaluation", _state(stdout="42", eval_passed=True)
            yield "final_response", _state(
                final_answer="42", task_outcome="SUCCESS", retry_count=1
            )


class _OneCaseTask:
    name = "smoke"

    def load_cases(self) -> list[EvalCase]:
        return [EvalCase(case_id="c0", bucket="observable", payload={"expected": "42"})]

    def build_prompt(self, case: EvalCase) -> str:
        return "print 42"

    def grade(
        self, case: EvalCase, stdout: str, exit_code: int | None,
        generated_code: str = "",
    ) -> bool:
        if exit_code is not None and exit_code != 0:
            return False
        return stdout.strip() == case.payload["expected"]


def test_targeted_skeleton_scores_governor_recovery():
    driver = TargetedDriver(
        _OneCaseTask(), n_seeds=3, runner_factory=_StubRunner
    )
    records = driver.run()

    # 2 modes × 3 seeds × 1 case = 6 records.
    assert len(records) == 6

    gov = [r for r in records if r.mode == "governor"]
    nai = [r for r in records if r.mode == "naive"]

    # Governor recovers (2 attempts, oracle-correct final); naive fails wrong.
    assert all(r.passed and r.recovered and r.attempts == 2 for r in gov)
    assert all((not r.passed) and r.attempts == 1 for r in nai)

    # Per-seed success rates: governor 1.0, naive 0.0.
    assert success_rate([r for r in gov if r.seed == 0]) == 1.0
    assert success_rate([r for r in nai if r.seed == 0]) == 0.0

    # Paired success delta = +1.0, and with 3 identical seeds the CI is degenerate
    # but must exclude zero (governor strictly better on this constructed suite).
    stats = headline(records, n_seeds=3)
    assert stats["success_rate"].mean == 1.0
    assert stats["success_rate"].excludes_zero

    # Bucket view routes the delta under the observable-failure bucket.
    buckets = per_bucket(records, n_seeds=3)
    assert set(buckets) == {"observable"}
    assert buckets["observable"]["success_rate"].mean == 1.0


def test_env_is_restored_after_run():
    # The driver toggles REFORGE_GOVERNOR_BYPASS per arm; it must leave no trace.
    before = os.environ.get("REFORGE_GOVERNOR_BYPASS")
    TargetedDriver(_OneCaseTask(), n_seeds=1, runner_factory=_StubRunner).run()
    assert os.environ.get("REFORGE_GOVERNOR_BYPASS") == before


def test_selfheal_task_grades_gold():
    from reforge.benchmark.targeted.selfheal_task import SelfHealTask

    task = SelfHealTask()
    cases = {c.case_id: c for c in task.load_cases()}

    # stale_api sequence is adjacent (repair_hint recall depends on it).
    ids = [c.case_id for c in task.load_cases()]
    stale = [i for i in ids if i.startswith("stale_api")]
    assert ids[ids.index(stale[0]):ids.index(stale[0]) + len(stale)] == stale

    # Gold oracle: exact stdout passes, wrong value / nonzero exit fail.
    c = cases["stale_api_0"]
    assert task.grade(c, "14", 0) is True
    assert task.grade(c, "13", 0) is False
    assert task.grade(c, "14", 1) is False  # observable failure is a hard fail


def test_selfheal_numeric_tolerance_accepts_roundoff():
    from reforge.benchmark.targeted.selfheal_task import SelfHealTask

    task = SelfHealTask()
    case = {c.case_id: c for c in task.load_cases()}["inexact_numeric_0"]

    # 20/3 ≈ 6.6667: round-off variants all pass under the declared tolerance.
    assert task.grade(case, "6.6667", 0) is True     # exact
    assert task.grade(case, "6.66667", 0) is True    # 5 dp, diff 3e-5 < 1e-3
    assert task.grade(case, "6.667", 0) is True       # 3 dp, diff 3e-4 < 1e-3
    # A genuinely wrong value is still rejected.
    assert task.grade(case, "6.5", 0) is False
    # Tolerance excuses neither a nonzero exit nor a non-numeric stdout (no crash).
    assert task.grade(case, "6.6667", 1) is False
    assert task.grade(case, "not-a-number", 0) is False

    # Regression guard: the old exact-`==` oracle would have rejected the 5-dp
    # variant — the exact reason numeric answers need tolerance, not string eq.
    assert ("6.66667" == "6.6667") is False


def _write_suite(tmp_path, rows: list[str]):
    p = tmp_path / "suite.jsonl"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return p


def test_selfheal_suite_loads_from_shipped_file():
    # The shipped suite loads, and stale_api stays a contiguous leading block
    # (repair_hint recall depends on the sequence sharing one memory leg).
    from reforge.benchmark.targeted.selfheal_task import SelfHealTask

    ids = [c.case_id for c in SelfHealTask().load_cases()]
    stale = [i for i in ids if i.startswith("stale_api")]
    start = ids.index(stale[0])
    assert ids[start:start + len(stale)] == stale


def test_selfheal_suite_validation_rejects_bad_rows(tmp_path):
    import pytest

    from reforge.benchmark.targeted.selfheal_task import SelfHealTask

    good = (
        '{"case_id":"a","bucket":"b","tests_mechanism":"x",'
        '"measured_first_try_fail_rate":null,"payload":{"prompt":"p","expected_stdout":"1"}}'
    )
    # Baseline: the good row alone loads.
    assert len(SelfHealTask(suite_path=_write_suite(tmp_path, [good])).load_cases()) == 1

    # Missing provenance field (measured_first_try_fail_rate) → hard error.
    no_prov = '{"case_id":"a","bucket":"b","tests_mechanism":"x","payload":{"prompt":"p"}}'
    with pytest.raises(RuntimeError, match="provenance"):
        SelfHealTask(suite_path=_write_suite(tmp_path, [no_prov])).load_cases()

    # Duplicate case_id → hard error.
    with pytest.raises(RuntimeError, match="duplicate"):
        SelfHealTask(suite_path=_write_suite(tmp_path, [good, good])).load_cases()

    # payload without a prompt → hard error.
    no_prompt = (
        '{"case_id":"a","bucket":"b","tests_mechanism":"x",'
        '"measured_first_try_fail_rate":null,"payload":{"expected_stdout":"1"}}'
    )
    with pytest.raises(RuntimeError, match="prompt"):
        SelfHealTask(suite_path=_write_suite(tmp_path, [no_prompt])).load_cases()

    # Missing file → hard error naming the path.
    with pytest.raises(RuntimeError, match="not found"):
        SelfHealTask(suite_path=tmp_path / "nope.jsonl").load_cases()
