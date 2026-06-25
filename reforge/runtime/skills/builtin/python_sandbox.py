"""PythonSandboxSkill — wrap the existing SandboxExecutor as a Skill.

This is the bridge between Reforge's native code-as-action paradigm and
the new Skill abstraction. Validates that the abstraction can carry the
existing sandbox without any behavior change.
"""

from __future__ import annotations

import time

from reforge.runtime.infrastructure.execution.sandbox import SandboxExecutor
from reforge.runtime.skills.context import SkillContext
from reforge.runtime.skills.result import SkillResult


class PythonSandboxSkill:
    """Execute Python code in an isolated subprocess sandbox.

    This is the canonical skill for code-as-action tasks. The codegen node
    invokes it when the LLM emits Python code rather than a structured tool
    call.
    """

    name = "python_sandbox"
    description = (
        "Execute Python code in an isolated subprocess sandbox. "
        "Captures stdout, stderr, exit code, and duration. "
        "Use for any task that benefits from arbitrary computation, "
        "data manipulation, or control flow."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python source code to execute. May import any installed package.",
            },
        },
        "required": ["code"],
    }
    prompt_fragment = ""

    def __init__(self, executor: SandboxExecutor | None = None) -> None:
        self._executor = executor
        # Defer construction until first use so workspace/timeout from
        # SkillContext can take effect.

    def invoke(self, params: dict, context: SkillContext) -> SkillResult:
        code = params.get("code", "")
        if not isinstance(code, str) or not code.strip():
            return SkillResult(
                success=False,
                error="python_sandbox: 'code' param is required and must be a non-empty string",
            )

        executor = self._executor or SandboxExecutor(
            timeout=context.timeout_s,
            workspace=context.workspace,
        )
        start = time.perf_counter()
        try:
            execution = executor.execute(code)
        except Exception as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            return SkillResult(
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=round(duration_ms, 2),
            )

        success = execution.exit_code == 0
        return SkillResult(
            success=success,
            output=execution.stdout if success else execution.stderr,
            raw=execution,
            error=execution.stderr if not success else "",
            duration_ms=execution.duration_ms,
            metadata={
                "exit_code": execution.exit_code,
                "stdout_bytes": len(execution.stdout or ""),
                "stderr_bytes": len(execution.stderr or ""),
            },
        )
