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
import uuid
from pathlib import Path

from reforge.runtime.domain.state.models import TIMEOUT_EXIT_CODE, ExecutionOutput


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
      - -v <workspace>:/work     (writable workspace round-trip)

    The container root filesystem is WRITABLE — isolation is limited to the
    network / memory / cpu / pids caps above. A read-only root is a deliberate
    non-goal: in a --network=none, one-shot --rm container the marginal gain
    over those caps is small, while --read-only breaks the many data-analysis
    scripts that write /tmp or ~/.cache. If this backend is ever pointed at
    genuinely adversarial code, harden as a SET (--read-only --tmpfs /tmp plus
    --user / --cap-drop=ALL / --security-opt), not by adding --read-only alone
    to a container that still runs as root.
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

        # Named so the timeout branch can force-remove the container: subprocess
        # timeout only kills the `docker run` client, leaving the container
        # running detached. PYTHONUNBUFFERED so the child streams stdout in real
        # time — otherwise block buffering swallows it before a timeout kill.
        container_name = f"reforge_run_{uuid.uuid4().hex[:12]}"
        cmd = [
            "docker", "run", "--rm",
            "--name", container_name,
            "-e", "PYTHONUNBUFFERED=1",
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
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
            )
            duration_ms = (time.perf_counter() - start) * 1000
            return ExecutionOutput(
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                exit_code=proc.returncode,
                duration_ms=round(duration_ms, 2),
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            # The timeout killed the `docker run` client, not the container —
            # force-remove it so a hung / infinite-loop script doesn't leak a
            # container that keeps holding its cpu/memory reservation.
            self._force_remove(container_name)
            # Surface whatever the container streamed before the kill (real-time
            # thanks to PYTHONUNBUFFERED). Same diagnostic-preserving contract as
            # SubprocessBackend's timeout branch; decode bytes if not yet decoded.
            buffered_stdout = _decode_buffered(exc.stdout)
            buffered_stderr = _decode_buffered(exc.stderr)
            timeout_marker = f"Execution timed out after {timeout_s}s"
            stderr = (
                f"{buffered_stderr}\n{timeout_marker}".strip()
                if buffered_stderr
                else timeout_marker
            )
            return ExecutionOutput(
                stdout=buffered_stdout,
                stderr=stderr,
                exit_code=TIMEOUT_EXIT_CODE,
                duration_ms=round(duration_ms, 2),
            )
        finally:
            if script_path.exists():
                script_path.unlink()

    @staticmethod
    def _force_remove(container_name: str) -> None:
        """Best-effort `docker rm -f` of a container left running after timeout.

        subprocess's timeout kills the `docker run` client, not the container;
        without this a non-terminating script leaks a container that keeps its
        resource caps until the daemon is restarted. Errors are swallowed: if the
        container already exited (--rm cleaned it up) the remove is a harmless
        no-op we must not turn into a backend failure.
        """
        try:
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True,
                timeout=10,
            )
        except (subprocess.SubprocessError, OSError):
            pass


def _decode_buffered(value: str | bytes | None) -> str:
    """Normalise TimeoutExpired.{stdout,stderr} to a UTF-8 string.

    subprocess.run with `text=True` returns str on success, but on timeout the
    buffered halves may come back as bytes if the decoder hadn't flushed yet. A
    replace-errors decode preserves diagnostic value over a clean str.
    """
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
