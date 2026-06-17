"""Events CLI — inspect ExecutionEventLog history from the command line.

Commands (wired into main.py via --events-* flags):
    --events-list           List all sessions with summary statistics
    --events-show <id>      Show full event timeline for a session
    --events-summary        Aggregate event statistics across all sessions
    --serve [port]          Start HTTP observer server (default port: 8080)

The default log path is data/execution_events.jsonl.  All handlers accept
an optional path argument so they are testable without touching disk.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from reforge.observability.dashboard import DashboardServer
from reforge.paths import describe_project, events_path, memory_json_dir
from reforge.runtime.events.persistent_log import PersistentEventLog
from reforge.runtime.events.replay import SessionReplay

DEFAULT_EVENT_LOG_PATH = events_path()

_EVENT_KINDS = [
    "EXECUTION_STARTED",
    "EXECUTION_SUCCEEDED",
    "EXECUTION_FAILED",
    "RECOVERY_ATTEMPTED",
    "EVALUATION_COMPLETED",
    "REFLECTION_GENERATED",
    "POLICY_DECIDED",
    "TASK_COMPLETED",
]


def handle_events_list(path: Path | None = None) -> None:
    """List all sessions recorded in the event log."""
    log = PersistentEventLog.load(path or DEFAULT_EVENT_LOG_PATH)
    print(f"Events: {describe_project()}")
    if not log.sessions():
        print("No event sessions found.")
        return

    replay = SessionReplay(log)
    summaries = replay.all_summaries()

    print(f"Sessions: {len(summaries)}\n")
    print(f"{'Session':<18} {'Outcome':<14} {'Attempts':<10} {'Events'}")
    print("-" * 54)
    for s in summaries:
        event_count = len(log.query(session_id=s.session_id))
        print(f"  {s.session_id:<16} {s.final_outcome:<14} {s.total_attempts:<10} {event_count}")


def handle_events_show(session_id: str, path: Path | None = None) -> None:
    """Show the full event timeline for a specific session."""
    log = PersistentEventLog.load(path or DEFAULT_EVENT_LOG_PATH)
    if session_id not in log.sessions():
        print(f"Session not found: {session_id}")
        print("Use --events-list to see available sessions.")
        return

    replay = SessionReplay(log)
    print(replay.render(session_id))


def handle_events_summary(path: Path | None = None) -> None:
    """Print aggregate event statistics across all sessions."""
    log = PersistentEventLog.load(path or DEFAULT_EVENT_LOG_PATH)
    if len(log) == 0:
        print("No events recorded yet.")
        return

    print(f"Total events : {len(log)}")
    print(f"Sessions     : {len(log.sessions())}\n")

    for kind in _EVENT_KINDS:
        count = len(log.query(kind=kind))
        if count:
            print(f"  {kind:<30} {count}")


def handle_serve(
    path: Path | None = None,
    port: int = 8080,
    stop_event: threading.Event | None = None,
    _on_ready: Callable[[str], None] | None = None,
) -> None:
    """Start the web dashboard and serve until interrupted.

    Loads the event log JSONL at *path* and exposes the runtime dashboard
    (HTML pages + JSON APIs + SSE stream + memory + skills) on the given port.

    Pass *_on_ready* to receive the actual base_url once the server is
    listening — useful when port=0 so the OS assigns a free port.
    """
    p = path or DEFAULT_EVENT_LOG_PATH
    log = PersistentEventLog.load(p)
    n = len(log)

    # Events are project-scoped; memory is global, so it lives elsewhere.
    memory_dir = memory_json_dir()

    with DashboardServer(log, memory_dir=memory_dir, port=port) as dash:
        if n == 0:
            print("No events recorded yet — serving an empty log.")
        else:
            print(f"Serving {n} event(s) from {p}")
        print(f"Dashboard: {dash.base_url}")
        print("Press Ctrl+C to stop.")

        if _on_ready is not None:
            _on_ready(dash.base_url)

        if stop_event is not None:
            stop_event.wait()
        else:
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass

    print("Dashboard stopped.")
