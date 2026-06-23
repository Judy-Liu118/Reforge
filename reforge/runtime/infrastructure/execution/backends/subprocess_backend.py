"""SubprocessBackend — direct subprocess execution (the original backend).

Trade-off: zero deps, fast startup (~30ms), but no filesystem / network /
resource isolation. Suitable for trusted LLM-generated code in dev/CI.
For untrusted code or production demos, prefer DockerBackend.

The generated script lives in a temp file so it never pollutes the user's
project tree; the subprocess `cwd` is the workspace the caller passed in
(typically the user's current working directory) so the script can read
files like ``pd.read_csv("sales.csv")``. Code safety is the responsibility
of the 3-layer governance guard upstream, not this backend.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

from reforge.runtime.domain.state.models import TIMEOUT_EXIT_CODE, ExecutionOutput


class SubprocessBackend:
    """Runs code in a Python subprocess with cwd set to the workspace."""

    name = "subprocess"

    def execute(
        self,
        code: str,
        *,
        workspace: Path,
        timeout_s: int,
    ) -> ExecutionOutput:
        # Script goes to a temp file so it doesn't show up as `_script.py` in
        # the user's project directory mid-run.
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            prefix="reforge_",
            delete=False,
            encoding="utf-8",
        ) as f:
            script_path = Path(f.name)
            f.write(code)

        # Force UTF-8 for both directions: the child writes UTF-8 (via
        # PYTHONIOENCODING) and our reader decodes UTF-8. Without this, the
        # subprocess reader thread crashes on Windows (default GBK) the first
        # time the script prints any non-ASCII character — common with vision
        # API output, CSV data, or `·` separators.
        child_env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        start = time.perf_counter()
        try:
            proc = subprocess.run(
                ["python", str(script_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=child_env,
                timeout=timeout_s,
                cwd=str(workspace),
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
            # exc.stdout / exc.stderr carry whatever the child buffered before
            # we killed it. Keeping them is the difference between a useful
            # diagnostic ("we got past screenshot but never past compare") and
            # a black box. Decode bytes → str if the child wrote raw bytes.
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


def _decode_buffered(value: str | bytes | None) -> str:
    """Normalise TimeoutExpired.{stdout,stderr} to a UTF-8 string.

    subprocess.run with `text=True` returns str on success but on timeout
    the buffered halves may come back as bytes if the decoder hadn't flushed
    yet. Falling back to a replace-errors decode preserves diagnostic value
    over a clean str.
    """
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
