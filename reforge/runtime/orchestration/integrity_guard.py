"""RetryIntegrityGuard — detects evaluator hacking and fake recovery in code.

Single source of truth for except-swallowing / fake-recovery detection. It used
to overlap two regexes here plus HeuristicEvaluator.BLANKET_EXCEPT_RE, so one
`except: pass` scored as three separate failed checks. Everything is now one AST
pass that classifies each handler once, emitting at most a single issue per
handler. The union of what the old three checks caught is preserved:

  * fake_traceback   — the handler re-prints the exception with
                       `traceback.print_exc()` and carries on (old
                       _SWALLOWED_TRACEBACK_RE).
  * blanket_swallow  — a broad catch (bare / Exception / BaseException) whose
                       body does nothing but pass/continue/return/print (old
                       BLANKET_EXCEPT_RE). This is the only issue mapped to the
                       `blanket_except_detected` failure_type downstream.
  * narrow_swallow   — a narrower catch that still swallows: an empty / pass
                       body of any exception type (old AST check), or one of
                       ValueError/TypeError/RuntimeError doing only
                       pass/continue/return-None (old _BLANK_EXCEPT_RE).

Judged over the WHOLE handler body, not just its first statement, so a body
containing a `raise` (log-and-reraise) or any real work is correctly left alone
— the old first-line regexes false-positived on those.
"""

from __future__ import annotations

import ast

from pydantic import BaseModel, Field


class IntegrityResult(BaseModel):
    clean: bool = Field(default=True)
    issues: list[str] = Field(default_factory=list)


# "Broad" catches: a no-op body under these is suspect because they catch nearly
# everything. Two tiers, mirroring the old regexes' accepted no-op sets:
#   _BROAD_A accepts pass|continue|return(any)|print(...)   (old BLANKET_EXCEPT_RE)
#   _BROAD_B additionally covers ValueError/TypeError/RuntimeError, but only
#            with pass|continue|return None                 (old _BLANK_EXCEPT_RE)
# None represents a bare `except:`.
_BROAD_A: frozenset[str | None] = frozenset({None, "Exception", "BaseException"})
_BROAD_B: frozenset[str | None] = _BROAD_A | {"ValueError", "TypeError", "RuntimeError"}

# Statement kinds that count as "no recovery". Anything else (a raise, an assign,
# a call other than print, a nested if/for/with, ...) is "real" work and clears
# the handler.
_A_NOOP = frozenset({"pass", "continue", "return_none", "return_val", "print"})
_B_NOOP = frozenset({"pass", "continue", "return_none"})


class RetryIntegrityGuard:
    """Check generated code for evaluator hacking / fake recovery patterns."""

    def check(self, code: str) -> IntegrityResult:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            # Can't parse — let execution surface the error (same fail-open the
            # old code used when the AST step raised).
            return IntegrityResult(clean=True, issues=[])

        issues: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    issue = self._classify_handler(handler, node)
                    if issue:
                        issues.append(issue)

        return IntegrityResult(clean=len(issues) == 0, issues=issues)

    def _classify_handler(self, handler: ast.ExceptHandler, try_node: ast.Try) -> str | None:
        exc = _handler_exc_name(handler)
        kinds = [_stmt_kind(s) for s in handler.body]

        # Any real work (including `raise`) means the handler is not swallowing.
        if "real" in kinds:
            return None

        # fake_traceback — prints the exception to look like a real crash.
        if "print_exc" in kinds:
            return (
                "fake_traceback: exception caught then traceback.print_exc() "
                "used to fake a real traceback"
            )

        # blanket_swallow — broad catch, body does nothing useful. Only this
        # issue maps to the blanket_except_detected failure_type.
        if kinds and exc in _BROAD_A and all(k in _A_NOOP for k in kinds):
            return (
                "blanket_swallow: broad 'except' whose body does nothing "
                "(pass/return/continue/print) silently suppresses the error"
            )

        # narrow_swallow — empty / pass-only body of any type (with no
        # else/finally suggesting real structure), or a broad-B class doing
        # only pass/continue/return-None.
        empty_or_pass = not kinds or all(k == "pass" for k in kinds)
        if empty_or_pass and try_node.orelse == [] and try_node.finalbody == []:
            return (
                "narrow_swallow: except handler has an empty body — error caught "
                "with no recovery action"
            )
        if kinds and exc in _BROAD_B and all(k in _B_NOOP for k in kinds):
            return (
                "narrow_swallow: except catches a specific error but only "
                "pass/continue/return None — no recovery action"
            )

        return None


def _handler_exc_name(handler: ast.ExceptHandler) -> str | None:
    """Return the caught exception's simple name, or None for a bare `except:`.

    A tuple (`except (A, B):`) or dotted name returns a sentinel matching no
    broad tier, so such handlers only trip narrow_swallow's empty/pass branch —
    exactly as the old regexes (which required a single bare/simple name) did.
    """
    if handler.type is None:
        return None
    if isinstance(handler.type, ast.Name):
        return handler.type.id
    return "<complex>"


def _stmt_kind(stmt: ast.stmt) -> str:
    """Classify one handler-body statement into a no-op kind or 'real'."""
    if isinstance(stmt, ast.Pass):
        return "pass"
    if isinstance(stmt, ast.Continue):
        return "continue"
    if isinstance(stmt, ast.Return):
        # Only an explicit `return None` counts for the broad-B tier; a bare
        # `return` (value omitted) is treated as a plain return.
        if isinstance(stmt.value, ast.Constant) and stmt.value.value is None:
            return "return_none"
        return "return_val"
    if _is_print_exc(stmt):
        return "print_exc"
    if _is_print_call(stmt):
        return "print"
    return "real"


def _is_print_exc(stmt: ast.stmt) -> bool:
    """`traceback.print_exc()` (or any `<x>.print_exc(...)`) as a statement."""
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Call)
        and isinstance(stmt.value.func, ast.Attribute)
        and stmt.value.func.attr == "print_exc"
    )


def _is_print_call(stmt: ast.stmt) -> bool:
    """A bare `print(...)` call as a statement."""
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Call)
        and isinstance(stmt.value.func, ast.Name)
        and stmt.value.func.id == "print"
    )
