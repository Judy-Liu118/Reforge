"""SkillResult — uniform return type for any skill invocation.

Carries both human-readable output (for LLM consumption) and structured
metadata (for runtime decisions). Frozen so callers cannot mutate after
the fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SkillResult:
    """Outcome of one Skill.invoke() call.

    Fields:
      success      : True when the skill achieved its purpose
      output       : Stringified output for LLM consumption (truncate yourself)
      raw          : Original native object (for programmatic consumers)
      error        : Error message when success=False
      duration_ms  : Wall-clock time spent inside invoke()
      metadata     : Free-form additional fields (exit_code, hit_count, etc.)
    """

    success: bool
    output: str = ""
    raw: Any = None
    error: str = ""
    duration_ms: float = 0.0
    metadata: dict = field(default_factory=dict)
