"""Real sandbox integration tests — actual subprocess execution, not mock."""

from reforge.runtime.infrastructure.execution.sandbox import SandboxExecutor


class TestRealSandbox:
    """Tests using the actual sandbox executor with real Python code."""

    def test_successful_code_execution(self):
        executor = SandboxExecutor(timeout=5)
        result = executor.execute("print('hello sandbox')")
        assert result.exit_code == 0
        assert "hello sandbox" in result.stdout
        assert result.duration_ms > 0

    def test_failing_code_captures_traceback(self):
        executor = SandboxExecutor(timeout=5)
        result = executor.execute("x = 1 / 0")
        assert result.exit_code == 1
        assert "ZeroDivisionError" in result.stderr

    def test_timeout_handling(self):
        executor = SandboxExecutor(timeout=2)
        result = executor.execute("import time; time.sleep(60)")
        assert result.exit_code == -1
        assert "timed out" in result.stderr.lower()

    def test_markdown_fence_cleaning(self):
        executor = SandboxExecutor(timeout=5)
        result = executor.execute("```python\nprint('clean')\n```")
        assert result.exit_code == 0
        assert "clean" in result.stdout

    def test_import_error_capture(self):
        executor = SandboxExecutor(timeout=5)
        result = executor.execute("import nonexistent_module_xyz_12345")
        assert result.exit_code == 1
        assert "ModuleNotFoundError" in result.stderr
