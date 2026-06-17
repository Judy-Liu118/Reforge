"""Workspace path-safety helpers used by file-system skills.

The governance-first design philosophy: by default a Skill cannot read or
write outside the current SkillContext.workspace. Callers must explicitly
construct skills with `restrict_to_workspace=False` to opt out.
"""

from __future__ import annotations

from pathlib import Path


class WorkspaceViolation(ValueError):
    """Raised when a skill tries to access a path outside its workspace."""


def resolve_safe(raw: str, workspace: Path, *, restrict: bool = True) -> Path:
    """Resolve *raw* (absolute or workspace-relative) to a Path.

    When restrict=True, raises WorkspaceViolation if the resolved path
    escapes *workspace* (handles ../.. and symlinks via Path.resolve()).
    """
    p = Path(raw)
    if not p.is_absolute():
        p = workspace / p
    resolved = p.resolve()
    if restrict:
        ws_resolved = workspace.resolve()
        try:
            resolved.relative_to(ws_resolved)
        except ValueError as exc:
            raise WorkspaceViolation(
                f"path {raw!r} resolves outside workspace {ws_resolved}"
            ) from exc
    return resolved
