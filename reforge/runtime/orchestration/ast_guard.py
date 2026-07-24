"""ASTGuard — static risk scoring of generated code.

NOT an execution gate. The only production caller is HeuristicEvaluator
(evaluation/heuristics.py), which runs *after* the sandbox has already executed
the code and turns any violation into a failed EvalCheck — a score penalty that
can trigger a retry, not a block. Real execution containment is the sandbox, not
this module. `analyze` walks the AST for dangerous imports and calls; it is
best-effort and, by design, only flags patterns that are statically legible.

The true pre-generation gate is Layer 1, SemanticSafetyGuard, which inspects the
*request* and routes to final_response before any code exists.
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

# Builtins with no legitimate data-analysis use — flagged wherever they are
# called directly. Dynamic introspection (vars/globals/locals) is deliberately
# NOT here: it is common in benign code and returns a namespace mapping that is
# harmless without a follow-up exec (already listed). getattr/setattr/delattr
# are handled by _check_introspection_call below — only their statically
# resolvable dangerous form is flagged, because `getattr(df, col)` is idiomatic
# pandas and indistinguishable from an attack once the name is non-constant.
_DANGEROUS_CALLS = {"eval", "exec", "compile", "__import__"}

_INTROSPECTION_CALLS = {"getattr", "setattr", "delattr"}

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
            # This guard only *scores*; it does not gate execution, so a payload
            # it can't parse is left for the sandbox to reject at runtime. If
            # ASTGuard is ever promoted to a pre-execution gate, this branch MUST
            # become fail-closed (allow=False) — an unparseable payload must not
            # win admission by being unreadable.
            return ASTGuardResult(allow=True, violations=[])

        # Resolve `import x as y` aliases up front so the attribute check can
        # normalise `y.system` back to `x.system` instead of missing it.
        alias_map = _build_alias_map(tree)

        for node in ast.walk(tree):
            # Check imports
            if isinstance(node, ast.Import):
                for alias in node.names:
                    v = _check_import(alias.name)
                    if v:
                        violations.append(v)

            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    v = _check_from_import(module, alias.name)
                    if v:
                        violations.append(v)

            # Check dangerous function calls
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                fn = node.func.id
                if fn in _DANGEROUS_CALLS:
                    violations.append(f"call:{fn}")
                elif fn in _INTROSPECTION_CALLS:
                    v = _check_introspection_call(fn, node, alias_map)
                    if v:
                        violations.append(v)

            # Check attribute access like os.system (walk yields these even when
            # the enclosing node is a Call whose func is an Attribute, so
            # `os.system(x)` is covered here, not in the Call branch above).
            elif isinstance(node, ast.Attribute):
                if isinstance(node.value, ast.Name):
                    module = alias_map.get(node.value.id, node.value.id)
                    if (module, node.attr) in _DANGEROUS_ATTRS:
                        violations.append(f"attribute:{module}.{node.attr}")

        risk = "high" if violations else "low"
        return ASTGuardResult(
            allow=len(violations) == 0,
            violations=violations,
            risk_level=risk,
        )


def _build_alias_map(tree: ast.AST) -> dict[str, str]:
    """Map each name bound by `import` to the top-level module it refers to.

        import os          -> {"os": "os"}
        import os as o     -> {"o": "os"}    # the alias bypass this closes
        import os.path     -> {"os": "os"}   # `import os.path` binds `os`

    `from x import y` is intentionally excluded: the from-import check already
    catches dangerous names at the import statement itself.
    """
    mapping: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                mapping[alias.asname or top] = top
    return mapping


def _check_introspection_call(
    fn: str, node: ast.Call, alias_map: dict[str, str]
) -> str | None:
    """Flag getattr/setattr/delattr only when it resolves to a known-dangerous
    attribute, e.g. `getattr(os, "system")`.

    A non-constant attribute name (`getattr(df, col)`) is left alone: it is
    idiomatic and statically indistinguishable from an attack, so flagging it
    only produced false positives that cost a retry cycle for no security gain.
    """
    if len(node.args) < 2:
        return None
    base, attr = node.args[0], node.args[1]
    if not isinstance(base, ast.Name):
        return None
    if not (isinstance(attr, ast.Constant) and isinstance(attr.value, str)):
        return None
    module = alias_map.get(base.id, base.id)
    if (module, attr.value) in _DANGEROUS_ATTRS:
        return f"call:{fn}:{module}.{attr.value}"
    return None


def _check_import(module: str) -> str | None:
    # `import os` / `import shutil` etc. are fine on their own — the danger is in
    # calling specific attributes, covered by the attribute check. Only modules
    # with no benign use get flagged at the import. Aliasing (`import os as o`)
    # doesn't matter here: wildcard modules are dangerous by import regardless of
    # the bound name, and function-level modules aren't flagged at import at all.
    if module in _WILDCARD_DANGEROUS_MODULES:
        return f"import:{module}"
    return None


def _check_from_import(module: str, name: str) -> str | None:
    if module in _DANGEROUS_IMPORTS:
        allowed = _DANGEROUS_IMPORTS[module]
        if "*" in allowed or name in allowed:
            return f"import:{module}.{name}"
    return None
