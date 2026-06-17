"""P41 — SSE Streaming: /api/events/stream endpoint.

Tests cover:
  1. Endpoint basics — 200, Content-Type: text/event-stream
  2. Historical events sent on connect
  3. New events pushed in real time via subscriber
  4. SSE data lines are valid JSON with correct fields
  5. Keepalive comments do not interfere with parsing
  6. JSON endpoints work concurrently alongside SSE connections
  7. Multiple SSE clients each receive events
  8. Disconnect does not crash the server (server continues serving)
  9. Subscription cancelled after client disconnects
  10. Empty log — no data lines, only keepalives
"""

from __future__ import annotations

import json
import threading
import time
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


def _get_json(url: str) -> object:
    return json.loads(urllib.request.urlopen(url).read())


def _read_sse_lines(url: str, n_data: int, timeout: float = 5.0) -> list[dict]:
    """Read until *n_data* SSE ``data:`` lines arrive, then return them.

    Ignores SSE comment lines (starting with ``:``) such as keepalives.
    Returns whatever was collected within *timeout* seconds.
    """
    collected: list[dict] = []
    done = threading.Event()

    def _reader() -> None:
        try:
            with urllib.request.urlopen(url) as resp:
                for raw in resp:
                    line = raw.decode().strip()
                    if line.startswith("data: "):
                        collected.append(json.loads(line[6:]))
                        if len(collected) >= n_data:
                            done.set()
                            return
        except Exception:
            done.set()

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    done.wait(timeout=timeout)
    return collected


def _observer_with_fast_keepalive(log: ExecutionEventLog) -> EventLogObserver:
    """Return an observer with a 1-second keepalive for faster tests."""
    obs = EventLogObserver(log)
    obs._server._keepalive_interval = 1  # type: ignore[attr-defined]
    return obs


# ---------------------------------------------------------------------------
# 1. Endpoint basics
# ---------------------------------------------------------------------------


class TestSSEEndpointBasics:
    def test_stream_returns_200(self) -> None:
        log = ExecutionEventLog()
        with EventLogObserver(log) as obs:
            resp = urllib.request.urlopen(obs.base_url + "/api/events/stream")
            assert resp.status == 200
            resp.close()

    def test_stream_content_type_is_event_stream(self) -> None:
        log = ExecutionEventLog()
        with EventLogObserver(log) as obs:
            resp = urllib.request.urlopen(obs.base_url + "/api/events/stream")
            ct = resp.headers.get("Content-Type", "")
            resp.close()
            assert "text/event-stream" in ct

    def test_stream_path_distinct_from_events_json(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "task"))
        with EventLogObserver(log) as obs:
            json_data = _get_json(obs.base_url + "/api/events")
            assert isinstance(json_data, list)
            resp = urllib.request.urlopen(obs.base_url + "/api/events/stream")
            ct = resp.headers.get("Content-Type", "")
            resp.close()
            assert "text/event-stream" in ct


# ---------------------------------------------------------------------------
# 2. Historical events on connect
# ---------------------------------------------------------------------------


class TestSSEHistoricalEvents:
    def test_empty_log_no_data_lines(self) -> None:
        log = ExecutionEventLog()
        obs = _observer_with_fast_keepalive(log)
        with obs:
            # With fast keepalive the reader gets keepalive lines but no data
            collected = _read_sse_lines(obs.base_url + "/api/events/stream", n_data=1, timeout=1.5)
            assert collected == []

    def test_one_historical_event_received(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "run script"))
        with EventLogObserver(log) as obs:
            events = _read_sse_lines(obs.base_url + "/api/events/stream", n_data=1)
            assert len(events) == 1

    def test_historical_event_kind_correct(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "task"))
        with EventLogObserver(log) as obs:
            events = _read_sse_lines(obs.base_url + "/api/events/stream", n_data=1)
            assert events[0]["kind"] == "EXECUTION_STARTED"

    def test_historical_event_payload_correct(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "my-task"))
        with EventLogObserver(log) as obs:
            events = _read_sse_lines(obs.base_url + "/api/events/stream", n_data=1)
            assert events[0]["payload"]["task"] == "my-task"

    def test_multiple_historical_events_received_in_order(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "task"))
        log.append(execution_succeeded("s1", "task"))
        log.append(task_completed("s1", "SUCCESS", "ok"))
        with EventLogObserver(log) as obs:
            events = _read_sse_lines(obs.base_url + "/api/events/stream", n_data=3)
            assert len(events) == 3
            assert events[0]["kind"] == "EXECUTION_STARTED"
            assert events[1]["kind"] == "EXECUTION_SUCCEEDED"
            assert events[2]["kind"] == "TASK_COMPLETED"

    def test_all_eight_kinds_streamed(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "t"))
        log.append(execution_succeeded("s1", "t"))
        log.append(execution_failed("s1", "t", category="syntax", recoverable=True, error="e"))
        log.append(recovery_attempted("s1", "t", "llm_retry", 1))
        log.append(evaluation_completed("s1", score=0.5, passed=False))
        log.append(reflection_generated("s1", "root cause"))
        log.append(policy_decided("s1", "ACCEPT", "ok"))
        log.append(task_completed("s1", "SUCCESS", "done"))
        with EventLogObserver(log) as obs:
            events = _read_sse_lines(obs.base_url + "/api/events/stream", n_data=8)
            kinds = {e["kind"] for e in events}
            assert len(kinds) == 8


# ---------------------------------------------------------------------------
# 3. Live event push
# ---------------------------------------------------------------------------


class TestSSELiveEvents:
    def test_event_appended_after_connect_is_received(self) -> None:
        log = ExecutionEventLog()
        with EventLogObserver(log) as obs:
            # Start reading — no historical events
            ready = threading.Event()
            collected: list[dict] = []

            def reader() -> None:
                try:
                    with urllib.request.urlopen(obs.base_url + "/api/events/stream") as resp:
                        ready.set()
                        for raw in resp:
                            line = raw.decode().strip()
                            if line.startswith("data: "):
                                collected.append(json.loads(line[6:]))
                                return
                except Exception:
                    pass

            t = threading.Thread(target=reader, daemon=True)
            t.start()
            ready.wait(timeout=3)
            time.sleep(0.05)  # let reader block on read

            log.append(execution_started("s1", "live-task"))
            t.join(timeout=5)
            assert len(collected) == 1
            assert collected[0]["kind"] == "EXECUTION_STARTED"

    def test_multiple_live_events_received_in_order(self) -> None:
        log = ExecutionEventLog()
        with EventLogObserver(log) as obs:
            collected: list[dict] = []
            done = threading.Event()
            n = 3

            def reader() -> None:
                try:
                    with urllib.request.urlopen(obs.base_url + "/api/events/stream") as resp:
                        for raw in resp:
                            line = raw.decode().strip()
                            if line.startswith("data: "):
                                collected.append(json.loads(line[6:]))
                                if len(collected) >= n:
                                    done.set()
                                    return
                except Exception:
                    done.set()

            t = threading.Thread(target=reader, daemon=True)
            t.start()
            time.sleep(0.05)

            log.append(execution_started("s1", "t"))
            log.append(execution_succeeded("s1", "t"))
            log.append(task_completed("s1", "SUCCESS", "ok"))

            done.wait(timeout=5)
            t.join(timeout=3)

            assert len(collected) == n
            assert collected[0]["kind"] == "EXECUTION_STARTED"
            assert collected[2]["kind"] == "TASK_COMPLETED"


# ---------------------------------------------------------------------------
# 4. SSE data format
# ---------------------------------------------------------------------------


class TestSSEDataFormat:
    def test_sse_data_has_event_id(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "task"))
        with EventLogObserver(log) as obs:
            events = _read_sse_lines(obs.base_url + "/api/events/stream", n_data=1)
            assert "event_id" in events[0]

    def test_sse_data_has_timestamp(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "task"))
        with EventLogObserver(log) as obs:
            events = _read_sse_lines(obs.base_url + "/api/events/stream", n_data=1)
            assert "timestamp" in events[0]

    def test_sse_data_has_session_id(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("my-session", "task"))
        with EventLogObserver(log) as obs:
            events = _read_sse_lines(obs.base_url + "/api/events/stream", n_data=1)
            assert events[0]["session_id"] == "my-session"

    def test_sse_data_has_payload(self) -> None:
        log = ExecutionEventLog()
        log.append(evaluation_completed("s1", score=0.9, passed=True))
        with EventLogObserver(log) as obs:
            events = _read_sse_lines(obs.base_url + "/api/events/stream", n_data=1)
            assert "payload" in events[0]
            assert events[0]["payload"]["score"] == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# 5. Concurrency — SSE + JSON in parallel
# ---------------------------------------------------------------------------


class TestSSEConcurrency:
    def test_json_endpoint_works_while_sse_connected(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "task"))
        with EventLogObserver(log) as obs:
            # Keep an SSE connection open in background
            sse_resp = urllib.request.urlopen(obs.base_url + "/api/events/stream")
            try:
                # JSON endpoint must still respond
                data = _get_json(obs.base_url + "/api/events")
                assert len(data) == 1
                data2 = _get_json(obs.base_url + "/api/summary")
                assert data2["total_events"] == 1
            finally:
                sse_resp.close()

    def test_two_sse_clients_both_receive_historical(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "task"))
        with EventLogObserver(log) as obs:
            a = _read_sse_lines(obs.base_url + "/api/events/stream", n_data=1)
            b = _read_sse_lines(obs.base_url + "/api/events/stream", n_data=1)
            assert len(a) == 1
            assert len(b) == 1

    def test_two_sse_clients_both_receive_live_event(self) -> None:
        log = ExecutionEventLog()
        with EventLogObserver(log) as obs:
            results_a: list[dict] = []
            results_b: list[dict] = []
            done_a = threading.Event()
            done_b = threading.Event()

            def reader(results: list[dict], done: threading.Event) -> None:
                try:
                    with urllib.request.urlopen(obs.base_url + "/api/events/stream") as resp:
                        for raw in resp:
                            line = raw.decode().strip()
                            if line.startswith("data: "):
                                results.append(json.loads(line[6:]))
                                done.set()
                                return
                except Exception:
                    done.set()

            ta = threading.Thread(target=reader, args=(results_a, done_a), daemon=True)
            tb = threading.Thread(target=reader, args=(results_b, done_b), daemon=True)
            ta.start()
            tb.start()
            time.sleep(0.1)

            log.append(execution_started("s1", "task"))

            done_a.wait(timeout=5)
            done_b.wait(timeout=5)

            assert len(results_a) == 1
            assert len(results_b) == 1


# ---------------------------------------------------------------------------
# 6. Disconnect resilience
# ---------------------------------------------------------------------------


class TestSSEDisconnect:
    def test_disconnect_does_not_crash_server(self) -> None:
        log = ExecutionEventLog()
        with EventLogObserver(log) as obs:
            # Connect and immediately close
            resp = urllib.request.urlopen(obs.base_url + "/api/events/stream")
            resp.close()
            time.sleep(0.1)  # give handler time to notice disconnect

            # Server must still respond to regular requests
            data = _get_json(obs.base_url + "/api/events")
            assert data == []

    def test_server_accepts_new_connections_after_disconnect(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "task"))
        with EventLogObserver(log) as obs:
            # First connection: connect and disconnect
            resp = urllib.request.urlopen(obs.base_url + "/api/events/stream")
            resp.close()
            time.sleep(0.05)

            # Second connection: must receive the historical event
            events = _read_sse_lines(obs.base_url + "/api/events/stream", n_data=1)
            assert len(events) == 1


# ---------------------------------------------------------------------------
# 7. PersistentEventLog integration
# ---------------------------------------------------------------------------


class TestSSEPersistentLog:
    def test_persistent_log_historical_events_streamed(self, tmp_path: Path) -> None:
        p = tmp_path / "e.jsonl"
        plog = PersistentEventLog(p)
        plog.append(execution_started("s1", "task"))
        plog.append(task_completed("s1", "SUCCESS", "ok"))

        loaded = PersistentEventLog.load(p)
        with EventLogObserver(loaded) as obs:
            events = _read_sse_lines(obs.base_url + "/api/events/stream", n_data=2)
            assert len(events) == 2
            kinds = [e["kind"] for e in events]
            assert kinds == ["EXECUTION_STARTED", "TASK_COMPLETED"]
