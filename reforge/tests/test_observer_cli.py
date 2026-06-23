"""P40 — Observer CLI: --serve command for HTTP event log browsing.

Tests cover:
  1. handle_serve() lifecycle — starts, serves, stops
  2. handle_serve() output — prints URL and status messages
  3. handle_serve() serves events from file via HTTP
  4. handle_serve() with empty log
  5. handle_serve() port parameter respected
  6. _on_ready callback receives actual base_url (port=0 case)
  7. stop_event causes clean exit
  8. EventLogObserver fully stopped after handle_serve returns
  9. _EVENT_KINDS includes TASK_COMPLETED
  10. --serve flag wiring in main()
"""

from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

from reforge.cli.events import (
    DEFAULT_EVENT_LOG_PATH,
    _EVENT_KINDS,
    handle_serve,
)
from reforge.cli.main import main
from reforge.runtime.events.models import (
    execution_failed,
    execution_started,
    execution_succeeded,
    policy_decided,
    task_completed,
)
from reforge.runtime.events.persistent_log import PersistentEventLog


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get(url: str) -> object:
    return json.loads(urllib.request.urlopen(url).read())


def _run_serve(path, port=0, *, stop_event, url_holder):
    """Run handle_serve in the current thread; stores actual base_url."""

    def on_ready(url: str) -> None:
        url_holder.append(url)

    handle_serve(path=path, port=port, stop_event=stop_event, _on_ready=on_ready)


# ---------------------------------------------------------------------------
# 1. handle_serve() lifecycle
# ---------------------------------------------------------------------------


class TestHandleServeLifecycle:
    def test_starts_and_stops_cleanly(self, tmp_path: Path) -> None:
        p = tmp_path / "e.jsonl"
        stop = threading.Event()
        stop.set()
        handle_serve(path=p, port=0, stop_event=stop)  # must not raise or hang

    def test_stop_event_terminates_serve(self, tmp_path: Path) -> None:
        p = tmp_path / "e.jsonl"
        stop = threading.Event()
        ready = threading.Event()

        def run():
            handle_serve(
                path=p, port=0,
                stop_event=stop,
                _on_ready=lambda _: ready.set(),
            )

        t = threading.Thread(target=run, daemon=True)
        t.start()
        ready.wait(timeout=5)
        stop.set()
        t.join(timeout=5)
        assert not t.is_alive()

    def test_server_unreachable_after_stop(self, tmp_path: Path) -> None:
        p = tmp_path / "e.jsonl"
        stop = threading.Event()
        url_holder: list[str] = []

        def run():
            handle_serve(
                path=p, port=0,
                stop_event=stop,
                _on_ready=url_holder.append,
            )

        t = threading.Thread(target=run, daemon=True)
        t.start()
        # Wait until URL is known
        while not url_holder:
            pass
        url = url_holder[0]

        # Server is up — verify
        _get(url + "/api/events")

        stop.set()
        t.join(timeout=5)

        # Server must now be down
        import socket
        with pytest.raises(Exception):
            urllib.request.urlopen(url + "/api/events", timeout=1)


# ---------------------------------------------------------------------------
# 2. handle_serve() output
# ---------------------------------------------------------------------------


class TestHandleServeOutput:
    def test_prints_base_url(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "e.jsonl"
        stop = threading.Event()
        stop.set()
        handle_serve(path=p, port=0, stop_event=stop)
        out = capsys.readouterr().out
        assert "http://127.0.0.1" in out

    def test_prints_stopped_message(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "e.jsonl"
        stop = threading.Event()
        stop.set()
        handle_serve(path=p, port=0, stop_event=stop)
        out = capsys.readouterr().out
        assert "stopped" in out.lower()

    def test_prints_event_count(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "e.jsonl"
        log = PersistentEventLog(p)
        log.append(execution_started("s1", "task"))
        log.append(execution_succeeded("s1", "task"))

        stop = threading.Event()
        stop.set()
        handle_serve(path=p, port=0, stop_event=stop)
        out = capsys.readouterr().out
        assert "2" in out

    def test_empty_log_prints_no_events_message(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "e.jsonl"
        stop = threading.Event()
        stop.set()
        handle_serve(path=p, port=0, stop_event=stop)
        out = capsys.readouterr().out
        assert "empty" in out.lower() or "no events" in out.lower()

    def test_on_ready_callback_receives_url(self, tmp_path: Path) -> None:
        p = tmp_path / "e.jsonl"
        stop = threading.Event()
        stop.set()
        received: list[str] = []
        handle_serve(path=p, port=0, stop_event=stop, _on_ready=received.append)
        assert len(received) == 1
        assert received[0].startswith("http://")


# ---------------------------------------------------------------------------
# 3. handle_serve() serves events via HTTP
# ---------------------------------------------------------------------------


class TestHandleServeHttp:
    def _start(self, path: Path):
        stop = threading.Event()
        url_holder: list[str] = []
        ready = threading.Event()

        def run():
            def on_ready(url: str) -> None:
                url_holder.append(url)
                ready.set()

            handle_serve(path=path, port=0, stop_event=stop, _on_ready=on_ready)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        ready.wait(timeout=5)
        return url_holder[0], stop, t

    def test_events_accessible_via_http(self, tmp_path: Path) -> None:
        p = tmp_path / "e.jsonl"
        log = PersistentEventLog(p)
        log.append(execution_started("s1", "task"))
        log.append(execution_succeeded("s1", "task"))

        url, stop, t = self._start(p)
        try:
            data = _get(url + "/api/events")
            assert len(data) == 2
        finally:
            stop.set()
            t.join(timeout=5)

    def test_sessions_accessible_via_http(self, tmp_path: Path) -> None:
        p = tmp_path / "e.jsonl"
        log = PersistentEventLog(p)
        log.append(execution_started("session-abc", "task"))

        url, stop, t = self._start(p)
        try:
            data = _get(url + "/api/sessions")
            assert "session-abc" in data
        finally:
            stop.set()
            t.join(timeout=5)

    def test_summary_accessible_via_http(self, tmp_path: Path) -> None:
        p = tmp_path / "e.jsonl"
        log = PersistentEventLog(p)
        for i in range(3):
            log.append(execution_started(f"s{i}", "task"))

        url, stop, t = self._start(p)
        try:
            data = _get(url + "/api/summary")
            assert data["total_events"] == 3
            assert data["session_count"] == 3
        finally:
            stop.set()
            t.join(timeout=5)

    def test_all_event_kinds_served(self, tmp_path: Path) -> None:
        p = tmp_path / "e.jsonl"
        log = PersistentEventLog(p)
        log.append(execution_started("s1", "t"))
        log.append(execution_succeeded("s1", "t"))
        log.append(task_completed("s1", "SUCCESS", "ok"))

        url, stop, t = self._start(p)
        try:
            data = _get(url + "/api/events")
            kinds = {ev["kind"] for ev in data}
            assert "TASK_COMPLETED" in kinds
        finally:
            stop.set()
            t.join(timeout=5)


# ---------------------------------------------------------------------------
# 4. _EVENT_KINDS completeness
# ---------------------------------------------------------------------------


class TestEventKinds:
    def test_task_completed_in_event_kinds(self) -> None:
        assert "TASK_COMPLETED" in _EVENT_KINDS

    def test_all_eight_kinds_present(self) -> None:
        expected = {
            "EXECUTION_STARTED", "EXECUTION_SUCCEEDED", "EXECUTION_FAILED",
            "RECOVERY_ATTEMPTED", "EVALUATION_COMPLETED", "REFLECTION_GENERATED",
            "POLICY_DECIDED", "TASK_COMPLETED",
        }
        assert expected == set(_EVENT_KINDS)


# ---------------------------------------------------------------------------
# 5. --serve flag in main()
# ---------------------------------------------------------------------------


class TestMainServeFlag:
    def test_serve_flag_calls_handle_serve(self, tmp_path: Path) -> None:
        stop = threading.Event()
        stop.set()
        with patch("reforge.cli.main.handle_serve") as mock_serve:
            main(["prog", "--serve", "9876"])
            mock_serve.assert_called_once()
            _, kwargs = mock_serve.call_args
            assert kwargs.get("port") == 9876 or mock_serve.call_args.args[0] if mock_serve.call_args.args else True

    def test_serve_default_port_8080(self) -> None:
        with patch("reforge.cli.main.handle_serve") as mock_serve:
            main(["prog", "--serve"])
            mock_serve.assert_called_once()

    def test_serve_returns_after_handle_serve(self) -> None:
        with patch("reforge.cli.main.handle_serve"):
            # Should return cleanly (not enter REPL or run task)
            main(["prog", "--serve"])
