"""Centralised data paths for the Reforge runtime.

Two scopes:

- **Global** (`~/.reforge/` or `$REFORGE_HOME`) — cross-project artefacts:
  memory database, trajectory log, user `.env`. Sharing these across
  projects is what makes `recall_repair_pattern` actually transfer repairs
  between tasks.
- **Project** (`./.reforge/` under cwd) — this working tree's ledger:
  ExecutionEvent stream, trace runs, session history, short-term working
  memory. `cd` to a different project and you get a fresh ledger; the
  global memory is still there.

Resolution happens at call time, not at module load, so tests can
`monkeypatch.setenv("REFORGE_HOME", ...)` or `monkeypatch.chdir(...)`
to redirect without re-importing.

Path accessors return :class:`pathlib.Path` objects only — they do NOT
create the parent directory. Callers writing to the path are responsible
for ``path.parent.mkdir(parents=True, exist_ok=True)``.
"""
from __future__ import annotations

import os
from pathlib import Path


# ---------------------------------------------------------------------------
# Scope roots
# ---------------------------------------------------------------------------

def global_dir() -> Path:
    """Return the global Reforge home (cross-project state lives here)."""
    override = os.environ.get("REFORGE_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".reforge"


def project_dir() -> Path:
    """Return the per-project Reforge dir under the current working dir."""
    return Path.cwd() / ".reforge"


# ---------------------------------------------------------------------------
# Global scope accessors
# ---------------------------------------------------------------------------

def memory_db_path() -> Path:
    """SQLite memory database — RECOVERY / SUCCESS_PATTERN records."""
    return global_dir() / "memory" / "memory.db"


def memory_json_dir() -> Path:
    """Legacy JSON memory store directory."""
    return global_dir() / "memory"


def trajectories_path() -> Path:
    """Cross-session trajectory log (research mode)."""
    return global_dir() / "trajectories.jsonl"


def multistep_trajectories_path() -> Path:
    return global_dir() / "multistep_trajectories.jsonl"


def research_path() -> Path:
    """Append-only research-session result log."""
    return global_dir() / "research.jsonl"


def env_path() -> Path:
    """Canonical location for the user's `.env`. See :func:`resolve_env_file`
    for the actual lookup order used at config load time."""
    return global_dir() / ".env"


# ---------------------------------------------------------------------------
# Project scope accessors
# ---------------------------------------------------------------------------

def events_path() -> Path:
    """Append-only ExecutionEvent log for this project."""
    return project_dir() / "events.jsonl"


def execution_memory_path() -> Path:
    """Short-term working-memory JSONL for this project."""
    return project_dir() / "execution_memory.jsonl"


def runs_dir() -> Path:
    """Per-session trace artefact directory for this project."""
    return project_dir() / "runs"


def history_dir() -> Path:
    """Session-history JSON files for this project."""
    return project_dir() / "history"


# ---------------------------------------------------------------------------
# .env resolution
# ---------------------------------------------------------------------------

def _package_env_path() -> Path:
    """`.env` shipped with a dev/editable install (legacy fallback)."""
    return Path(__file__).resolve().parent.parent / ".env"


def resolve_env_file() -> Path | None:
    """Find a `.env` to load, in priority order.

    1. ``./.env`` in the current working directory
    2. ``$REFORGE_HOME/.env`` (or ``~/.reforge/.env``)
    3. Package root ``.env`` (only meaningful for editable installs)

    Returns the first one that exists, or ``None`` if none do.
    """
    candidates = [Path.cwd() / ".env", env_path(), _package_env_path()]
    for c in candidates:
        if c.is_file():
            return c
    return None


# ---------------------------------------------------------------------------
# Convenience: scope description for CLI output
# ---------------------------------------------------------------------------

def describe_global() -> str:
    """One-line summary of the global scope (for CLI scope hints)."""
    return f"{global_dir()} (global, shared across projects)"


def describe_project() -> str:
    """One-line summary of the project scope (for CLI scope hints)."""
    return f"{project_dir()} (project: {Path.cwd()})"
