"""Interface tests for MbppTask — no LLM, no tokens.

Same contract as the HumanEval tests: the reference `code` must pass its own
held-out `test_list`, a wrong-but-named function must fail, empty code fails,
and the `__main__` demo block must not affect grading. Skips if the data is
absent (scripts/fetch_mbpp.py).
"""

from __future__ import annotations

import pytest

from reforge.benchmark.targeted.mbpp_task import MbppTask, _DATA_PATH, _extract_entry
from reforge.benchmark.targeted.task import EvalCase, EvalTask

pytestmark = pytest.mark.skipif(
    not _DATA_PATH.exists(),
    reason="MBPP data not fetched (scripts/fetch_mbpp.py)",
)


def _first_cases(n: int = 5) -> list[EvalCase]:
    return MbppTask(limit=n).load_cases()


def test_is_evaltask() -> None:
    assert isinstance(MbppTask(), EvalTask)


def test_load_cases_shape() -> None:
    cases = _first_cases(5)
    assert len(cases) == 5
    for c in cases:
        assert c.case_id.startswith("Mbpp/")
        assert c.bucket == "mbpp"
        assert {"prompt", "entry_point", "test_list", "test_imports", "code"} <= set(c.payload)
        assert c.payload["entry_point"]


def test_prompt_names_entry_but_hides_tests() -> None:
    case = _first_cases(1)[0]
    prompt = MbppTask().build_prompt(case)
    assert case.payload["entry_point"] in prompt
    # The held-out asserts must NOT leak into the prompt (they are the gold).
    for assertion in case.payload["test_list"]:
        assert assertion not in prompt


def test_extract_entry_skips_wrapper_calls() -> None:
    # `set(similar_elements(...))` — the entry is the inner call, not `set`.
    code = "def similar_elements(a, b):\n    return tuple(set(a) & set(b))\n"
    tests = ["assert set(similar_elements((3,4),(4,5))) == set((4,))"]
    assert _extract_entry(code, tests) == "similar_elements"


def test_reference_code_passes_its_own_tests() -> None:
    """The reference solution must pass its held-out asserts — otherwise the
    oracle wiring (import + from-import-* + asserts) is wrong, not the model."""
    task = MbppTask()
    for case in _first_cases(8):
        code = case.payload["code"]
        assert task.grade(case, stdout="", exit_code=0, generated_code=code) is True


def test_main_block_does_not_affect_grade() -> None:
    task = MbppTask()
    case = _first_cases(1)[0]
    sabotaged = case.payload["code"] + '\n\nif __name__ == "__main__":\n    raise RuntimeError("boom")\n'
    assert task.grade(case, stdout="", exit_code=0, generated_code=sabotaged) is True


def test_wrong_solution_fails() -> None:
    task = MbppTask()
    case = _first_cases(1)[0]
    entry = case.payload["entry_point"]
    stub = f"def {entry}(*args, **kwargs):\n    return None\n"
    assert task.grade(case, stdout="", exit_code=0, generated_code=stub) is False


def test_empty_code_fails() -> None:
    task = MbppTask()
    case = _first_cases(1)[0]
    assert task.grade(case, stdout="x", exit_code=0, generated_code="") is False
