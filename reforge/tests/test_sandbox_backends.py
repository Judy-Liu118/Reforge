"""Sandbox backends — Protocol conformance + selection + Docker mocking.

Real subprocess execution is already covered by
`reforge/tests/integration/test_sandbox_chain.py`. These unit tests focus on
the backend dispatch / env-var resolution / Docker CLI shape (mocked).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from reforge.runtime.domain.state.models import ExecutionOutput
from reforge.runtime.infrastructure.execution import (
    DockerBackend,
    DockerUnavailableError,
    SandboxBackend,
    SandboxExecutor,
    SubprocessBackend,
)


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_subprocess_is_a_backend(self) -> None:
        assert isinstance(SubprocessBackend(), SandboxBackend)

    def test_docker_is_a_backend(self) -> None:
        # Skip the real docker probe so the test runs anywhere.
        backend = DockerBackend(verify_on_init=False)
        assert isinstance(backend, SandboxBackend)


# ---------------------------------------------------------------------------
# Encoding: child must write UTF-8 and parent must decode UTF-8.
# Regression — Windows default GBK crashed subprocess on `print("Q1 路")`.
# ---------------------------------------------------------------------------


class TestSubprocessEncoding:
    def test_utf8_stdout_does_not_crash_reader(self, tmp_path: Path) -> None:
        backend = SubprocessBackend()
        # Mix of chars that are multi-byte in UTF-8 and undefined in GBK:
        # · (middle dot), © (copyright), … (ellipsis), 路 (Chinese), – (en dash).
        code = "print('Generated 2026-06-16 路 Internal use only · © … – done')"
        result = backend.execute(code, workspace=tmp_path, timeout_s=30)
        assert result.exit_code == 0
        assert "路" in result.stdout
        assert "©" in result.stdout
        assert "·" in result.stdout

    def test_utf8_stderr_does_not_crash_reader(self, tmp_path: Path) -> None:
        backend = SubprocessBackend()
        code = (
            "import sys\n"
            "sys.stderr.write('error · 路 occurred\\n')\n"
            "raise ValueError('© bad input')\n"
        )
        result = backend.execute(code, workspace=tmp_path, timeout_s=30)
        assert result.exit_code != 0
        assert "©" in result.stderr or "路" in result.stderr

    def test_subprocess_backend_has_name(self) -> None:
        assert SubprocessBackend().name == "subprocess"

    def test_docker_backend_has_name(self) -> None:
        assert DockerBackend(verify_on_init=False).name == "docker"


# ---------------------------------------------------------------------------
# SubprocessBackend — only needs a smoke test; integration suite covers real exec
# ---------------------------------------------------------------------------


class TestSubprocessBackend:
    def test_executes_real_code(self, tmp_path: Path) -> None:
        backend = SubprocessBackend()
        result = backend.execute(
            "print('hi from subprocess')",
            workspace=tmp_path,
            timeout_s=5,
        )
        assert result.exit_code == 0
        assert "hi from subprocess" in result.stdout

    def test_cleans_up_script_after_run(self, tmp_path: Path) -> None:
        SubprocessBackend().execute("print(1)", workspace=tmp_path, timeout_s=5)
        assert not (tmp_path / "_script.py").exists()


# ---------------------------------------------------------------------------
# Timeout — must surface whatever the child buffered before the kill.
# Regression: previously TimeoutExpired's stdout/stderr were thrown away, so
# CLI's [stdout tail] section saw nothing on a timed-out run — the user
# couldn't tell whether the script got stuck on step 1, step 2, or step N.
# ---------------------------------------------------------------------------


class TestSubprocessTimeoutPreservesBufferedOutput:
    def test_timeout_keeps_stdout_printed_before_hang(self, tmp_path: Path) -> None:
        backend = SubprocessBackend()
        # Print + flush, then hang well past the parent's timeout.
        code = (
            "import time\n"
            "print('[reforge.step] screenshot: start', flush=True)\n"
            "time.sleep(30)\n"
        )
        result = backend.execute(code, workspace=tmp_path, timeout_s=2)
        assert result.exit_code == -1
        assert "[reforge.step] screenshot: start" in result.stdout
        # Original timeout marker still surfaces in stderr.
        assert "timed out" in result.stderr.lower()

    def test_timeout_keeps_stderr_printed_before_hang(self, tmp_path: Path) -> None:
        backend = SubprocessBackend()
        code = (
            "import sys, time\n"
            "sys.stderr.write('warmup error line\\n'); sys.stderr.flush()\n"
            "time.sleep(30)\n"
        )
        result = backend.execute(code, workspace=tmp_path, timeout_s=2)
        assert result.exit_code == -1
        assert "warmup error line" in result.stderr
        assert "timed out after 2s" in result.stderr.lower()

    def test_timeout_with_no_output_still_works(self, tmp_path: Path) -> None:
        """An immediately-blocking child should still give a clean timeout result."""
        backend = SubprocessBackend()
        result = backend.execute(
            "import time; time.sleep(30)", workspace=tmp_path, timeout_s=2
        )
        assert result.exit_code == -1
        assert result.stdout == ""
        assert "timed out after 2s" in result.stderr.lower()


# ---------------------------------------------------------------------------
# DockerBackend — verify CLI shape via mocked subprocess.run
# ---------------------------------------------------------------------------


class TestDockerBackendCommandShape:
    """We don't run a real container in unit tests; we assert the CLI we build."""

    def test_constructor_raises_when_docker_missing(self) -> None:
        with patch(
            "reforge.runtime.infrastructure.execution.backends.docker_backend"
            ".shutil.which",
            return_value=None,
        ):
            with pytest.raises(DockerUnavailableError):
                DockerBackend()

    def test_constructor_raises_when_daemon_unreachable(self) -> None:
        with patch(
            "reforge.runtime.infrastructure.execution.backends.docker_backend"
            ".shutil.which",
            return_value="/usr/bin/docker",
        ), patch(
            "reforge.runtime.infrastructure.execution.backends.docker_backend"
            ".subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="Cannot connect to daemon"
            ),
        ):
            with pytest.raises(DockerUnavailableError, match="daemon unreachable"):
                DockerBackend()

    def test_execute_builds_expected_docker_run_command(
        self, tmp_path: Path
    ) -> None:
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["timeout"] = kwargs.get("timeout")
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="ok\n", stderr=""
            )

        with patch(
            "reforge.runtime.infrastructure.execution.backends.docker_backend"
            ".subprocess.run",
            side_effect=fake_run,
        ):
            backend = DockerBackend(verify_on_init=False)
            result = backend.execute(
                "print('hi')", workspace=tmp_path, timeout_s=42
            )

        assert isinstance(result, ExecutionOutput)
        assert result.exit_code == 0
        assert "ok" in result.stdout

        cmd = captured["cmd"]
        assert cmd[:3] == ["docker", "run", "--rm"]
        # All isolation flags MUST be present — this is the contract.
        assert "--network=none" in cmd
        assert "--memory=512m" in cmd
        assert "--cpus=1" in cmd
        assert "--pids-limit=128" in cmd
        assert "python:3.11-slim" in cmd
        # Workspace must be mounted to /work and used as -w
        assert any(c.startswith("-v") for c in cmd)
        assert "/work" in cmd  # working dir
        # Timeout must be forwarded to subprocess.run
        assert captured["timeout"] == 42

    def test_execute_surfaces_timeout_as_negative_exit_code(
        self, tmp_path: Path
    ) -> None:
        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout"))

        with patch(
            "reforge.runtime.infrastructure.execution.backends.docker_backend"
            ".subprocess.run",
            side_effect=fake_run,
        ):
            backend = DockerBackend(verify_on_init=False)
            result = backend.execute("...", workspace=tmp_path, timeout_s=3)

        assert result.exit_code == -1
        assert "timed out" in result.stderr.lower()

    def test_execute_cleans_up_script_after_run(self, tmp_path: Path) -> None:
        with patch(
            "reforge.runtime.infrastructure.execution.backends.docker_backend"
            ".subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            ),
        ):
            DockerBackend(verify_on_init=False).execute(
                "print(1)", workspace=tmp_path, timeout_s=5
            )
        assert not (tmp_path / "_script.py").exists()


# ---------------------------------------------------------------------------
# SandboxExecutor facade — backend resolution
# ---------------------------------------------------------------------------


class TestSandboxExecutorBackendSelection:
    def test_defaults_to_subprocess_backend(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REFORGE_SANDBOX_BACKEND", raising=False)
        executor = SandboxExecutor()
        assert isinstance(executor.backend, SubprocessBackend)
        assert executor.backend.name == "subprocess"

    def test_env_var_subprocess(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REFORGE_SANDBOX_BACKEND", "subprocess")
        assert isinstance(SandboxExecutor().backend, SubprocessBackend)

    def test_env_var_docker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REFORGE_SANDBOX_BACKEND", "docker")
        with patch(
            "reforge.runtime.infrastructure.execution.backends.docker_backend"
            ".shutil.which",
            return_value="/usr/bin/docker",
        ), patch(
            "reforge.runtime.infrastructure.execution.backends.docker_backend"
            ".subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout="24.0.7", stderr=""
            ),
        ):
            assert isinstance(SandboxExecutor().backend, DockerBackend)

    def test_env_var_unknown_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REFORGE_SANDBOX_BACKEND", "wasm")
        with pytest.raises(ValueError, match="unknown REFORGE_SANDBOX_BACKEND"):
            SandboxExecutor()

    def test_explicit_backend_wins_over_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("REFORGE_SANDBOX_BACKEND", "docker")
        executor = SandboxExecutor(backend=SubprocessBackend())
        # docker probe never runs because explicit backend short-circuits resolution
        assert isinstance(executor.backend, SubprocessBackend)

    def test_facade_strips_markdown_fences_before_backend(
        self, tmp_path: Path
    ) -> None:
        seen: dict = {}

        class _RecordingBackend:
            name = "recording"

            def execute(self, code: str, *, workspace: Path, timeout_s: int):
                seen["code"] = code
                return ExecutionOutput(
                    stdout="", stderr="", exit_code=0, duration_ms=0.0
                )

        SandboxExecutor(workspace=tmp_path, backend=_RecordingBackend()).execute(
            "```python\nprint('hi')\n```"
        )
        assert seen["code"] == "print('hi')"


# ---------------------------------------------------------------------------
# Real docker integration — runs only when docker is actually installed.
# Marked so CI can skip without docker.
# ---------------------------------------------------------------------------


def _docker_available() -> bool:
    try:
        DockerBackend()
    except DockerUnavailableError:
        return False
    return True


@pytest.mark.docker
@pytest.mark.skipif(
    not _docker_available(),
    reason="docker daemon not available — skipping real container test",
)
class TestDockerBackendIntegration:
    """Real docker run — guarded by mark + skip so CI without docker is fine."""

    def test_runs_real_container(self, tmp_path: Path) -> None:
        backend = DockerBackend()
        result = backend.execute(
            "print('hello from docker')", workspace=tmp_path, timeout_s=30
        )
        assert result.exit_code == 0
        assert "hello from docker" in result.stdout
