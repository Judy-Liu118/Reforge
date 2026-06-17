"""RetryIntegrityGuard — detects evaluator hacking and fake recovery in generated code.

Catches: blank except blocks, swallowed exceptions, fake success output.
"""

from __future__ import annotations

import ast
import re

from pydantic import BaseModel, Field


class IntegrityResult(BaseModel):
    clean: bool = Field(default=True)
    issues: list[str] = Field(default_factory=list)


_BLANK_EXCEPT_RE = re.compile(
    r"except\s*(?::\s*(?:Exception|BaseException|ValueError|TypeError|RuntimeError))?\s*:\s*\n\s*(?:pass|return\s+None|continue)\b",
    re.MULTILINE,
)

_SWALLOWED_TRACEBACK_RE = re.compile(
    r"except.*:\s*\n\s*traceback\.print_exc\(\)",
    re.MULTILINE,
)


class RetryIntegrityGuard:
    """Check generated code for evaluator hacking / fake recovery patterns."""

    def check(self, code: str) -> IntegrityResult:
        issues: list[str] = []

        # Blanket except: pass
        if _BLANK_EXCEPT_RE.search(code):
            issues.append("blanket_except_pass: bare 'except: pass' swallows errors silently")

        # Swallowed exception with traceback.print_exc() — fake traceback
        if _SWALLOWED_TRACEBACK_RE.search(code):
            issues.append("fake_traceback: exception caught then traceback.print_exc() used to fake a real traceback")

        # AST analysis: try/except without meaningful recovery
        try:
            tree = ast.parse(code)
            issues.extend(self._ast_check(tree))
        except SyntaxError:
            pass

        return IntegrityResult(
            clean=len(issues) == 0,
            issues=issues,
        )

    def _ast_check(self, tree: ast.AST) -> list[str]:
        issues: list[str] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    # Empty handler body (or just pass/return)
                    if len(handler.body) == 0 or (
                        len(handler.body) == 1
                        and isinstance(handler.body[0], ast.Pass)
                    ):
                        # Check if this is inside a real task function (not a test harness)
                        if node.orelse == [] and node.finalbody == []:
                            issues.append("empty_except_handler: try/except catches error with no recovery action")

        return issues
