"""Skill Protocol — uniform shape for any runtime capability."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from reforge.runtime.skills.context import SkillContext
from reforge.runtime.skills.result import SkillResult


@runtime_checkable
class Skill(Protocol):
    """A typed capability the runtime can invoke.

    Implementations declare:
      - name           : unique identifier (used by registry + LLM function-call)
      - description    : short text shown to the LLM for tool selection
      - input_schema   : JSON Schema describing params (OpenAI function-call shape)

    invoke() must be side-effect-isolated: it MAY do I/O, MAY raise on
    invalid params, but MUST NOT touch RuntimeState directly. State changes
    happen through ExecutionEvent / governor (per OWNERSHIP.md).
    """

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def input_schema(self) -> dict: ...

    def invoke(self, params: dict, context: SkillContext) -> SkillResult: ...
