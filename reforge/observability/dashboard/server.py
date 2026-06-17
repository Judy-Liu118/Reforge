"""DashboardServer — HTML dashboard on top of EventLogObserver.

Routes (in addition to inherited /api/*):
  GET /                       — dashboard home (sessions + outcome chart + live stream)
  GET /sessions/<id>          — per-session event timeline
  GET /memory                 — memory store browser
  GET /skills                 — registered skill catalogue
  GET /api/memory             — memory records as JSON
  GET /api/skills             — registered skills (name + description + schema)

HTML / JS / CSS are inlined in `pages.py` to keep deployment a single
import — no static-file serving fragility.
"""

from __future__ import annotations

import dataclasses
import json
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

from reforge.observability.dashboard.pages import (
    HOME_HTML,
    MEMORY_HTML,
    SESSION_HTML,
    SKILLS_HTML,
)
from reforge.runtime.events.log import ExecutionEventLog
from reforge.runtime.events.observer import _Handler as _ObserverHandler  # JSON+SSE base
from reforge.runtime.skills.registry import SkillRegistry


class _ThreadingHTTPServer(ThreadingMixIn, BaseHTTPRequestHandler.server_version.__class__):
    daemon_threads = True


# Use the proper HTTPServer parent.
from http.server import HTTPServer  # noqa: E402


class _DashThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


class _DashHandler(_ObserverHandler):
    """Extends EventLogObserver's handler with HTML + memory/skills routes."""

    def log_message(self, fmt: str, *args: object) -> None:
        pass  # silence default access log

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)

        # HTML pages
        if path == "/":
            self._html(200, HOME_HTML)
            return
        if path == "/memory":
            self._html(200, MEMORY_HTML)
            return
        if path == "/skills":
            self._html(200, SKILLS_HTML)
            return
        if path.startswith("/sessions/"):
            self._html(200, SESSION_HTML)
            return

        # Memory + skills APIs (added on top of inherited /api/*)
        if path == "/api/memory":
            mem_type = (params.get("type") or [None])[0]
            self._json(200, _load_memory_records(self.server._memory_dir, mem_type))  # type: ignore[attr-defined]
            return
        if path == "/api/skills":
            registry: SkillRegistry | None = self.server._skill_registry  # type: ignore[attr-defined]
            self._json(200, _skills_payload(registry))
            return

        # Fall back to the inherited handler for /api/events, /api/sessions, etc.
        super().do_GET()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _html(self, status: int, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


class DashboardServer:
    """HTML dashboard + JSON APIs over an ExecutionEventLog.

    Adds memory + skills visibility on top of EventLogObserver's event API.
    Optional injections:
      - memory_dir     : Path to MemoryStore JSON dir (default: data/memory/)
      - skill_registry : SkillRegistry to expose; if None, builds the default registry
    """

    def __init__(
        self,
        log: ExecutionEventLog,
        memory_dir: Path | None = None,
        skill_registry: SkillRegistry | None = None,
        host: str = "127.0.0.1",
        port: int = 8080,
    ) -> None:
        self._log = log
        self._memory_dir = memory_dir
        self._skill_registry = skill_registry
        self._server = _DashThreadingHTTPServer((host, port), _DashHandler)
        self._server._event_log = log  # type: ignore[attr-defined]
        self._server._memory_dir = memory_dir  # type: ignore[attr-defined]
        self._server._skill_registry = skill_registry  # type: ignore[attr-defined]
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Properties

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def host(self) -> str:
        return self._server.server_address[0]

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    # ------------------------------------------------------------------
    # Lifecycle

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="dashboard-server"
        )
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._server.shutdown()
        self._thread.join(timeout=5)
        self._thread = None

    def __enter__(self) -> "DashboardServer":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# Memory + Skills payload builders
# ---------------------------------------------------------------------------


_MEMORY_FILES = {
    "RECOVERY": "recovery.json",
    "FAILURE": "failures.json",
    "SUCCESS_PATTERN": "success_patterns.json",
}


def _load_memory_records(memory_dir: Path | None, mem_type: str | None) -> dict:
    """Return memory contents as a JSON-serialisable dict.

    Reads the MemoryStore JSON files directly so the dashboard works whether
    the store is being actively updated or not.
    """
    if memory_dir is None or not memory_dir.exists():
        return {"records": [], "counts": {}}

    types_to_load = [mem_type] if mem_type in _MEMORY_FILES else list(_MEMORY_FILES.keys())
    records: list[dict] = []
    counts: dict[str, int] = {}
    for mt in types_to_load:
        fname = _MEMORY_FILES[mt]
        path = memory_dir / fname
        if not path.exists():
            counts[mt] = 0
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                counts[mt] = len(data)
                # Tag each record with its type for display
                for rec in data:
                    rec["_memory_type"] = mt
                records.extend(data)
        except (json.JSONDecodeError, OSError):
            counts[mt] = 0

    return {"records": records, "counts": counts}


def _skills_payload(registry: SkillRegistry | None) -> dict:
    """Return registered skills as JSON-serialisable dicts."""
    if registry is None:
        from reforge.runtime.skills.builtin import default_skill_registry

        registry = default_skill_registry()

    skills: list[dict] = []
    for skill in registry.list_all():
        skills.append({
            "name": skill.name,
            "description": skill.description,
            "input_schema": skill.input_schema,
            "is_mcp": skill.name.startswith("mcp."),
        })
    return {"skills": skills, "count": len(skills)}
