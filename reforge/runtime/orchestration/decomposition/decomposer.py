"""TaskDecomposer — heuristic + LLM task decomposition.

Conservative by design: single-task requests with temporal connectors ("then",
"and") are NOT decomposed. Only explicit step numbering or three+ distinct
sequential goals trigger LLM decomposition.
"""

from __future__ import annotations

import json
import re

from reforge.models.adapters.llm_client import LLMClient
from reforge.models.prompts.templates import DECOMPOSER_SYSTEM
from reforge.runtime.orchestration.decomposition.models import DecompositionResult, SubtaskPlan

# Patterns that strongly suggest multi-step structure
_MULTISTEP_PATTERNS: list[str] = [
    r"第[一二三四五六七八九十\d]+步",          # 第一步, 第二步
    r"步骤\s*[一二三四五六七八九十\d]+",        # 步骤1, 步骤二
    r"(?:^|\n)\s*[1-9]\.\s+\S",               # Numbered list: 1. task
    r"(?:^|\n)\s*Step\s+[1-9]",               # Step 1 / Step 2
    r"(?:首先|第一)[^。\n]{3,}(?:然后|接着|其次)[^。\n]{3,}(?:最后|最终|再|最后一步)",  # 首先...然后...最后
    r"(?:first)[^.]{3,}(?:then)[^.]{3,}(?:finally|lastly|after that)",
    r"Part\s+[12]|PART\s+[12]",               # Part 1 / Part 2
]

_COMPILED = [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in _MULTISTEP_PATTERNS]


def _has_multistep_signals(request: str) -> bool:
    """Quick check: does the request contain explicit multi-step markers?"""
    for pat in _COMPILED:
        if pat.search(request):
            return True
    return False


class TaskDecomposer:
    """Decomposes complex multi-step requests into ordered subtask lists.

    Strategy:
    1. Heuristic check — if no explicit step markers, return single-task immediately
       (zero LLM cost for the common case).
    2. LLM classification — for requests with step signals, ask LLM to decompose.
    3. Fallback — if LLM call fails or returns invalid JSON, treat as single-task.
    """

    def __init__(self, llm: LLMClient | None = None) -> None:
        self._llm = llm or LLMClient()

    def decompose(self, user_request: str) -> DecompositionResult:
        if not _has_multistep_signals(user_request):
            return DecompositionResult.single(user_request)
        return self._llm_decompose(user_request)

    def _llm_decompose(self, user_request: str) -> DecompositionResult:
        try:
            raw = self._llm.chat(DECOMPOSER_SYSTEM, user_request)
            return _parse_response(raw, user_request)
        except Exception:
            return DecompositionResult.single(user_request)


def _parse_response(raw: str, original_request: str) -> DecompositionResult:
    """Parse LLM JSON output into DecompositionResult. Falls back to single-task on error."""
    text = raw.strip()
    # Strip markdown fences if LLM ignored the instruction
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text.strip())

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to extract JSON from surrounding text
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return DecompositionResult.single(original_request)
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError:
            return DecompositionResult.single(original_request)

    is_multistep = bool(data.get("is_multistep", False))
    raw_subtasks = data.get("subtasks", [])
    reasoning = data.get("reasoning", "")

    if not is_multistep or not raw_subtasks:
        return DecompositionResult.single(original_request)

    subtasks = [
        SubtaskPlan(
            index=int(s.get("index", i)),
            request=str(s.get("request", original_request)),
            description=str(s.get("description", "")),
            depends_on=[int(d) for d in s.get("depends_on", []) if isinstance(d, (int, str))],
        )
        for i, s in enumerate(raw_subtasks)
    ]

    # Guard: if only one subtask returned, treat as single-task
    if len(subtasks) <= 1:
        return DecompositionResult.single(original_request)

    return DecompositionResult(
        is_multistep=True,
        subtasks=subtasks,
        reasoning=reasoning,
        original_request=original_request,
    )
