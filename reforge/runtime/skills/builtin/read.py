"""ReadSkill — read a file by path, with line numbers and offset/limit."""

from __future__ import annotations

import time

from reforge.runtime.skills.builtin._workspace import (
    WorkspaceViolation,
    resolve_safe,
)
from reforge.runtime.skills.context import SkillContext
from reforge.runtime.skills.result import SkillResult

_DEFAULT_LIMIT = 2000
_MAX_LINE_CHARS = 2000  # truncate ultra-long lines to keep LLM context manageable


class ReadSkill:
    """Read a text file. Output is line-numbered (cat -n style) for LLM reference."""

    name = "read"
    description = (
        "Read a text file from disk. Output is prefixed with 1-based line numbers. "
        "Use offset+limit for files longer than 2000 lines. Path is resolved "
        "relative to the workspace when not absolute."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path or path relative to the workspace.",
            },
            "offset": {
                "type": "integer",
                "description": "0-based line offset to start reading.",
                "default": 0,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of lines to return (default 2000).",
                "default": _DEFAULT_LIMIT,
            },
        },
        "required": ["path"],
    }

    def __init__(self, restrict_to_workspace: bool = True) -> None:
        self._restrict = restrict_to_workspace

    def invoke(self, params: dict, context: SkillContext) -> SkillResult:
        raw_path = params.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            return SkillResult(success=False, error="read: 'path' is required")

        offset = max(0, int(params.get("offset", 0)))
        limit = max(1, int(params.get("limit", _DEFAULT_LIMIT)))

        start = time.perf_counter()
        try:
            resolved = resolve_safe(raw_path, context.workspace, restrict=self._restrict)
        except WorkspaceViolation as exc:
            return SkillResult(success=False, error=f"read: {exc}")

        if not resolved.exists():
            return SkillResult(success=False, error=f"read: file not found: {resolved}")
        if not resolved.is_file():
            return SkillResult(success=False, error=f"read: not a file: {resolved}")

        try:
            with open(resolved, encoding="utf-8", errors="replace") as fh:
                all_lines = fh.readlines()
        except OSError as exc:
            return SkillResult(success=False, error=f"read: {exc}")

        total = len(all_lines)
        slice_ = all_lines[offset : offset + limit]
        formatted = []
        for i, line in enumerate(slice_, start=offset + 1):
            text = line.rstrip("\n")
            if len(text) > _MAX_LINE_CHARS:
                text = text[:_MAX_LINE_CHARS] + " …[truncated]"
            formatted.append(f"{i:6d}\t{text}")

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        return SkillResult(
            success=True,
            output="\n".join(formatted),
            raw=slice_,
            duration_ms=duration_ms,
            metadata={
                "path": str(resolved),
                "total_lines": total,
                "returned_lines": len(slice_),
                "offset": offset,
                "limit": limit,
            },
        )
