"""ASTGuard — AST-based capability analysis on generated code.

Layer 2 of capability enforcement. Layer 1 (SemanticSafetyGuard) checks requests.
Layer 2 (ASTGuard) analyzes generated code for dangerous imports and calls.
"""

from __future__ import annotations

import ast

from pydantic import BaseModel, Field


class ASTGuardResult(BaseModel):
    allow: bool = Field(default=True)
    violations: list[str] = Field(default_factory=list)
    risk_level: str = Field(default="low")


# Two distinct categories of dangerous modules:
#   * Wildcard ("*"): the import itself is the violation (raw memory / sockets /
#     signal handling — no benign use in a data-analysis sandbox).
#   * Function-level (specific names): the import is fine — only a call to one
#     of the listed attributes is dangerous. `import os` to use `os.path.exists`
#     is legitimate; `os.system(...)` is not.
_DANGEROUS_IMPORTS: dict[str, list[str]] = {
    "os": ["system", "popen", "fork", "kill", "remove", "rmdir", "unlink", "chmod", "chown"],
    "subprocess": ["Popen", "call", "run", "check_output", "check_call"],
    "ctypes": ["*"],
    "mmap": ["*"],
    "socket": ["*"],
    "shutil": ["rmtree", "move", "copy", "copytree"],
    "multiprocessing": ["Process", "Pool", "cpu_count"],
    "pty": ["*"],
    "fcntl": ["*"],
    "signal": ["*"],
    "builtins": ["__import__"],
}

_DANGEROUS_CALLS = {
    "eval", "exec", "compile", "__import__",
    "getattr", "setattr", "delattr",
    "globals", "locals", "vars",
}

# Attribute access (module.fn) that's banned. Derived from _DANGEROUS_IMPORTS
# so the import-level check and the call-site check never drift out of sync.
_DANGEROUS_ATTRS: set[tuple[str, str]] = {
    (module, fn)
    for module, fns in _DANGEROUS_IMPORTS.items()
    for fn in fns
    if fn != "*"
}

# Modules whose mere import constitutes a violation (no benign use).
_WILDCARD_DANGEROUS_MODULES: frozenset[str] = frozenset(
    module for module, fns in _DANGEROUS_IMPORTS.items() if "*" in fns
)


class ASTGuard:
    """Analyze generated Python code for dangerous patterns using AST."""

    def analyze(self, code: str) -> ASTGuardResult:
        violations: list[str] = []

        try:
            tree = ast.parse(code)
        except SyntaxError:
            return ASTGuardResult(allow=True, violations=[])  # Can't parse, let execution decide

        for node in ast.walk(tree):
            # Check imports
            if isinstance(node, ast.Import):
                for alias in node.names:
                    v = _check_import(alias.name, alias.asname or alias.name)
                    if v:
                        violations.append(v)

            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    v = _check_from_import(module, alias.name)
                    if v:
                        violations.append(v)

            # Check dangerous function calls
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id in _DANGEROUS_CALLS:
                        violations.append(f"call:{node.func.id}")

            # Check attribute access like os.system
            elif isinstance(node, ast.Attribute):
                if isinstance(node.value, ast.Name):
                    key = (node.value.id, node.attr)
                    if key in _DANGEROUS_ATTRS:
                        violations.append(f"attribute:{node.value.id}.{node.attr}")

        risk = "high" if violations else "low"
        return ASTGuardResult(
            allow=len(violations) == 0,
            violations=violations,
            risk_level=risk,
        )


def _check_import(module: str, _alias: str) -> str | None:
    # `import os` / `import shutil` etc. are fine on their own — the danger
    # is in calling specific attributes, which is covered by the attribute
    # check below. Only modules with no benign use get flagged at the import.
    if module in _WILDCARD_DANGEROUS_MODULES:
        return f"import:{module}"
    return None


def _check_from_import(module: str, name: str) -> str | None:
    if module in _DANGEROUS_IMPORTS:
        allowed = _DANGEROUS_IMPORTS[module]
        if "*" in allowed or name in allowed:
            return f"import:{module}.{name}"
    return None
