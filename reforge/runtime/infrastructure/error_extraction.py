"""Shared traceback → error-type extractor.

Used by execution_node and reflection_node so the two stay in sync.
Returns the dotted-or-bare exception name (e.g. "ValueError",
"pandas.errors.ParserError"). Unmatched traceback returns the *default*
sentinel — callers choose whether "" or "UnknownError" suits them.
"""

from __future__ import annotations


def extract_error_type(traceback: str, *, default: str = "") -> str:
    if not traceback:
        return default
    for line in traceback.strip().split("\n"):
        line = line.strip()
        for sep in ("Error", "Warning", "Exception"):
            idx = line.find(sep)
            if idx == -1:
                continue
            start = idx
            while start > 0 and (line[start - 1].isalpha() or line[start - 1] == "."):
                start -= 1
            return line[start : idx + len(sep)]
    return default
