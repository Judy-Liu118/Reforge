"""Save/load trace events as JSON. Each session → runs/{session_id}/trace.json."""

from __future__ import annotations

import json
from pathlib import Path

from reforge.observability.tracing.collector import TraceCollector
from reforge.paths import runs_dir

_RUNS_DIR = runs_dir()


def save_trace(collector: TraceCollector) -> Path:
    """Persist all events from a collector to runs/{session_id}/trace.json."""
    session_dir = _RUNS_DIR / collector.session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    trace_path = session_dir / "trace.json"
    data = {
        "session_id": collector.session_id,
        "outcome": collector.outcome.value,
        "total_events": len(collector.events),
        "events": [e.model_dump() for e in collector.events],
    }
    trace_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return trace_path


def load_trace(session_id: str) -> dict | None:
    """Load a saved trace by session_id (supports prefix match)."""
    if not _RUNS_DIR.exists():
        return None

    for session_dir in _RUNS_DIR.iterdir():
        if not session_dir.is_dir():
            continue
        if session_dir.name.startswith(session_id):
            trace_path = session_dir / "trace.json"
            if trace_path.exists():
                return json.loads(trace_path.read_text(encoding="utf-8"))
            return None
    return None


def list_sessions() -> list[dict]:
    """List all sessions with summary info from their trace files."""
    if not _RUNS_DIR.exists():
        return []

    sessions: list[dict] = []
    for session_dir in sorted(_RUNS_DIR.iterdir(), reverse=True):
        if not session_dir.is_dir():
            continue
        trace_path = session_dir / "trace.json"
        if not trace_path.exists():
            continue
        try:
            data = json.loads(trace_path.read_text(encoding="utf-8"))
            sessions.append({
                "session_id": data.get("session_id", session_dir.name),
                "outcome": data.get("outcome", "UNKNOWN"),
                "total_events": data.get("total_events", 0),
            })
        except (json.JSONDecodeError, KeyError):
            continue
    return sessions
