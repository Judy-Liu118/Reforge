"""P36 — Event Subscriber / Hook System.

Tests cover:
  1. Basic subscription: subscribe, receive events, unsubscribe
  2. SubscriptionHandle.cancel() lifecycle
  3. Error isolation: subscriber exceptions don't crash the log
  4. Multiple subscribers: all notified independently
  5. PersistentEventLog subscriber integration
  6. load() does not fire subscribers (reconstruction is not new events)
  7. Thread safety: concurrent appends notify all subscribers
"""

from __future__ import annotations

import threading
from pathlib import Path


from reforge.runtime.events.log import ExecutionEventLog, SubscriptionHandle
from reforge.runtime.events.models import (
    evaluation_completed,
    execution_failed,
    execution_started,
    execution_succeeded,
    policy_decided,
    recovery_attempted,
    reflection_generated,
)
from reforge.runtime.events.persistent_log import PersistentEventLog


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _started(sid: str = "s1") -> object:
    return execution_started(sid, "run code")


def _all_kinds(sid: str = "s1") -> list:
    return [
        execution_started(sid, "task"),
        execution_succeeded(sid, "task"),
        execution_failed(sid, "task", category="syntax", recoverable=True, error="err"),
        recovery_attempted(sid, "task", "llm_retry", 1),
        evaluation_completed(sid, score=0.8, passed=True),
        reflection_generated(sid, "root cause"),
        policy_decided(sid, "ACCEPT", "clean"),
    ]


# ---------------------------------------------------------------------------
# 1. Basic subscription
# ---------------------------------------------------------------------------


class TestBasicSubscription:
    def test_subscribe_returns_handle(self) -> None:
        log = ExecutionEventLog()
        handle = log.subscribe(lambda e: None)
        assert isinstance(handle, SubscriptionHandle)

    def test_subscriber_called_on_append(self) -> None:
        log = ExecutionEventLog()
        received: list = []
        log.subscribe(received.append)
        log.append(_started())
        assert len(received) == 1

    def test_subscriber_receives_correct_event(self) -> None:
        log = ExecutionEventLog()
        received: list = []
        log.subscribe(received.append)
        ev = _started("my-session")
        log.append(ev)
        assert received[0].event_id == ev.event_id

    def test_subscriber_not_called_before_append(self) -> None:
        log = ExecutionEventLog()
        received: list = []
        log.subscribe(received.append)
        assert len(received) == 0

    def test_subscriber_called_for_each_append(self) -> None:
        log = ExecutionEventLog()
        count: list = []
        log.subscribe(lambda e: count.append(1))
        for ev in _all_kinds():
            log.append(ev)
        assert len(count) == len(_all_kinds())

    def test_all_event_kinds_trigger_subscriber(self) -> None:
        log = ExecutionEventLog()
        kinds: list = []
        log.subscribe(lambda e: kinds.append(e.kind))
        for ev in _all_kinds("s1"):
            log.append(ev)
        assert set(kinds) == {
            "EXECUTION_STARTED", "EXECUTION_SUCCEEDED", "EXECUTION_FAILED",
            "RECOVERY_ATTEMPTED", "EVALUATION_COMPLETED", "REFLECTION_GENERATED",
            "POLICY_DECIDED",
        }

    def test_past_events_not_replayed_to_new_subscriber(self) -> None:
        log = ExecutionEventLog()
        log.append(_started())
        log.append(_started())
        received: list = []
        log.subscribe(received.append)  # subscribe AFTER two events
        assert len(received) == 0

    def test_unsubscribe_stops_notifications(self) -> None:
        log = ExecutionEventLog()
        received: list = []
        handle = log.subscribe(received.append)
        log.append(_started())
        log.unsubscribe(handle)
        log.append(_started())  # should not be received
        assert len(received) == 1


# ---------------------------------------------------------------------------
# 2. SubscriptionHandle lifecycle
# ---------------------------------------------------------------------------


class TestSubscriptionHandle:
    def test_cancel_stops_notifications(self) -> None:
        log = ExecutionEventLog()
        received: list = []
        handle = log.subscribe(received.append)
        log.append(_started())
        handle.cancel()
        log.append(_started())
        assert len(received) == 1

    def test_cancel_is_idempotent(self) -> None:
        log = ExecutionEventLog()
        handle = log.subscribe(lambda e: None)
        handle.cancel()
        handle.cancel()  # second cancel is a no-op, must not raise

    def test_handle_references_correct_log(self) -> None:
        log = ExecutionEventLog()
        handle = log.subscribe(lambda e: None)
        assert handle._log is log

    def test_two_handles_are_independent(self) -> None:
        log = ExecutionEventLog()
        received_a: list = []
        received_b: list = []
        handle_a = log.subscribe(received_a.append)
        log.subscribe(received_b.append)
        log.append(_started())
        handle_a.cancel()
        log.append(_started())  # only b receives this
        assert len(received_a) == 1
        assert len(received_b) == 2


# ---------------------------------------------------------------------------
# 3. Error isolation
# ---------------------------------------------------------------------------


class TestErrorIsolation:
    def test_subscriber_exception_does_not_raise(self) -> None:
        log = ExecutionEventLog()
        log.subscribe(lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
        log.append(_started())  # must not raise

    def test_subscriber_exception_does_not_block_other_subscribers(self) -> None:
        log = ExecutionEventLog()
        received: list = []
        log.subscribe(lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
        log.subscribe(received.append)
        log.append(_started())
        assert len(received) == 1

    def test_log_still_records_event_after_subscriber_exception(self) -> None:
        log = ExecutionEventLog()
        log.subscribe(lambda e: (_ for _ in ()).throw(ValueError("bad")))
        log.append(_started("s1"))
        assert len(log) == 1
        assert len(log.query(session_id="s1")) == 1


# ---------------------------------------------------------------------------
# 4. Multiple subscribers
# ---------------------------------------------------------------------------


class TestMultipleSubscribers:
    def test_two_subscribers_both_called(self) -> None:
        log = ExecutionEventLog()
        a: list = []
        b: list = []
        log.subscribe(a.append)
        log.subscribe(b.append)
        log.append(_started())
        assert len(a) == 1
        assert len(b) == 1

    def test_three_subscribers_all_receive_same_event(self) -> None:
        log = ExecutionEventLog()
        received: list[list] = [[], [], []]
        for lst in received:
            log.subscribe(lst.append)
        ev = _started("unique-session")
        log.append(ev)
        for lst in received:
            assert lst[0].session_id == "unique-session"


# ---------------------------------------------------------------------------
# 5. PersistentEventLog subscriber integration
# ---------------------------------------------------------------------------


class TestPersistentLogSubscribers:
    def test_persistent_log_notifies_subscribers(self, tmp_path: Path) -> None:
        log = PersistentEventLog(tmp_path / "e.jsonl")
        received: list = []
        log.subscribe(received.append)
        log.append(_started())
        assert len(received) == 1

    def test_persistent_log_subscriber_called_after_disk_write(
        self, tmp_path: Path
    ) -> None:
        p = tmp_path / "e.jsonl"
        log = PersistentEventLog(p)
        disk_had_event: list[bool] = []

        def check_disk(event: object) -> None:
            lines = [ln for ln in p.read_text().splitlines() if ln.strip()]
            disk_had_event.append(len(lines) >= 1)

        log.subscribe(check_disk)
        log.append(_started())
        assert disk_had_event == [True]

    def test_load_does_not_notify_subscribers(self, tmp_path: Path) -> None:
        p = tmp_path / "e.jsonl"
        orig = PersistentEventLog(p)
        orig.append(_started())
        orig.append(_started())

        loaded = PersistentEventLog.load(p)
        received: list = []
        loaded.subscribe(received.append)
        # Subscribe AFTER load — no past events should fire
        assert len(received) == 0

    def test_new_append_after_load_notifies(self, tmp_path: Path) -> None:
        p = tmp_path / "e.jsonl"
        orig = PersistentEventLog(p)
        orig.append(_started())

        loaded = PersistentEventLog.load(p)
        received: list = []
        loaded.subscribe(received.append)
        loaded.append(_started("new-session"))
        assert len(received) == 1
        assert received[0].session_id == "new-session"

    def test_no_subscriber_calls_during_load_reconstruction(
        self, tmp_path: Path
    ) -> None:
        p = tmp_path / "e.jsonl"
        for ev in _all_kinds("s1"):
            PersistentEventLog(p).append(ev)

        call_count = [0]

        class CountingLog(PersistentEventLog):
            def _notify_subscribers(self, event: object) -> None:
                call_count[0] += 1
                super()._notify_subscribers(event)

        CountingLog.load(p)
        assert call_count[0] == 0  # load must not notify


# ---------------------------------------------------------------------------
# 6. Thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_appends_all_subscribers_notified(self) -> None:
        log = ExecutionEventLog()
        received: list = []
        lock = threading.Lock()

        def safe_append(ev: object) -> None:
            with lock:
                received.append(ev)

        log.subscribe(safe_append)

        n = 30
        barrier = threading.Barrier(n)

        def worker(i: int) -> None:
            barrier.wait()
            log.append(execution_started(f"s{i}", f"task{i}"))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(received) == n
