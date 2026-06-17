"""SkillContext — sealed envelope passed to every Skill.invoke().

Skills receive ONLY this context, never the full RuntimeState. This is the
hard boundary that keeps skills from becoming a back door into runtime
state mutation (per CLAUDE.md "RuntimeState — FROZEN").

Adding a new field here is allowed (skills need new context); adding a
field to RuntimeState is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SkillContext:
    """Read-only context every skill receives.

    Fields:
      session_id  : current runtime session — used for event log correlation
      workspace   : current working directory for filesystem skills
      timeout_s   : suggested upper bound for the invocation (skills SHOULD respect)
    """

    session_id: str
    workspace: Path
    timeout_s: int = 30
