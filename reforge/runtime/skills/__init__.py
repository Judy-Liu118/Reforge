"""Skill abstraction layer — uniform wrapper for runtime capabilities.

A Skill is a typed capability the runtime can invoke. Two invocation
paradigms are supported:

  1. code-as-action (Reforge native):
     LLM generates Python code that imports skills as a library:
       `from reforge.skills import read; content = read('foo.py')`

  2. tool-as-action (Claude Code style):
     LLM emits a structured function call:
       `{"skill": "read", "params": {"path": "foo.py"}}`
     The codegen node selects which paradigm fits the task.

Both paths go through the same Skill.invoke() method, so:
  - Governor / memory / events behave identically
  - RuntimeState is never modified by skills (CLAUDE.md frozen)
  - Skill results are emitted as ExecutionEvent records

See OWNERSHIP.md for subsystem boundaries.
"""

from reforge.runtime.skills.context import SkillContext
from reforge.runtime.skills.protocol import Skill
from reforge.runtime.skills.registry import SkillRegistry
from reforge.runtime.skills.result import SkillResult

__all__ = [
    "Skill",
    "SkillContext",
    "SkillRegistry",
    "SkillResult",
]
