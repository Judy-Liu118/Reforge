"""Process-constraint extraction from user requests.

Some requests encode constraints on HOW the task must be executed, not just
what the final outcome should be. e.g.:

- "故意加乱码让语法出错" → must fail on attempt 1, recover by attempt 2
- "演示 traceback" → exception must propagate, no try/except

Extraction is purely lexical (regex over the request); no LLM involvement.
The resulting TaskRequirements is consumed by the code-generation node to
inject the appropriate directive from prompts/directives.
"""

from __future__ import annotations

import re

from reforge.models.prompts.directives import (
    EXPECTS_UNCAUGHT_PATTERNS,
    MUST_FAIL_FIRST_PATTERNS,
)


def extract_requirements(user_request: str) -> dict | None:
    """Lightweight heuristic — returns None when no constraint is detected."""
    lowered = user_request.lower()
    reqs: dict = {}

    for pat in MUST_FAIL_FIRST_PATTERNS:
        if re.search(pat, lowered):
            reqs["must_fail_first"] = True
            reqs["requires_recovery"] = True
            reqs["expected_final_success"] = True
            break

    for pat in EXPECTS_UNCAUGHT_PATTERNS:
        if re.search(pat, lowered):
            reqs["expects_uncaught_exception"] = True
            break

    return reqs if reqs else None
