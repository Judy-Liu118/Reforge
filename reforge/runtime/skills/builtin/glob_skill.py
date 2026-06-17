"""GlobSkill — match files by glob pattern (rooted in workspace)."""

from __future__ import annotations

import time
from pathlib import Path

from reforge.runtime.skills.builtin._workspace import (
    WorkspaceViolation,
    resolve_safe,
)
from reforge.runtime.skills.context import SkillContext
from reforge.runtime.skills.result import SkillResult

_DEFAULT_LIMIT = 250


class GlobSkill:
    """Match files using Python pathlib glob patterns."""

    name = "glob"
    description = (
        "Find files matching a glob pattern (e.g. '**/*.py', 'src/**/*.tsx'). "
        "Search root defaults to the workspace; pass 'path' to override. "
        "Returns matched file paths sorted by modification time (newest first)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern (e.g. '**/*.py').",
            },
            "path": {
                "type": "string",
                "description": "Search root (absolute or workspace-relative). Defaults to workspace.",
            },
            "limit": {
                "type": "integer",
                "description": f"Maximum number of paths to return (default {_DEFAULT_LIMIT}).",
                "default": _DEFAULT_LIMIT,
            },
        },
        "required": ["pattern"],
    }

    def __init__(self, restrict_to_workspace: bool = True) -> None:
        self._restrict = restrict_to_workspace

    def invoke(self, params: dict, context: SkillContext) -> SkillResult:
        pattern = params.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return SkillResult(success=False, error="glob: 'pattern' is required")

        limit = max(1, int(params.get("limit", _DEFAULT_LIMIT)))
        raw_root = params.get("path")

        start = time.perf_counter()
        try:
            root: Path = (
                resolve_safe(raw_root, context.workspace, restrict=self._restrict)
                if isinstance(raw_root, str) and raw_root
                else context.workspace.resolve()
            )
        except WorkspaceViolation as exc:
            return SkillResult(success=False, error=f"glob: {exc}")

        if not root.exists():
            return SkillResult(success=False, error=f"glob: root not found: {root}")
        if not root.is_dir():
            return SkillResult(success=False, error=f"glob: root is not a directory: {root}")

        try:
            matches = [p for p in root.glob(pattern) if p.is_file()]
        except (ValueError, OSError) as exc:
            return SkillResult(success=False, error=f"glob: {exc}")

        # Sort newest-first by mtime; ties broken by name for determinism.
        matches.sort(key=lambda p: (-p.stat().st_mtime, str(p)))
        sliced = matches[:limit]

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        return SkillResult(
            success=True,
            output="\n".join(str(p) for p in sliced),
            raw=sliced,
            duration_ms=duration_ms,
            metadata={
                "pattern": pattern,
                "root": str(root),
                "total_matches": len(matches),
                "returned": len(sliced),
                "truncated": len(matches) > limit,
            },
        )
