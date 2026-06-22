"""FailureCategory classifier — maps execution output to semantic failure type.

Pure function with no runtime dependencies.  Used by wrap_execution_node to
populate the `category` and `semantic_meaning` fields of EXECUTION_FAILED events.

Matching order matters: more specific patterns are checked first.
"""

from reforge.runtime.events.models import FailureCategory


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


def categorize_failure(exit_code: int, stderr: str) -> tuple[FailureCategory, str]:
    """Return (category, semantic_meaning) for a failed execution.

    exit_code 0  → ("unknown", "")  — caller should not emit FAILED for success
    empty stderr → ("unknown", "")
    """
    if exit_code == 0 or not stderr:
        return "unknown", ""

    stderr_lower = stderr.lower()

    if "modulenotfounderror" in stderr_lower:
        return "dependency", "missing_package"
    if "importerror" in stderr_lower:
        return "dependency", "import_error"

    if (
        "syntaxerror" in stderr_lower
        or "indentationerror" in stderr_lower
        or "taberror" in stderr_lower
    ):
        return "syntax", "syntax_error"

    if "timeouterror" in stderr_lower or "timed out" in stderr_lower:
        return "timeout", "execution_timeout"

    if "permissionerror" in stderr_lower or "permission denied" in stderr_lower:
        return "policy_blocked", "permission_denied"

    if any(marker in stderr_lower for marker in _RUNTIME_MARKERS):
        return "runtime_error", ""

    return "runtime_error", ""
