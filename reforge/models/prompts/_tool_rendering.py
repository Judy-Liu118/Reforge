"""Render registered skills into a system-prompt section for codegen.

Codegen takes the code-as-action paradigm: the LLM emits Python that
imports helper functions, rather than emitting structured tool calls.
That means the system prompt — not the OpenAI `tools=` parameter — is
the channel through which the LLM learns what helpers exist.

This module is the bridge. Given an iterable of skills (typically a
subset of the canonical `SkillRegistry`), it produces a markdown-style
text block describing each skill: name, one-line description, argument
list (derived from `input_schema`), and the optional `prompt_fragment`
that carries empirical usage guidance the skill owns.

Keeping the bridge here (next to the prompts that consume it) instead
of inside `runtime/skills/` avoids the skills package taking a
dependency on prompt-formatting concerns. The single source of truth
remains the SkillRegistry; this is a read-only projection of it.
"""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING, Iterable

# Importing `Skill` eagerly would chain through `reforge.runtime.skills.__init__`,
# which transitively imports the research package, which imports this module's
# parent (`templates.py`) — a circular import. The runtime contract is duck-
# typed (we only read `name` / `description` / `input_schema` / `prompt_fragment`
# attributes), so a TYPE_CHECKING-only import is sufficient.
if TYPE_CHECKING:
    from reforge.runtime.skills.protocol import Skill


def render_skill(skill: "Skill") -> str:
    """Render one skill as a `- name` block with description, args, and notes."""
    schema = skill.input_schema or {}
    required = set(schema.get("required", []))
    props: dict = schema.get("properties", {}) or {}

    arg_lines: list[str] = []
    for arg_name, spec in props.items():
        if not isinstance(spec, dict):
            continue
        typ = spec.get("type", "any")
        marker = "" if arg_name in required else " (optional)"
        desc = spec.get("description", "").strip()
        suffix = f" — {desc}" if desc else ""
        arg_lines.append(f"      {arg_name}: {typ}{marker}{suffix}")
    args_block = "\n".join(arg_lines) if arg_lines else "      (no args)"

    fragment = (getattr(skill, "prompt_fragment", "") or "").strip()
    notes_block = (
        f"\n    notes:\n{textwrap.indent(fragment, '      ')}"
        if fragment
        else ""
    )

    return (
        f"  - {skill.name}\n"
        f"    {skill.description.strip()}\n"
        f"    args:\n{args_block}{notes_block}"
    )


def render_codegen_tools(skills: "Iterable[Skill]") -> str:
    """Render an iterable of skills as a contiguous text section.

    Order is preserved from the input — caller controls listing order.
    Empty iterable returns an empty string so the prompt template can
    still interpolate it without injecting stray whitespace.
    """
    blocks = [render_skill(s) for s in skills]
    return "\n\n".join(blocks)
