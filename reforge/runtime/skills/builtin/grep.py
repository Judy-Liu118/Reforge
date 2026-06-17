"""GrepSkill — regex search in files. Pure Python, no ripgrep dependency."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Iterator

from reforge.runtime.skills.builtin._workspace import (
    WorkspaceViolation,
    resolve_safe,
)
from reforge.runtime.skills.context import SkillContext
from reforge.runtime.skills.result import SkillResult

_DEFAULT_LIMIT = 250
_MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB per file
_DEFAULT_EXCLUDE_DIRS = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", "dist", "build", ".idea",
})


class GrepSkill:
    """Search file contents by regex.

    Three output modes (matching ripgrep semantics):
      - "files_with_matches" (default): list paths containing at least one match
      - "content"                     : matching lines with optional line numbers
      - "count"                       : per-file match counts
    """

    name = "grep"
    description = (
        "Search file contents using a regular expression. Walks the workspace "
        "(or given path) recursively. Skips common cache/vendor directories. "
        "Three output modes: files_with_matches | content | count."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Python re pattern (anchored matching).",
            },
            "path": {
                "type": "string",
                "description": "Search root (absolute or workspace-relative). Defaults to workspace.",
            },
            "glob": {
                "type": "string",
                "description": "Glob filter applied to filenames (e.g. '*.py').",
            },
            "output_mode": {
                "type": "string",
                "enum": ["files_with_matches", "content", "count"],
                "default": "files_with_matches",
            },
            "case_insensitive": {"type": "boolean", "default": False},
            "show_line_numbers": {"type": "boolean", "default": True},
            "limit": {
                "type": "integer",
                "description": f"Cap on output rows (default {_DEFAULT_LIMIT}).",
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
            return SkillResult(success=False, error="grep: 'pattern' is required")

        flags = re.IGNORECASE if params.get("case_insensitive") else 0
        try:
            regex = re.compile(pattern, flags)
        except re.error as exc:
            return SkillResult(success=False, error=f"grep: invalid regex: {exc}")

        output_mode = params.get("output_mode", "files_with_matches")
        if output_mode not in {"files_with_matches", "content", "count"}:
            return SkillResult(
                success=False, error=f"grep: unknown output_mode {output_mode!r}"
            )

        glob_filter = params.get("glob")
        show_line_numbers = bool(params.get("show_line_numbers", True))
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
            return SkillResult(success=False, error=f"grep: {exc}")

        if not root.exists() or not root.is_dir():
            return SkillResult(success=False, error=f"grep: root not found or not a dir: {root}")

        files_scanned = 0
        per_file_counts: dict[Path, int] = {}
        content_lines: list[str] = []

        for file_path in _walk_files(root, glob_filter):
            try:
                if file_path.stat().st_size > _MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            files_scanned += 1
            count_here = 0
            try:
                with open(file_path, encoding="utf-8", errors="replace") as fh:
                    for line_no, line in enumerate(fh, start=1):
                        if regex.search(line):
                            count_here += 1
                            if output_mode == "content":
                                prefix = f"{file_path}:{line_no}:" if show_line_numbers else f"{file_path}:"
                                content_lines.append(prefix + line.rstrip("\n"))
                                if len(content_lines) >= limit:
                                    break
            except OSError:
                continue
            if count_here:
                per_file_counts[file_path] = count_here
            if output_mode == "content" and len(content_lines) >= limit:
                break

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        if output_mode == "files_with_matches":
            paths = list(per_file_counts.keys())[:limit]
            return SkillResult(
                success=True,
                output="\n".join(str(p) for p in paths),
                raw=paths,
                duration_ms=duration_ms,
                metadata={
                    "files_scanned": files_scanned,
                    "files_matched": len(per_file_counts),
                    "truncated": len(per_file_counts) > limit,
                },
            )
        if output_mode == "count":
            entries = sorted(per_file_counts.items(), key=lambda kv: -kv[1])[:limit]
            return SkillResult(
                success=True,
                output="\n".join(f"{count}\t{path}" for path, count in entries),
                raw=entries,
                duration_ms=duration_ms,
                metadata={
                    "files_scanned": files_scanned,
                    "files_matched": len(per_file_counts),
                },
            )
        # content
        return SkillResult(
            success=True,
            output="\n".join(content_lines),
            raw=content_lines,
            duration_ms=duration_ms,
            metadata={
                "files_scanned": files_scanned,
                "files_matched": len(per_file_counts),
                "matches_returned": len(content_lines),
                "truncated": len(content_lines) >= limit,
            },
        )


def _walk_files(root: Path, glob_filter: str | None) -> Iterator[Path]:
    """Yield files under root, skipping common cache/vendor directories."""
    import fnmatch
    for path in root.rglob("*"):
        if any(part in _DEFAULT_EXCLUDE_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        if glob_filter and not fnmatch.fnmatch(path.name, glob_filter):
            continue
        yield path
