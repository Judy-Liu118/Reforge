"""Probes + verifiers for Reforge's own execution domain (sandboxed data / code tasks).

Built on signals the runtime already computes, not on AppWorld's API shape:

    swallowed exception   probe: RetryIntegrityGuard flags a no-op handler (blanket / narrow swallow,
                                 fake traceback) and the script exited 0
                          verifier: metamorphic re-run — the flagged handlers' bodies are replaced by
                                 `raise` and the script is run again in the sandbox; a traceback now
                                 proves an exception really fired and was swallowed (gold-free, no LLM)
    missing return        probe: the last stdout line is `None` (the heuristic evaluator's strongest
                                 SUSPICIOUS_NUMERIC value)
                          verifier: AST — a function defined in the script is printed / used as the
                                 result but has no `return <value>` anywhere in its body
    probe-only            empty stdout, nan / inf, values coerced to missing (errors="coerce"):
                          suspicious, but nothing deterministic can confirm them without the task's
                          answer. They are never minted by this module; the optional L2 critic
                          (critic.py) may judge them

Deterministic confirmations carry confirmation="reproducible" in their evidence; critic
confirmations carry confirmation="judgment".

Evidence is redacted to shapes (handler tier, exception class name, exit codes) — never data values.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from dataclasses import dataclass

from reforge.experience.cards import VerifiedFailure
from reforge.runtime.orchestration.integrity_guard import RetryIntegrityGuard

_EXC_LINE = re.compile(r"^(\w+(?:\.\w+)*(?:Error|Exception|Warning|Interrupt|Exit))\b", re.M)


@dataclass(frozen=True)
class Suspicion:
    kind: str
    detail: str


def _flagged_handlers(tree: ast.AST) -> list[tuple[ast.ExceptHandler, str]]:
    guard = RetryIntegrityGuard()
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for h in node.handlers:
                issue = guard._classify_handler(h, node)   # same classification the evaluator uses
                if issue:
                    out.append((h, issue.split(":")[0]))
    return out


def swallowed_exception_probe(code: str, exit_code: int | None) -> list[str]:
    """Issue tiers of no-op handlers in a script that exited cleanly; [] when nothing to check."""
    if exit_code != 0 or not code:
        return []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    return [tier for _, tier in _flagged_handlers(tree)]


def unswallow(code: str) -> str | None:
    """The same script with every flagged handler's body replaced by a bare `raise`."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    flagged = _flagged_handlers(tree)
    if not flagged:
        return None
    for h, _ in flagged:
        h.body = [ast.Raise(exc=None, cause=None)]
    return ast.unparse(ast.fix_missing_locations(tree))


def confirm_swallowed_exception(code: str, exit_code: int | None, rerun: Callable[[str], tuple[int, str]], *,
                                source_task: str, source_run: str = "") -> VerifiedFailure | None:
    """rerun(code) -> (exit_code, stderr) in the same sandbox. Confirmed iff the un-swallowed script crashes."""
    tiers = swallowed_exception_probe(code, exit_code)
    variant = unswallow(code) if tiers else None
    if not variant:
        return None
    rc, stderr = rerun(variant)
    if rc == 0 or "Traceback" not in (stderr or ""):
        return None
    m = _EXC_LINE.findall(stderr or "")
    ev = {"kind": "swallowed exception", "confirmation": "reproducible", "handler_tiers": sorted(set(tiers)), "original_exit": 0,
          "unswallowed_exit": rc, "exception_type": m[-1].split(".")[-1] if m else "unknown"}
    return VerifiedFailure("swallowed_exception", "unswallowed_rerun", (ev,), source_task, source_run)


def missing_return_probe(stdout: str) -> bool:
    lines = [ln.strip() for ln in (stdout or "").strip().splitlines() if ln.strip()]
    return bool(lines) and lines[-1] == "None"


def _returns_value(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and node.value is not None and not (
                isinstance(node.value, ast.Constant) and node.value.value is None):
            return True
        if isinstance(node, (ast.Yield, ast.YieldFrom)):
            return True
    return False


def confirm_missing_return(code: str, stdout: str, *, source_task: str,
                           source_run: str = "") -> VerifiedFailure | None:
    if not missing_return_probe(stdout):
        return None
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    defs = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    no_value = {name for name, fn in defs.items() if not _returns_value(fn)}
    used = set()
    for node in ast.walk(tree):                        # print(f(...)) or x = f(...)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
            used |= {a.func.id for a in node.args if isinstance(a, ast.Call) and isinstance(a.func, ast.Name)}
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
            used.add(node.value.func.id)
    hit = sorted(no_value & used)
    if not hit:
        return None
    ev = {"kind": "missing return", "confirmation": "reproducible", "printed_value": "None", "functions_without_return_value": len(hit)}
    return VerifiedFailure("missing_return", "ast_no_return_value", (ev,), source_task, source_run)


def coerces_to_missing(code: str) -> bool:
    """A call with errors="coerce" (pd.to_numeric / to_datetime / astype): invalid values become NaN silently."""
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return False
    return any(isinstance(n, ast.Call) and any(k.arg == "errors" and isinstance(k.value, ast.Constant)
                                               and k.value.value == "coerce" for k in n.keywords)
               for n in ast.walk(tree))


def probe_only_suspicions(user_request: str, stdout: str, exit_code: int | None,
                          code: str = "") -> list[Suspicion]:
    """Signals worth surfacing that no deterministic verifier here can confirm."""
    out = []
    s = (stdout or "").strip()
    if exit_code == 0 and not s:
        out.append(Suspicion("empty_output", "clean exit with empty stdout"))
    if exit_code == 0 and s.casefold() in {"nan", "inf", "-inf"}:
        out.append(Suspicion("degenerate_number", f"result is {s}"))
    if exit_code == 0 and coerces_to_missing(code):
        out.append(Suspicion("coerce_drop", "values coerced to missing before the result was computed"))
    return out
