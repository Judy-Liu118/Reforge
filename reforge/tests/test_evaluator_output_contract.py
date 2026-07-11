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
FREEFORM_REQUEST = "Calculate the average count of members per club and explain."


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


# --- unchanged behavior everywhere else --------------------------------------


def test_short_output_still_fails_without_contract():
    result = _evaluate("ok\n", user_request=FREEFORM_REQUEST)
    assert not result.passed
    assert any(c.name == "output_not_empty" and not c.passed for c in result.checks)


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
