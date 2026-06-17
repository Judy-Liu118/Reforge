"""Execution node — run generated code in the sandbox and capture output."""

from __future__ import annotations

from reforge.config import config
from reforge.runtime.infrastructure.error_extraction import extract_error_type
from reforge.runtime.infrastructure.execution.sandbox import SandboxExecutor
from reforge.runtime.domain.state.models import RuntimeState


def execution_node(state: RuntimeState) -> dict:
    executor = SandboxExecutor(timeout=config.execution_timeout)
    result = executor.execute(state.generated_code)
    traceback = result.stderr if result.exit_code != 0 else ""
    error_type = extract_error_type(traceback, default="UnknownError")

    record = {
        "attempt": state.control_state.retry_count + 1,
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "error_type": error_type,
    }
    exec_state = state.exec_state.model_copy(
        update={
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
        }
    )
    return {
        "attempts": state.attempts + [record],
        "exec_state": exec_state,
    }
