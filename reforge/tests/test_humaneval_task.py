"""Interface tests for HumanEvalTask — no LLM, no tokens.

These prove the program-graded oracle is wired correctly *before* any real
run spends tokens: the reference solution must pass its own held-out tests,
a wrong-but-named function must fail, and empty code must fail. If the
benchmark data is absent the whole module skips (fetch_humaneval.py).
"""

from __future__ import annotations

import pytest

from reforge.benchmark.targeted.humaneval_task import HumanEvalTask, _DATA_PATH
from reforge.benchmark.targeted.task import EvalCase, EvalTask

pytestmark = pytest.mark.skipif(
    not _DATA_PATH.exists(),
    reason="HumanEval data not fetched (scripts/fetch_humaneval.py)",
)


def _first_cases(n: int = 3) -> list[EvalCase]:
    return HumanEvalTask(limit=n).load_cases()


def test_is_evaltask() -> None:
    assert isinstance(HumanEvalTask(), EvalTask)


def test_load_cases_shape() -> None:
    cases = _first_cases(3)
    assert len(cases) == 3
    for c in cases:
        assert c.case_id.startswith("HumanEval/")
        assert c.bucket == "humaneval"
        assert {"prompt", "entry_point", "test", "canonical_solution"} <= set(c.payload)


def test_build_prompt_names_entry_point() -> None:
    case = _first_cases(1)[0]
    prompt = HumanEvalTask().build_prompt(case)
    assert case.payload["entry_point"] in prompt
    # The signature/docstring must survive into the prompt verbatim.
    assert case.payload["prompt"].strip() in prompt


def test_reference_solution_passes_its_own_tests() -> None:
    """canonical_solution is a body; prompt+body is the full function the
    model is asked to produce. That composite MUST pass the held-out tests —
    otherwise the oracle wiring (not the model) is wrong."""
    task = HumanEvalTask()
    for case in _first_cases(5):
        full = case.payload["prompt"] + case.payload["canonical_solution"]
        assert task.grade(case, stdout="", exit_code=0, generated_code=full) is True


def test_main_block_does_not_affect_grade() -> None:
    """The prompt asks for a `__main__` demo block (to give the runtime a
    non-empty stdout). Grading imports the module, so that block must NOT run —
    a correct function passes even if its demo block would crash."""
    task = HumanEvalTask()
    case = _first_cases(1)[0]
    full = case.payload["prompt"] + case.payload["canonical_solution"]
    sabotaged = full + '\n\nif __name__ == "__main__":\n    raise RuntimeError("boom")\n'
    assert task.grade(case, stdout="", exit_code=0, generated_code=sabotaged) is True


def test_wrong_solution_fails() -> None:
    task = HumanEvalTask()
    case = _first_cases(1)[0]
    entry = case.payload["entry_point"]
    stub = f"def {entry}(*args, **kwargs):\n    return None\n"
    assert task.grade(case, stdout="", exit_code=0, generated_code=stub) is False


def test_empty_code_fails() -> None:
    task = HumanEvalTask()
    case = _first_cases(1)[0]
    assert task.grade(case, stdout="whatever", exit_code=0, generated_code="") is False
