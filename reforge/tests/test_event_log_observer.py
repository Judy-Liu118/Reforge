"""P39 — EventLogObserver: read-only HTTP API over ExecutionEventLog.

Tests cover:
  1. Server lifecycle — start, stop, context manager
  2. GET /api/events — empty, populated, session filter
  3. GET /api/sessions — empty, single, multiple
  4. GET /api/summary — total_events, session_count, by_kind
  5. Event visibility — events appended after start are served immediately
  6. Live log updates — mutating the log between requests reflects immediately
  7. 404 for unknown paths
  8. JSON structure of serialised events
  9. Thread safety — concurrent appends visible via API
  10. PersistentEventLog integration
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from reforge.runtime.events.log import ExecutionEventLog
from reforge.runtime.events.models import (
    evaluation_completed,
    execution_failed,
    execution_started,
    execution_succeeded,
    policy_decided,
    recovery_attempted,
    reflection_generated,
    task_completed,
)
from reforge.runtime.events.observer import EventLogObserver
from reforge.runtime.events.persistent_log import PersistentEventLog


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get(url: str) -> object:
    return json.loads(urllib.request.urlopen(url).read())


def _get_status(url: str) -> int:
    try:
        urllib.request.urlopen(url)
        return 200
    except urllib.error.HTTPError as exc:
        return exc.code


# ---------------------------------------------------------------------------
# 1. Server lifecycle
# ---------------------------------------------------------------------------


class TestServerLifecycle:
    def test_port_assigned_when_zero(self) -> None:
        log = ExecutionEventLog()
        with EventLogObserver(log) as obs:
            assert obs.port > 0

    def test_different_instances_get_different_ports(self) -> None:
        log = ExecutionEventLog()
        with EventLogObserver(log) as a, EventLogObserver(log) as b:
            assert a.port != b.port

    def test_base_url_format(self) -> None:
        log = ExecutionEventLog()
        with EventLogObserver(log) as obs:
            assert obs.base_url.startswith("http://127.0.0.1:")
            assert str(obs.port) in obs.base_url

    def test_context_manager_stops_server(self) -> None:
        log = ExecutionEventLog()
        with EventLogObserver(log) as obs:
            url = obs.base_url + "/api/events"
            _get(url)  # works inside context
        # After __exit__ the server is gone; connection must fail
        import socket
        with pytest.raises(Exception):
            urllib.request.urlopen(url, timeout=1)

    def test_start_idempotent(self) -> None:
        log = ExecutionEventLog()
        obs = EventLogObserver(log)
        obs.start()
        obs.start()  # second call must not spawn another thread
        obs.stop()

    def test_stop_without_start(self) -> None:
        log = ExecutionEventLog()
        obs = EventLogObserver(log)
        obs.stop()  # must not raise

    def test_host_property(self) -> None:
        log = ExecutionEventLog()
        with EventLogObserver(log, host="127.0.0.1") as obs:
            assert obs.host == "127.0.0.1"


# ---------------------------------------------------------------------------
# 2. GET /api/events
# ---------------------------------------------------------------------------


class TestEventsEndpoint:
    def test_empty_log_returns_empty_list(self) -> None:
        log = ExecutionEventLog()
        with EventLogObserver(log) as obs:
            data = _get(obs.base_url + "/api/events")
            assert data == []

    def test_returns_all_events(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "task"))
        log.append(execution_succeeded("s1", "task"))
        with EventLogObserver(log) as obs:
            data = _get(obs.base_url + "/api/events")
            assert len(data) == 2

    def test_session_filter_returns_subset(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "task"))
        log.append(execution_started("s2", "task"))
        with EventLogObserver(log) as obs:
            data = _get(obs.base_url + "/api/events?session_id=s1")
            assert len(data) == 1
            assert data[0]["session_id"] == "s1"

    def test_session_filter_unknown_returns_empty(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "task"))
        with EventLogObserver(log) as obs:
            data = _get(obs.base_url + "/api/events?session_id=nobody")
            assert data == []

    def test_event_kind_in_response(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "task"))
        with EventLogObserver(log) as obs:
            data = _get(obs.base_url + "/api/events")
            assert data[0]["kind"] == "EXECUTION_STARTED"

    def test_event_payload_in_response(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "run script"))
        with EventLogObserver(log) as obs:
            data = _get(obs.base_url + "/api/events")
            assert data[0]["payload"]["task"] == "run script"

    def test_event_has_event_id_and_timestamp(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "task"))
        with EventLogObserver(log) as obs:
            data = _get(obs.base_url + "/api/events")
            ev = data[0]
            assert "event_id" in ev
            assert "timestamp" in ev


# ---------------------------------------------------------------------------
# 3. GET /api/sessions
# ---------------------------------------------------------------------------


class TestSessionsEndpoint:
    def test_empty_log_returns_empty_list(self) -> None:
        log = ExecutionEventLog()
        with EventLogObserver(log) as obs:
            data = _get(obs.base_url + "/api/sessions")
            assert data == []

    def test_single_session(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("abc", "task"))
        with EventLogObserver(log) as obs:
            data = _get(obs.base_url + "/api/sessions")
            assert data == ["abc"]

    def test_multiple_sessions_sorted(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("zzz", "task"))
        log.append(execution_started("aaa", "task"))
        log.append(execution_started("mmm", "task"))
        with EventLogObserver(log) as obs:
            data = _get(obs.base_url + "/api/sessions")
            assert data == ["aaa", "mmm", "zzz"]

    def test_duplicate_events_same_session_listed_once(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "task1"))
        log.append(execution_succeeded("s1", "task1"))
        log.append(execution_started("s1", "task2"))
        with EventLogObserver(log) as obs:
            data = _get(obs.base_url + "/api/sessions")
            assert data == ["s1"]


# ---------------------------------------------------------------------------
# 4. GET /api/summary
# ---------------------------------------------------------------------------


class TestSummaryEndpoint:
    def test_empty_log_summary(self) -> None:
        log = ExecutionEventLog()
        with EventLogObserver(log) as obs:
            data = _get(obs.base_url + "/api/summary")
            assert data["total_events"] == 0
            assert data["session_count"] == 0
            assert data["by_kind"] == {}

    def test_total_events_count(self) -> None:
        log = ExecutionEventLog()
        for _ in range(5):
            log.append(execution_started("s1", "task"))
        with EventLogObserver(log) as obs:
            data = _get(obs.base_url + "/api/summary")
            assert data["total_events"] == 5

    def test_session_count(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "t"))
        log.append(execution_started("s2", "t"))
        with EventLogObserver(log) as obs:
            data = _get(obs.base_url + "/api/summary")
            assert data["session_count"] == 2

    def test_by_kind_breakdown(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "t"))
        log.append(execution_started("s2", "t"))
        log.append(execution_succeeded("s1", "t"))
        with EventLogObserver(log) as obs:
            data = _get(obs.base_url + "/api/summary")
            assert data["by_kind"]["EXECUTION_STARTED"] == 2
            assert data["by_kind"]["EXECUTION_SUCCEEDED"] == 1


# ---------------------------------------------------------------------------
# 5. Live event visibility
# ---------------------------------------------------------------------------


class TestLiveVisibility:
    def test_events_appended_after_start_are_served(self) -> None:
        log = ExecutionEventLog()
        with EventLogObserver(log) as obs:
            data_before = _get(obs.base_url + "/api/events")
            log.append(execution_started("s1", "task"))
            data_after = _get(obs.base_url + "/api/events")
            assert len(data_before) == 0
            assert len(data_after) == 1

    def test_multiple_appends_all_visible(self) -> None:
        log = ExecutionEventLog()
        with EventLogObserver(log) as obs:
            for i in range(5):
                log.append(execution_started(f"s{i}", "task"))
            data = _get(obs.base_url + "/api/events")
            assert len(data) == 5

    def test_sessions_updated_after_append(self) -> None:
        log = ExecutionEventLog()
        with EventLogObserver(log) as obs:
            log.append(execution_started("new-session", "task"))
            data = _get(obs.base_url + "/api/sessions")
            assert "new-session" in data


# ---------------------------------------------------------------------------
# 6. 404 for unknown paths
# ---------------------------------------------------------------------------


class TestNotFound:
    def test_unknown_path_returns_404(self) -> None:
        log = ExecutionEventLog()
        with EventLogObserver(log) as obs:
            status = _get_status(obs.base_url + "/api/does-not-exist")
            assert status == 404

    def test_404_body_is_json(self) -> None:
        log = ExecutionEventLog()
        with EventLogObserver(log) as obs:
            try:
                urllib.request.urlopen(obs.base_url + "/unknown")
            except urllib.error.HTTPError as exc:
                body = json.loads(exc.read())
                assert "error" in body


# ---------------------------------------------------------------------------
# 7. Thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_appends_all_visible(self) -> None:
        log = ExecutionEventLog()
        n = 20
        barrier = threading.Barrier(n)

        def worker(i: int) -> None:
            barrier.wait()
            log.append(execution_started(f"s{i}", "task"))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        with EventLogObserver(log) as obs:
            data = _get(obs.base_url + "/api/events")
            assert len(data) == n


# ---------------------------------------------------------------------------
# 8. PersistentEventLog integration
# ---------------------------------------------------------------------------


class TestPersistentLogIntegration:
    def test_persistent_log_events_served(self, tmp_path: Path) -> None:
        log = PersistentEventLog(tmp_path / "e.jsonl")
        log.append(execution_started("s1", "task"))
        log.append(task_completed("s1", "SUCCESS", "ok"))
        with EventLogObserver(log) as obs:
            data = _get(obs.base_url + "/api/events")
            assert len(data) == 2

    def test_loaded_log_served_correctly(self, tmp_path: Path) -> None:
        p = tmp_path / "e.jsonl"
        orig = PersistentEventLog(p)
        orig.append(execution_started("s1", "task"))
        orig.append(execution_succeeded("s1", "task"))

        loaded = PersistentEventLog.load(p)
        with EventLogObserver(loaded) as obs:
            data = _get(obs.base_url + "/api/events")
            assert len(data) == 2
            kinds = {ev["kind"] for ev in data}
            assert kinds == {"EXECUTION_STARTED", "EXECUTION_SUCCEEDED"}
