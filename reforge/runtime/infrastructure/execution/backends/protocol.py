"""SandboxBackend Protocol — pluggable code-execution mechanism.

Backends are stateless: every call carries its own workspace + timeout.
This keeps SandboxExecutor (the facade) the single owner of per-call
configuration, while backends focus only on the execution mechanism.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from reforge.runtime.domain.state.models import ExecutionOutput


@runtime_checkable
class SandboxBackend(Protocol):
    """Pluggable backend that runs Python code and returns ExecutionOutput.

    Implementations MUST:
      - capture stdout / stderr / exit_code / duration_ms
      - respect timeout_s and surface timeout as exit_code = -1
      - never raise on user-code failures (return non-zero exit_code instead)

    A backend MAY raise on infrastructure failure (e.g. DockerUnavailableError
    when the daemon is missing) — callers decide whether to fall back.
    """

    name: str

    def execute(
        self,
        code: str,
        *,
        workspace: Path,
        timeout_s: int,
    ) -> ExecutionOutput: ...
