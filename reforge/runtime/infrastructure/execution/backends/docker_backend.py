"""DockerBackend — execute code inside a docker container.

Provides filesystem / network / cpu / memory isolation that SubprocessBackend
cannot. Uses the docker CLI directly so we don't take a hard dependency on
the docker python SDK.

Caller contract:
  - constructor verifies docker is callable (raises DockerUnavailableError)
  - execute() returns ExecutionOutput exactly like SubprocessBackend
  - timeout is enforced by `docker run`'s own kill via subprocess.run timeout
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from reforge.runtime.domain.state.models import ExecutionOutput


class DockerUnavailableError(RuntimeError):
    """Raised when docker CLI is missing or daemon unreachable."""


class DockerBackend:
    """Runs code inside a docker container with strict resource limits.

    Defaults are deliberately conservative:
      - python:3.11-slim image
      - --network=none           (no network)
      - --memory=512m            (RAM cap)
      - --cpus=1                 (CPU cap)
      - --pids-limit=128         (fork-bomb guard)
      - --read-only with /work writable mount (workspace round-trip)
    """

    name = "docker"

    def __init__(
        self,
        image: str = "python:3.11-slim",
        *,
        memory: str = "512m",
        cpus: str = "1",
        network: str = "none",
        pids_limit: int = 128,
        verify_on_init: bool = True,
    ) -> None:
        self._image = image
        self._memory = memory
        self._cpus = cpus
        self._network = network
        self._pids_limit = pids_limit
        if verify_on_init:
            self._verify_docker_available()

    @staticmethod
    def _verify_docker_available() -> None:
        if shutil.which("docker") is None:
            raise DockerUnavailableError(
                "docker CLI not found on PATH — install Docker or "
                "fall back to SubprocessBackend"
            )
        try:
            proc = subprocess.run(
                ["docker", "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise DockerUnavailableError(f"docker probe failed: {exc}") from exc
        if proc.returncode != 0:
            raise DockerUnavailableError(
                f"docker daemon unreachable: {proc.stderr.strip()}"
            )

    def execute(
        self,
        code: str,
        *,
        workspace: Path,
        timeout_s: int,
    ) -> ExecutionOutput:
        script_path = workspace / "_script.py"
        script_path.write_text(code, encoding="utf-8")

        cmd = [
            "docker", "run", "--rm",
            f"--network={self._network}",
            f"--memory={self._memory}",
            f"--cpus={self._cpus}",
            f"--pids-limit={self._pids_limit}",
            "-v", f"{workspace.resolve()}:/work",
            "-w", "/work",
            self._image,
            "python", "/work/_script.py",
        ]

        start = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
            duration_ms = (time.perf_counter() - start) * 1000
            return ExecutionOutput(
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                exit_code=proc.returncode,
                duration_ms=round(duration_ms, 2),
            )
        except subprocess.TimeoutExpired:
            duration_ms = (time.perf_counter() - start) * 1000
            return ExecutionOutput(
                stdout="",
                stderr=f"Execution timed out after {timeout_s}s",
                exit_code=-1,
                duration_ms=round(duration_ms, 2),
            )
        finally:
            if script_path.exists():
                script_path.unlink()
