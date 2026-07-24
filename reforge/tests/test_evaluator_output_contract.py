"""Output-contract awareness in HeuristicEvaluator (KNOWN_LIMITATIONS L6 fix).

When the request pins the exact stdout shape ("print nothing else", "output
only ...", "只输出..."), brevity is compliance: a one-character scalar can be
the complete correct answer, so the length/digit plausibility checks are
suspended. Everything else — emptiness, tracebacks, non-zero exit — must
keep failing exactly as before. Calibrated on held-out BIRD questions
(docs/eval/EVALUATOR_CALIBRATION.md), never on the Phase 1 picks.
"""

from __future__ import annotations

import pytest

from reforge.runtime.domain.state.models import (
    ExecutionState,
    RuntimeState,
    SemanticState,
)
from reforge.runtime.orchestration.evaluation.heuristics import HeuristicEvaluator

CONTRACT_REQUEST = (
    "Run a SQL query and print rows one per line, fields joined by ' | '. "
    "Print nothing else (no headers, no preamble, no trailing summary). "
    "Question: How many members joined the count of clubs on average?"
)


def _evaluate(stdout: str, *, user_request: str, exit_code: int = 0):
    state = RuntimeState(
        user_request=user_request,
        exec_state=ExecutionState(stdout=stdout, stderr="", exit_code=exit_code),
        semantic_state=SemanticState(),
    )
    return HeuristicEvaluator().evaluate(state)


# --- the fix: contract makes short answers acceptable -----------------------


def test_bare_scalar_passes_under_output_contract():
    result = _evaluate("5\n", user_request=CONTRACT_REQUEST)
    assert result.passed, [c for c in result.checks if not c.passed]


def test_non_numeric_short_answer_passes_under_output_contract():
    # A legitimate BIRD answer can be a single dash-like cell value.
    result = _evaluate("-\n", user_request=CONTRACT_REQUEST)
    assert result.passed, [c for c in result.checks if not c.passed]


@pytest.mark.parametrize("phrase", [
    "Print nothing else after the rows.",
    "Output only the number.",
    "print only the result",
    "只输出最终结果",
])
def test_contract_phrases_detected(phrase: str):
    result = _evaluate("7\n", user_request=f"Count the items. {phrase}")
    assert result.passed, [c for c in result.checks if not c.passed]


# --- new behavior: length no longer gates a non-empty freeform answer --------


def test_short_freeform_output_no_longer_fails_on_length():
    # output_not_empty now checks presence, not length: it is the only guard
    # against a clean exit with no stdout, so it must not double as a
    # short-output floor. A brief answer to a plain (non-data, non-research)
    # task is no longer failed just for being short — length/quality is left to
    # output_contains_data / research_output_quality, which know each task type.
    result = _evaluate("ok\n", user_request="Reply with a short status word.")
    assert result.passed, [c for c in result.checks if not c.passed]
    assert all(c.passed for c in result.checks if c.name == "output_not_empty")


# --- a zero result is a legitimate answer, not a suspicious one --------------

ZERO_REQUEST = (
    "What is the average net change across accounts? "
    "Print nothing else, output only the number."
)


@pytest.mark.parametrize("stdout", ["0\n", "0.0\n", "0.00\n", "0.000\n", "0,000\n"])
def test_zero_average_is_not_suspicious(stdout: str):
    # An average/count of 0 is a real answer ("net change", "统计…差额"), and
    # under an output contract stdout is exactly "0". Flagging it burned a
    # retry that re-derived the same 0. Note 0.000 / 0,000 were never in
    # SUSPICIOUS_NUMERIC — a separate float(...) == 0 branch caught them, so
    # both had to go for the zero to actually pass.
    result = _evaluate(stdout, user_request=ZERO_REQUEST)
    assert result.passed, [c for c in result.checks if not c.passed]
    assert all(c.name != "suspicious_result" for c in result.checks)


def test_zero_count_in_chinese_stat_task_is_not_suspicious():
    result = _evaluate("0\n", user_request="统计亏损账户的平均余额，只输出最终结果")
    assert result.passed, [c for c in result.checks if not c.passed]


@pytest.mark.parametrize("stdout", ["nan\n", "inf\n", "-inf\n"])
def test_broken_computation_values_still_flagged(stdout: str):
    # These can only come from an empty series / divide-by-zero — still a bug.
    result = _evaluate(stdout, user_request=ZERO_REQUEST)
    assert not result.passed
    assert any(c.name == "suspicious_result" and not c.passed for c in result.checks)
    assert result.failure_type == "suspicious_result"


@pytest.mark.parametrize("stdout", ["None\n", "NaN\n", "Inf\n", "-Inf\n"])
def test_capitalised_broken_values_are_flagged(stdout: str):
    # Regression: the lookup was case-sensitive against an all-lowercase set,
    # so `None` — what Python actually prints for a function that forgot to
    # return — slipped through. nan/inf matched only because Python happens to
    # print them lowercase.
    result = _evaluate(stdout, user_request=ZERO_REQUEST)
    assert not result.passed
    assert any(c.name == "suspicious_result" and not c.passed for c in result.checks)


# --- unchanged behavior everywhere else --------------------------------------


def test_empty_output_still_fails_under_contract():
    result = _evaluate("", user_request=CONTRACT_REQUEST)
    assert not result.passed
    assert any(c.name == "output_not_empty" and not c.passed for c in result.checks)


def test_traceback_still_fails_under_contract():
    stdout = (
        'Traceback (most recent call last):\n'
        '  File "gen.py", line 3, in <module>\n'
        "KeyError: 0\n"
    )
    result = _evaluate(stdout, user_request=CONTRACT_REQUEST)
    assert not result.passed


def test_nonzero_exit_still_fails_under_contract():
    result = _evaluate("5\n", user_request=CONTRACT_REQUEST, exit_code=1)
    assert not result.passed
    assert any(c.name == "clean_exit" and not c.passed for c in result.checks)


def test_data_task_digit_check_still_fires_without_contract():
    # Data-oriented freeform task with brief, digitless output keeps failing.
    result = _evaluate("done\n", user_request="analyze the csv and calculate stats")
    assert any(
        c.name in {"output_not_empty", "output_contains_data"} and not c.passed
        for c in result.checks
    )
