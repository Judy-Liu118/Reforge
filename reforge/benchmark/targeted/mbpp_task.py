"""MBPP as an EvalTask — same program-graded oracle shape as HumanEval.

MBPP (sanitized, 427 tasks) is the harder-suite counterpart to HumanEval: a
natural-language spec, a reference `code`, and a held-out `test_list` of
asserts. The oracle imports the generated module and runs those asserts —
identical machinery to HumanEvalTask, so the `__main__` demo block stays
dormant during grading (see humaneval_task for the rationale).

Deliberate design choice for measuring the evaluator's false positives:
MBPP hides the function name only in `test_list`, and handing the whole
test_list to the model would leak the gold and make the task trivial (few
wrong answers, so few FP opportunities — the same "too easy" dead end
HumanEval hit). Instead we extract the entry-point *name* from the reference
`code` so the model can name its function correctly, but keep every assert as
hidden gold. A model that writes a function that merely looks right on the
description — and fails a held-out assert — is exactly the wrong answer the
no-gold evaluator may wave through.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from reforge.benchmark.targeted.task import EvalCase

_DATA_PATH = (
    Path(__file__).resolve().parents[3] / "benchmark_data" / "sanitized-mbpp.json"
)
_EXEC_TIMEOUT_S = 10


def _extract_entry(code: str, test_list: list[str]) -> str | None:
    """The entry point is the code-defined function the tests actually call.

    Reference `code` may define helpers; pick the `def` name that appears in
    the test asserts, so a solution's helper functions don't shadow it.
    """
    defs = re.findall(r"^\s*def\s+(\w+)", code, re.MULTILINE)
    joined = "\n".join(test_list)
    for name in defs:
        if re.search(rf"\b{re.escape(name)}\s*\(", joined):
            return name
    return defs[0] if defs else None


class MbppTask:
    """The sanitized MBPP suite behind the EvalTask protocol. `name` + 3 seams."""

    name = "mbpp"

    def __init__(
        self,
        *,
        data_path: Path | None = None,
        task_ids: tuple[int, ...] | None = None,
        limit: int | None = None,
    ) -> None:
        self._data_path = data_path or _DATA_PATH
        self._task_ids = task_ids
        self._limit = limit

    def load_cases(self) -> list[EvalCase]:
        if not self._data_path.exists():
            raise RuntimeError(
                f"MBPP data not found at {self._data_path}. Fetch it with "
                "scripts/fetch_mbpp.py (google-research/mbpp, sanitized)."
            )
        data = json.loads(self._data_path.read_text(encoding="utf-8"))
        cases: list[EvalCase] = []
        for row in data:
            tid = row["task_id"]
            if self._task_ids is not None and tid not in self._task_ids:
                continue
            entry = _extract_entry(row["code"], row["test_list"])
            if entry is None:
                raise RuntimeError(
                    f"MBPP task {tid}: could not extract an entry-point name "
                    "from the reference code."
                )
            cases.append(EvalCase(
                case_id=f"Mbpp/{tid}",
                difficulty="n/a",
                bucket="mbpp",
                payload={
                    "prompt": row["prompt"],
                    "entry_point": entry,
                    "test_list": row["test_list"],
                    "test_imports": row["test_imports"],
                    "code": row["code"],
                },
            ))
        if self._limit is not None:
            cases = cases[: self._limit]
        if not cases:
            raise RuntimeError(
                f"MBPP selection is empty (task_ids={self._task_ids}, "
                f"limit={self._limit}) — nothing to run."
            )
        return cases

    def build_prompt(self, case: EvalCase) -> str:
        entry = case.payload["entry_point"]
        spec = case.payload["prompt"]
        return (
            "请用 Python 实现一个函数完成下面的需求。要求：\n"
            f"1. 函数名必须是 `{entry}`；\n"
            "2. 给出完整、可直接运行的函数定义，需要的 import 一并写出；\n"
            f"3. 在文件末尾加一个 `if __name__ == \"__main__\":` 块，用你自己构造的"
            f"输入调用 `{entry}`，并按 `输入 -> 返回值` 的形式把输入和结果一起 print "
            "出来，每个示例一行。\n\n"
            f"需求：{spec}"
        )

    def grade(
        self,
        case: EvalCase,
        stdout: str,
        exit_code: int | None,
        generated_code: str = "",
    ) -> bool:
        """Program-graded: import the solution, run the held-out asserts.

        stdout/exit_code are ignored. The `__main__` demo block does not run
        under import, so demo prints never affect gold.
        """
        if not generated_code.strip():
            return False
        return self._passes(
            generated_code, case.payload["test_imports"], case.payload["test_list"]
        )

    @staticmethod
    def _passes(solution_code: str, test_imports: list[str], test_list: list[str]) -> bool:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "solution.py").write_text(solution_code, encoding="utf-8")
            runner = (
                "from solution import *\n"
                + "\n".join(test_imports)
                + "\n"
                + "\n".join(test_list)
                + "\n"
            )
            try:
                proc = subprocess.run(
                    [sys.executable, "-c", runner],
                    cwd=d,
                    capture_output=True,
                    timeout=_EXEC_TIMEOUT_S,
                )
            except subprocess.TimeoutExpired:
                return False
            except Exception:  # noqa: BLE001 — a broken invocation is a fail, not a crash
                return False
        return proc.returncode == 0
