"""EditSkill — string replacement in a file with strict uniqueness check."""

from __future__ import annotations

import time

from reforge.runtime.skills.builtin._workspace import (
    WorkspaceViolation,
    resolve_safe,
)
from reforge.runtime.skills.context import SkillContext
from reforge.runtime.skills.result import SkillResult


class EditSkill:
    """Exact string replacement with strict safety semantics:

      - old_string MUST exist in the file
      - old_string MUST be unique (unless replace_all=True)
      - new_string MUST differ from old_string
      - File is read+written as UTF-8

    These constraints prevent accidental wrong-place edits — the same
    constraints Claude Code's Edit tool enforces.
    """

    name = "edit"
    description = (
        "Perform an exact string replacement in a file. The old_string must be "
        "uniquely present in the file (or pass replace_all=True). Use this for "
        "surgical changes — for full rewrites use python_sandbox to write a new file."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path or path relative to the workspace.",
            },
            "old_string": {
                "type": "string",
                "description": "Exact text to find. Must be unique unless replace_all=True.",
            },
            "new_string": {
                "type": "string",
                "description": "Replacement text. Must differ from old_string.",
            },
            "replace_all": {
                "type": "boolean",
                "default": False,
                "description": "Allow multi-occurrence replacement.",
            },
        },
        "required": ["path", "old_string", "new_string"],
    }

    def __init__(self, restrict_to_workspace: bool = True) -> None:
        self._restrict = restrict_to_workspace

    def invoke(self, params: dict, context: SkillContext) -> SkillResult:
        raw_path = params.get("path")
        old = params.get("old_string")
        new = params.get("new_string")
        replace_all = bool(params.get("replace_all", False))

        if not isinstance(raw_path, str) or not raw_path:
            return SkillResult(success=False, error="edit: 'path' is required")
        if not isinstance(old, str) or not old:
            return SkillResult(
                success=False, error="edit: 'old_string' is required and must be non-empty"
            )
        if not isinstance(new, str):
            return SkillResult(success=False, error="edit: 'new_string' is required")
        if old == new:
            return SkillResult(
                success=False, error="edit: new_string must differ from old_string"
            )

        start = time.perf_counter()
        try:
            resolved = resolve_safe(raw_path, context.workspace, restrict=self._restrict)
        except WorkspaceViolation as exc:
            return SkillResult(success=False, error=f"edit: {exc}")

        if not resolved.exists():
            return SkillResult(success=False, error=f"edit: file not found: {resolved}")
        if not resolved.is_file():
            return SkillResult(success=False, error=f"edit: not a file: {resolved}")

        try:
            content = resolved.read_text(encoding="utf-8")
        except OSError as exc:
            return SkillResult(success=False, error=f"edit: {exc}")

        occurrences = content.count(old)
        if occurrences == 0:
            return SkillResult(
                success=False, error=f"edit: old_string not found in {resolved}"
            )
        if occurrences > 1 and not replace_all:
            return SkillResult(
                success=False,
                error=(
                    f"edit: old_string occurs {occurrences} times in {resolved}; "
                    "make it unique or pass replace_all=true"
                ),
            )

        new_content = (
            content.replace(old, new) if replace_all else content.replace(old, new, 1)
        )
        try:
            resolved.write_text(new_content, encoding="utf-8")
        except OSError as exc:
            return SkillResult(success=False, error=f"edit: write failed: {exc}")

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        return SkillResult(
            success=True,
            output=f"edited {resolved} (replaced {occurrences} occurrence(s))",
            raw=None,
            duration_ms=duration_ms,
            metadata={
                "path": str(resolved),
                "replacements": occurrences if replace_all else 1,
                "replace_all": replace_all,
            },
        )
