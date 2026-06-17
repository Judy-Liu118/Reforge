"""SandboxExecutor — facade over pluggable SandboxBackend implementations.

Backwards-compatible: `SandboxExecutor(timeout=30, workspace=None)` still
works exactly as before (subprocess backend). Pick a different backend by
passing `backend=` explicitly or setting `REFORGE_SANDBOX_BACKEND=docker`.

Selection precedence:
  1. explicit `backend=` arg
  2. `REFORGE_SANDBOX_BACKEND` env var ("subprocess" | "docker")
  3. default SubprocessBackend
"""

from __future__ import annotations

import os
from pathlib import Path

from reforge.runtime.domain.state.models import ExecutionOutput
from reforge.runtime.infrastructure.execution.backends import (
    DockerBackend,
    SandboxBackend,
    SubprocessBackend,
)


class SandboxExecutor:
    """Execute Python code via a pluggable backend."""

    def __init__(
        self,
        timeout: int = 30,
        workspace: Path | None = None,
        backend: SandboxBackend | None = None,
    ) -> None:
        self._timeout = timeout
        self._workspace = workspace or Path.cwd()
        self._backend = backend or _resolve_backend_from_env()

    def execute(self, code: str) -> ExecutionOutput:
        code = self._clean_code(code)
        return self._backend.execute(
            code,
            workspace=self._workspace,
            timeout_s=self._timeout,
        )

    @staticmethod
    def _clean_code(code: str) -> str:
        """Strip markdown fences if the LLM wrapped the code in them."""
        code = code.strip()
        if code.startswith("```"):
            lines = code.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            code = "\n".join(lines).strip()
        return code

    @property
    def workspace(self) -> Path:
        return self._workspace

    @property
    def backend(self) -> SandboxBackend:
        return self._backend


def _resolve_backend_from_env() -> SandboxBackend:
    """Pick a backend from REFORGE_SANDBOX_BACKEND env var, default subprocess."""
    name = os.environ.get("REFORGE_SANDBOX_BACKEND", "subprocess").lower()
    if name == "docker":
        return DockerBackend()
    if name in ("subprocess", ""):
        return SubprocessBackend()
    raise ValueError(
        f"unknown REFORGE_SANDBOX_BACKEND={name!r}; expected 'subprocess' or 'docker'"
    )
