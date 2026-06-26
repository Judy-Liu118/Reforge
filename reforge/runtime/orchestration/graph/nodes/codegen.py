"""Code generation node — emit Python code, augmented with retry context.

Two routes, chosen per attempt by `bool(state.image_inputs)`:
  * Text: the default — `LLMClient().chat(...)` for data / general tasks.
  * Vision: when the caller declared input images on the task, call
    `LLMClient.for_vision_codegen().chat_multimodal` with those images so
    the LLM sees the pixels. Text descriptions of a UI lose ~90% of the
    signal (spatial layout, exact colors, font weight).

`state.image_inputs` is task-level immutable — populated once by
`RuntimeRunner.stream()` from the caller and never mutated by any graph
node. That's how "data-task produced PNG happens to live in workspace"
no longer false-routes to vision: routing depends on declaration, not
filesystem state.
"""

from __future__ import annotations

import re
from pathlib import Path

from reforge.models.adapters.llm_client import LLMClient
from reforge.models.prompts.directives import (
    CONSTRAINT_VIOLATION_DIRECTIVE,
    EXPECTS_UNCAUGHT_OVERRIDE,
    MUST_FAIL_FIRST_OVERRIDE,
)
from reforge.models.prompts.templates import (
    CODE_GENERATION_SYSTEM,
    VISION_CODEGEN_SYSTEM,
)
from reforge.runtime.orchestration.retry_context import RetryContextData, build_retry_prompt
from reforge.runtime.domain.state.models import RuntimeState

# Despite the "no markdown fences" rule in CODE_GENERATION_SYSTEM, some LLMs
# (qwen3-coder, occasionally GPT-4 / Claude) still wrap their answer in
# ```python ... ``` and prepend Chinese / English explanation prose. Without
# stripping, the sandbox sees prose-then-fence and crashes with SyntaxError
# on the first non-ASCII character. Extract the code from inside fences; if
# no fence is present, treat the raw response as code (current behaviour).
_FENCE_RE = re.compile(
    r"```(?:python|py)?\s*\n?(.*?)\n?```",
    re.DOTALL | re.IGNORECASE,
)


def _strip_markdown(raw: str) -> str:
    """Pull Python code out of a possibly-fence-wrapped LLM response.

    Multiple fences are concatenated in source order. If no fence appears
    (or every fence is empty), the response is returned as-is — already raw
    Python (happy path) or a degenerate format the sandbox can choke on
    visibly rather than silently swallowing.
    """
    non_empty = [b.strip() for b in _FENCE_RE.findall(raw) if b.strip()]
    if not non_empty:
        return raw.strip()
    return "\n\n".join(non_empty)


def code_generation_node(state: RuntimeState) -> dict:
    eval_result = state.semantic_state.evaluation_result
    is_retry = bool(state.traceback or (eval_result and not eval_result.passed))

    retry_prompt = ""
    if is_retry:
        if eval_result and not eval_result.passed:
            failed = [c.name for c in eval_result.checks if not c.passed]
            if "must_fail_first_violated" in failed:
                retry_prompt = CONSTRAINT_VIOLATION_DIRECTIVE
        if not retry_prompt:
            ctx_data = RetryContextData.from_state(state)
            retry_prompt = "\n\n" + build_retry_prompt(ctx_data)

    extra_system: list[str] = []
    reqs = state.task_requirements
    if (
        reqs
        and reqs.must_fail_first
        and state.control_state.retry_count == 0
        and not state.traceback
        and not eval_result
    ):
        extra_system.append(MUST_FAIL_FIRST_OVERRIDE)
    if reqs and reqs.expects_uncaught_exception:
        extra_system.append(EXPECTS_UNCAUGHT_OVERRIDE)

    use_vision = bool(state.image_inputs)
    target_images: list[Path] = [Path(p) for p in state.image_inputs]

    base_system = VISION_CODEGEN_SYSTEM if use_vision else CODE_GENERATION_SYSTEM
    system_prompt = base_system
    if extra_system:
        system_prompt = base_system + "\n\n" + "\n".join(extra_system)

    user_msg = f"Request: {state.user_request}{retry_prompt}"
    if use_vision:
        llm = LLMClient.for_vision_codegen()
        raw = llm.chat_multimodal(system_prompt, user_msg, target_images)
    else:
        llm = LLMClient()
        raw = llm.chat(system_prompt, user_msg)
    return {"generated_code": _strip_markdown(raw)}
