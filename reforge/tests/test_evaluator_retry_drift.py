"""retry_drift — the same error_type repeating across consecutive attempts.

The check had no test coverage at all before the magic `2` was lifted into
`DRIFT_REPEAT_THRESHOLD`. These tests pin both the behaviour and the wiring:
the constant is read at all three sites (the length guard, the slice, and the
count guard), so overriding it actually moves the window rather than leaving a
stale literal behind.
"""

from __future__ import annotations

from reforge.runtime.domain.state.models import (
    AttemptRecord,
    ExecutionState,
    ReflectionResult,
    RuntimeState,
    SemanticState,
)
from reforge.runtime.orchestration.evaluation.heuristics import HeuristicEvaluator

DATA_REQUEST = "analyze the csv and compute totals"


def _state(
    error_types: list[str],
    *,
    current: str = "KeyError",
    request: str = DATA_REQUEST,
    exit_code: int = 1,
):
    return RuntimeState(
        user_request=request,
        exec_state=ExecutionState(stdout="x", stderr="", exit_code=exit_code),
        semantic_state=SemanticState(
            reflection_result=ReflectionResult(error_type=current),
        ),
        attempts=[
            AttemptRecord(attempt=i + 1, error_type=et)
            for i, et in enumerate(error_types)
        ],
    )


def _drifted(result) -> bool:
    return any(c.name == "retry_drift" and not c.passed for c in result.checks)


def _evaluate(state, evaluator: HeuristicEvaluator | None = None):
    return (evaluator or HeuristicEvaluator()).evaluate(state)


# --- default window ----------------------------------------------------------


def test_two_identical_errors_trip_drift():
    result = _evaluate(_state(["KeyError", "KeyError"]))
    assert _drifted(result)


def test_nonzero_exit_outranks_drift_in_failure_type():
    # Both checks fail, but _classify_failure reports clean_exit first: a
    # non-zero exit is the more fundamental description of what went wrong.
    # This is why `retry_drift` as a *failure_type* is rare in practice — the
    # drift check almost always fires alongside a failed run.
    result = _evaluate(_state(["KeyError", "KeyError"], exit_code=1))
    assert _drifted(result)
    assert result.failure_type == "execution_failed"


def test_drift_surfaces_as_failure_type_on_a_clean_exit():
    # Exit 0 but the reflection from the previous attempt still reports the
    # same error_type — here nothing outranks drift.
    result = _evaluate(_state(["KeyError", "KeyError"], exit_code=0))
    assert _drifted(result)
    assert result.failure_type == "retry_drift"


def test_single_attempt_does_not_trip_drift():
    # One failure is just a failure — there is no repetition to observe yet.
    assert not _drifted(_evaluate(_state(["KeyError"])))


def test_differing_recent_error_does_not_trip_drift():
    # Progress: the second attempt failed differently, so codegen is moving.
    assert not _drifted(_evaluate(_state(["KeyError", "ValueError"])))


def test_only_the_last_window_matters():
    # Early KeyErrors are irrelevant once the recent window disagrees.
    assert not _drifted(_evaluate(_state(["KeyError", "KeyError", "ValueError"])))


def test_intentional_error_task_is_exempt():
    # A task whose whole point is to raise repeats the same error by design.
    state = _state(["KeyError", "KeyError"], request="故意报错，演示 traceback")
    assert not _drifted(_evaluate(state))


def test_missing_reflection_error_type_is_skipped():
    # Nothing to compare against — the check must not guess.
    state = _state(["KeyError", "KeyError"], current="")
    assert not _drifted(_evaluate(state))


# --- the constant is wired, not decorative -----------------------------------


class _WiderWindow(HeuristicEvaluator):
    DRIFT_REPEAT_THRESHOLD = 3


def test_raising_the_threshold_widens_the_window():
    # Two repeats no longer suffice; three do. If any of the three call sites
    # had kept a hardcoded 2, the first assertion would fail.
    evaluator = _WiderWindow()
    assert not _drifted(_evaluate(_state(["KeyError", "KeyError"]), evaluator))
    assert _drifted(_evaluate(_state(["KeyError"] * 3), evaluator))
