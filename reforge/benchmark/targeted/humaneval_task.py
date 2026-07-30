"""HumanEval as an EvalTask — a *program-graded* gold oracle.

This is the third oracle shape on the same three-seam boundary, and the one
that motivated giving `grade` the `generated_code` argument. SelfHeal grades
by exact stdout, SQL by result-set equivalence — both judge the runtime's
*output*. HumanEval judges the *program itself*: it runs the benchmark's
held-out unit-test suite (`check(candidate)`) against the generated function
and passes iff every assertion holds. The stdout the runtime printed is
irrelevant to correctness here, which is precisely why this oracle is a
strong, gold-based counterweight to the runtime's no-gold heuristic
evaluator — the pairing that lets the driver count false positives
(eval_passed ∧ ¬oracle_correct), the phenomenon Reflexion quantifies as the
FP column of its Table 2.

Data: benchmark_data/HumanEval.jsonl (the official 164-task release,
task_id / prompt / entry_point / canonical_solution / test), fetched from
openai/human-eval (MIT). `canonical_solution` is not needed to grade — the
held-out `test` is the field-of-record — but it is kept in the payload so a
smoke check can confirm the test harness itself is wired correctly (the
reference solution must pass its own tests).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from reforge.benchmark.targeted.task import EvalCase

_DATA_PATH = (
    Path(__file__).resolve().parents[3] / "benchmark_data" / "HumanEval.jsonl"
)

# A generated solution that loops forever must not wedge the sweep. The
# official HumanEval problems are short; 10s is generous and still bounded.
_EXEC_TIMEOUT_S = 10


class HumanEvalTask:
    """The official HumanEval suite behind the EvalTask protocol. `name` + 3 seams.

    `task_ids` (or `limit`) restricts the corpus to a subset — the pilot runs a
    handful of tasks to prove the FP-observation link before spending tokens on
    all 164.
    """

    name = "humaneval"

    def __init__(
        self,
        *,
        data_path: Path | None = None,
        task_ids: tuple[str, ...] | None = None,
        limit: int | None = None,
    ) -> None:
        self._data_path = data_path or _DATA_PATH
        self._task_ids = task_ids
        self._limit = limit

    def load_cases(self) -> list[EvalCase]:
        if not self._data_path.exists():
            raise RuntimeError(
                f"HumanEval data not found at {self._data_path}. Fetch it with "
                "scripts/fetch_humaneval.py (openai/human-eval, MIT)."
            )
        cases: list[EvalCase] = []
        for lineno, raw in enumerate(
            self._data_path.read_text(encoding="utf-8").splitlines(), 1
        ):
            line = raw.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"{self._data_path}:{lineno}: invalid JSON — {exc}"
                ) from exc
            tid = row["task_id"]
            if self._task_ids is not None and tid not in self._task_ids:
                continue
            cases.append(EvalCase(
                case_id=tid,
                difficulty="n/a",  # HumanEval ships no difficulty label
                bucket="humaneval",
                payload={
                    "prompt": row["prompt"],
                    "entry_point": row["entry_point"],
                    "test": row["test"],
                    "canonical_solution": row["canonical_solution"],
                },
            ))
        if self._limit is not None:
            cases = cases[: self._limit]
        if not cases:
            raise RuntimeError(
                f"HumanEval selection is empty (task_ids={self._task_ids}, "
                f"limit={self._limit}) — nothing to run."
            )
        return cases

    def build_prompt(self, case: EvalCase) -> str:
        entry = case.payload["entry_point"]
        signature = case.payload["prompt"]
        return (
            "请实现下面这个 Python 函数。要求：\n"
            f"1. 函数名必须是 `{entry}`，签名与文档字符串保持一致；\n"
            "2. 给出完整、可直接运行的函数定义（含 def 行），需要的 import 也一并写出；\n"
            f"3. 在文件末尾加一个 `if __name__ == \"__main__\":` 块，用文档字符串里的"
            f"示例输入（若没有就自己构造一个简单输入）调用 `{entry}`，并按 "
            "`输入 -> 返回值` 的形式把**输入和结果一起** print 出来，每个示例一行"
            "——这样运行时的输出既非空、又带上了输入数据。\n\n"
            f"{signature}"
        )

    def grade(
        self,
        case: EvalCase,
        stdout: str,
        exit_code: int | None,
        generated_code: str = "",
    ) -> bool:
        """Program-graded: run the held-out test suite against the generated code.

        stdout/exit_code from the runtime are ignored — correctness is whether
        the generated function passes HumanEval's own `check(candidate)`.

        The generated code is *imported* as a module, not run as `__main__`, so
        the `if __name__ == "__main__":` demo block (which the prompt asks for,
        purely to give the runtime a non-empty stdout and avoid the evaluator's
        output_not_empty false-negative) does NOT execute during grading. Gold
        therefore depends only on the function definition, never on the demo
        prints.
        """
        if not generated_code.strip():
            return False
        entry = case.payload["entry_point"]
        return self._passes(generated_code, case.payload["test"], entry)

    @staticmethod
    def _passes(solution_code: str, test_code: str, entry: str) -> bool:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "solution.py").write_text(solution_code, encoding="utf-8")
            # import (not run-as-main) → __main__ demo block stays dormant.
            runner = f"import solution\n{test_code}\ncheck(solution.{entry})\n"
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
