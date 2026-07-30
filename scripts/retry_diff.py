"""Replay a few tasks and show the per-attempt code diff — REAL LLM, tokens.

fp_sweep records don't store generated code, only outcomes. To see WHAT the
model changed on retry (and why it still crashed), this replays specific
task_ids, captures each attempt's generated_code + stderr, and prints a
unified diff between attempt 1 and 2.

Governor arm (retries on). Set the model in your env first, e.g.
    $env:LLM_MODEL="qwen-turbo"; python scripts/retry_diff.py

NOTE: replay is non-deterministic — the model may crash differently, or even
pass this time. The diff is a representative look at its retry behaviour, not
a byte-exact reproduction of the earlier run.
"""

from __future__ import annotations

import difflib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from reforge.benchmark.targeted.mbpp_task import MbppTask
from reforge.runtime.orchestration.engine.runner import RuntimeRunner

TASK_IDS = (97, 106, 126, 130)


def _last_error_line(stderr: str) -> str:
    lines = [ln for ln in stderr.strip().splitlines() if ln.strip()]
    return lines[-1] if lines else "(no stderr)"


def main() -> None:
    task = MbppTask(task_ids=TASK_IDS)
    for case in task.load_cases():
        prompt = task.build_prompt(case)
        attempts: list[tuple[str, int | None, str]] = []
        for node, st in RuntimeRunner().stream(prompt):
            if node == "execution":
                attempts.append((
                    st.generated_code or "",
                    st.exec_state.exit_code,
                    st.exec_state.stderr or "",
                ))

        print("=" * 70)
        print(f"{case.case_id}  ({len(attempts)} attempt(s))  —  {case.payload['prompt']}")
        for i, (code, ec, err) in enumerate(attempts, 1):
            print(f"\n--- attempt {i}: exit={ec}  error={_last_error_line(err)!r} ---")
            print(code)

        if len(attempts) >= 2:
            a1, a2 = attempts[0][0], attempts[1][0]
            print(f"\n>>> DIFF attempt1 -> attempt2 ({case.case_id}):")
            diff = difflib.unified_diff(
                a1.splitlines(), a2.splitlines(),
                fromfile="attempt1", tofile="attempt2", lineterm="",
            )
            body = "\n".join(diff)
            print(body if body.strip() else "(identical — model regenerated the same code)")
        else:
            print("\n>>> only 1 attempt this replay (it passed or didn't retry — non-deterministic)")
        print()


if __name__ == "__main__":
    main()
