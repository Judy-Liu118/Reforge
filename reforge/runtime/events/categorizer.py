"""FailureCategory classifier — maps execution output to semantic failure type.

Pure function with no runtime dependencies.  Used by wrap_execution_node to
populate the `category` and `semantic_meaning` fields of EXECUTION_FAILED events.

Matching order matters: more specific patterns are checked first.
"""

from __future__ import annotations

from reforge.runtime.events.models import FailureCategory


def categorize_failure(exit_code: int, stderr: str) -> tuple[FailureCategory, str]:
    """Return (category, semantic_meaning) for a failed execution.

    exit_code 0  → ("unknown", "")  — caller should not emit FAILED for success
    empty stderr → ("unknown", "")
    """
    if exit_code == 0 or not stderr:
        return "unknown", ""

    lower = stderr.lower()

    # --- Dependency / import failures ---
    if "modulenotfounderror" in lower:
        return "dependency", "missing_package"
    if "importerror" in lower:
        return "dependency", "import_error"

    # --- Syntax / parse errors ---
    if "syntaxerror" in lower or "indentationerror" in lower or "taberror" in lower:
        return "syntax", "syntax_error"

    # --- Timeout ---
    if "timeouterror" in lower or "timed out" in lower:
        return "timeout", "execution_timeout"

    # --- Permission / policy blocked ---
    if "permissionerror" in lower or "permission denied" in lower:
        return "policy_blocked", "permission_denied"

    # --- Common runtime errors ---
    _RUNTIME_MARKERS = (
        "nameerror",
        "typeerror",
        "attributeerror",
        "valueerror",
        "zerodivisionerror",
        "indexerror",
        "keyerror",
        "runtimeerror",
        "assertionerror",
        "recursionerror",
        "overflowerror",
        "memoryerror",
        "stopiteration",
    )
    if any(marker in lower for marker in _RUNTIME_MARKERS):
        return "runtime_error", ""

    return "runtime_error", ""
