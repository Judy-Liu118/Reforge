"""EventLogObserver — read-only HTTP API over an ExecutionEventLog.

Exposes the event log as a JSON HTTP service running in a background daemon
thread.  Designed for external monitoring, debugging, and integration with
observability tooling (dashboards, log shippers, test harnesses).

Routes
------
  GET /api/events                     — all events as JSON array
  GET /api/events?session_id=<id>     — events for a single session
  GET /api/sessions                   — sorted list of known session IDs
  GET /api/summary                    — aggregate statistics
  GET /api/events/stream              — SSE stream: historical then live events

All JSON responses are application/json.  Unknown paths return 404.
The /api/events/stream endpoint is text/event-stream (SSE).

Zero external dependencies — uses only Python stdlib.

SSE behaviour
-------------
On connect the server replays all recorded events as individual SSE
``data:`` lines, then pushes each subsequent append in real time.  A
keepalive comment (``: keepalive``) is written every KEEPALIVE_INTERVAL
seconds so proxies do not close idle connections.

Race window: events appended in the tiny gap between replay() and
subscribe() may be missed.  This is acceptable for a monitoring tool;
use the JSON /api/events endpoint for complete snapshots.

Usage
-----
    log = ExecutionEventLog()
    with EventLogObserver(log, port=8080) as obs:
        # server is running; connects at obs.base_url
        ...
    # server stopped, all handler threads are daemon threads and exit

Pass port=0 to let the OS assign a free port (recommended for tests).
"""

from __future__ import annotations

import dataclasses
import json
import queue as queue_mod
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

from reforge.runtime.events.log import ExecutionEventLog

# Seconds between SSE keepalive comments on idle streams.
KEEPALIVE_INTERVAL: int = 15


# ---------------------------------------------------------------------------
# Threading HTTP server — each connection gets its own daemon thread
# ---------------------------------------------------------------------------


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTPServer that dispatches each connection to a daemon thread.

    Required so that long-lived SSE connections do not block concurrent
    JSON requests from other clients.
    """

    daemon_threads = True


# ---------------------------------------------------------------------------
# Internal HTTP handler
# ---------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    """Dispatch GET requests to the event log."""

    def log_message(self, format: str, *args: object) -> None:
        pass  # silence default access log

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        params = parse_qs(parsed.query)

        log: ExecutionEventLog = self.server._event_log  # type: ignore[attr-defined]

        if path == "/api/events":
            session_id = (params.get("session_id") or [None])[0]
            events = log.query(session_id=session_id) if session_id else log.replay()
            self._json(200, [dataclasses.asdict(e) for e in events])

        elif path == "/api/sessions":
            self._json(200, sorted(log.sessions()))

        elif path == "/api/summary":
            all_events = log.replay()
            by_kind: dict[str, int] = {}
            for e in all_events:
                by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
            self._json(200, {
                "total_events": len(all_events),
                "session_count": len(log.sessions()),
                "by_kind": by_kind,
            })

        elif path == "/api/events/stream":
            self._sse(log)

        else:
            self._json(404, {"error": "not found", "path": path})

    # ------------------------------------------------------------------
    # Response helpers

    def _json(self, status: int, body: object) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _sse(self, log: ExecutionEventLog) -> None:
        """Stream events as Server-Sent Events until the client disconnects."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        interval = getattr(self.server, "_keepalive_interval", KEEPALIVE_INTERVAL)
        q: queue_mod.Queue[object] = queue_mod.Queue()

        # Replay historical events, then subscribe for new ones.
        # A tiny race window exists between replay() and subscribe(); see module docstring.
        try:
            for event in log.replay():
                self.wfile.write(
                    b"data: " + json.dumps(dataclasses.asdict(event)).encode() + b"\n\n"
                )
            self.wfile.flush()
        except OSError:
            return

        handle = log.subscribe(q.put)
        try:
            while True:
                try:
                    event = q.get(timeout=interval)
                    self.wfile.write(
                        b"data: " + json.dumps(dataclasses.asdict(event)).encode() + b"\n\n"
                    )
                    self.wfile.flush()
                except queue_mod.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            handle.cancel()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class EventLogObserver:
    """HTTP observer for an ExecutionEventLog.

    Starts a daemon thread serving a read-only JSON + SSE API.  All routes
    reflect the live state of the log — events appended after start() are
    immediately visible to JSON endpoints and pushed to connected SSE clients.

    Parameters
    ----------
    log  : the ExecutionEventLog to expose
    host : bind address (default: 127.0.0.1)
    port : bind port; 0 lets the OS choose a free port
    """

    def __init__(
        self,
        log: ExecutionEventLog,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        self._log = log
        self._server = _ThreadingHTTPServer((host, port), _Handler)
        self._server._event_log = log  # type: ignore[attr-defined]
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Properties

    @property
    def port(self) -> int:
        """The actual bound port (useful when port=0 was passed)."""
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
        """Start the HTTP server in a background daemon thread."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="event-log-observer"
        )
        self._thread.start()

    def stop(self) -> None:
        """Shut down the server and wait for the thread to exit.

        Safe to call even if start() was never invoked — no-op in that case.
        """
        if self._thread is None:
            return
        self._server.shutdown()
        self._thread.join(timeout=5)
        self._thread = None

    # ------------------------------------------------------------------
    # Context manager

    def __enter__(self) -> EventLogObserver:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()
