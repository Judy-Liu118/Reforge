"""P34 — PersistentEventLog JSONL persistence.

Tests cover:
  1. Basic persistence: append writes to disk
  2. Load: reconstructs events from disk (all fields, all kinds, order)
  3. Resilience: missing file, empty file, corrupted lines
  4. Multi-session roundtrip and post-load querying
  5. Drop-in compatibility with ExecutionEventLog
  6. Thread safety: concurrent appends all persisted
  7. Path management: nested dirs auto-created, str paths accepted
"""

from __future__ import annotations

import json
import threading
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
)
from reforge.runtime.events.persistent_log import PersistentEventLog


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _started(sid: str = "s1", task: str = "run code") -> object:
    return execution_started(sid, task)


def _succeeded(sid: str = "s1") -> object:
    return execution_succeeded(sid, "run code")


def _failed(sid: str = "s1") -> object:
    return execution_failed(
        sid, "run code", category="syntax", recoverable=True, error="SyntaxError"
    )


def _all_kinds(sid: str = "s1") -> list:
    return [
        execution_started(sid, "task"),
        execution_succeeded(sid, "task"),
        execution_failed(sid, "task", category="syntax", recoverable=True, error="err"),
        recovery_attempted(sid, "task", "llm_retry", 1),
        evaluation_completed(sid, score=0.8, passed=True),
        reflection_generated(sid, "root cause"),
        policy_decided(sid, "ACCEPT", "clean run"),
    ]


# ---------------------------------------------------------------------------
# 1. Basic persistence
# ---------------------------------------------------------------------------


class TestBasicPersistence:
    def test_append_creates_file(self, tmp_path: Path) -> None:
        p = tmp_path / "events.jsonl"
        log = PersistentEventLog(p)
        log.append(_started())
        assert p.exists()

    def test_append_writes_one_line_per_event(self, tmp_path: Path) -> None:
        p = tmp_path / "events.jsonl"
        log = PersistentEventLog(p)
        log.append(_started())
        log.append(_succeeded())
        log.append(_failed())
        lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(lines) == 3

    def test_each_line_is_valid_json(self, tmp_path: Path) -> None:
        p = tmp_path / "events.jsonl"
        log = PersistentEventLog(p)
        for event in _all_kinds():
            log.append(event)
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                data = json.loads(line)
                assert "kind" in data
                assert "session_id" in data

    def test_path_property(self, tmp_path: Path) -> None:
        p = tmp_path / "sub" / "events.jsonl"
        log = PersistentEventLog(p)
        assert log.path == p


# ---------------------------------------------------------------------------
# 2. Load — field preservation
# ---------------------------------------------------------------------------


class TestLoad:
    def test_load_reconstructs_event_count(self, tmp_path: Path) -> None:
        p = tmp_path / "e.jsonl"
        orig = PersistentEventLog(p)
        for ev in _all_kinds():
            orig.append(ev)

        loaded = PersistentEventLog.load(p)
        assert len(loaded) == len(orig)

    def test_load_preserves_kind(self, tmp_path: Path) -> None:
        p = tmp_path / "e.jsonl"
        orig = PersistentEventLog(p)
        orig.append(_started())
        loaded = PersistentEventLog.load(p)
        assert loaded.replay()[0].kind == "EXECUTION_STARTED"

    def test_load_preserves_session_id(self, tmp_path: Path) -> None:
        p = tmp_path / "e.jsonl"
        orig = PersistentEventLog(p)
        orig.append(execution_started("my-session", "task"))
        loaded = PersistentEventLog.load(p)
        assert loaded.replay()[0].session_id == "my-session"

    def test_load_preserves_event_id(self, tmp_path: Path) -> None:
        p = tmp_path / "e.jsonl"
        orig = PersistentEventLog(p)
        ev = _started()
        orig.append(ev)
        loaded = PersistentEventLog.load(p)
        assert loaded.replay()[0].event_id == ev.event_id

    def test_load_preserves_timestamp(self, tmp_path: Path) -> None:
        p = tmp_path / "e.jsonl"
        orig = PersistentEventLog(p)
        ev = _started()
        orig.append(ev)
        loaded = PersistentEventLog.load(p)
        assert loaded.replay()[0].timestamp == ev.timestamp

    def test_load_preserves_payload(self, tmp_path: Path) -> None:
        p = tmp_path / "e.jsonl"
        orig = PersistentEventLog(p)
        orig.append(evaluation_completed("s1", score=0.75, passed=False, reasons=["drift"]))
        loaded = PersistentEventLog.load(p)
        ev = loaded.replay()[0]
        assert ev.payload["score"] == pytest.approx(0.75)
        assert ev.payload["passed"] is False
        assert ev.payload["reasons"] == ["drift"]

    def test_load_preserves_insertion_order(self, tmp_path: Path) -> None:
        p = tmp_path / "e.jsonl"
        orig = PersistentEventLog(p)
        kinds_in = [e.kind for e in _all_kinds("s1")]
        for ev in _all_kinds("s1"):
            orig.append(ev)
        loaded = PersistentEventLog.load(p)
        assert [e.kind for e in loaded.replay()] == kinds_in

    def test_all_event_kinds_survive_roundtrip(self, tmp_path: Path) -> None:
        p = tmp_path / "e.jsonl"
        orig = PersistentEventLog(p)
        events = _all_kinds("s1")
        for ev in events:
            orig.append(ev)
        loaded = PersistentEventLog.load(p)
        orig_kinds = {e.kind for e in events}
        loaded_kinds = {e.kind for e in loaded.replay()}
        assert orig_kinds == loaded_kinds


# ---------------------------------------------------------------------------
# 3. Resilience
# ---------------------------------------------------------------------------


class TestResilience:
    def test_load_nonexistent_file_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "no_such_file.jsonl"
        log = PersistentEventLog.load(p)
        assert len(log) == 0

    def test_load_empty_file_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.jsonl"
        p.write_text("", encoding="utf-8")
        log = PersistentEventLog.load(p)
        assert len(log) == 0

    def test_load_skips_corrupted_json_lines(self, tmp_path: Path) -> None:
        p = tmp_path / "e.jsonl"
        orig = PersistentEventLog(p)
        orig.append(_started())
        # inject a bad line
        with open(p, "a", encoding="utf-8") as fh:
            fh.write("NOT_JSON\n")
        orig.append(_succeeded())

        loaded = PersistentEventLog.load(p)
        assert len(loaded) == 2  # bad line skipped

    def test_load_skips_invalid_event_fields(self, tmp_path: Path) -> None:
        p = tmp_path / "e.jsonl"
        orig = PersistentEventLog(p)
        orig.append(_started())
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"bad_field": "no kind"}) + "\n")
        orig.append(_succeeded())

        loaded = PersistentEventLog.load(p)
        assert len(loaded) == 2

    def test_load_does_not_append_to_file(self, tmp_path: Path) -> None:
        """load() must not double-write events that already exist on disk."""
        p = tmp_path / "e.jsonl"
        orig = PersistentEventLog(p)
        orig.append(_started())
        orig.append(_succeeded())
        line_count_before = len(p.read_text(encoding="utf-8").splitlines())

        PersistentEventLog.load(p)

        line_count_after = len(p.read_text(encoding="utf-8").splitlines())
        assert line_count_after == line_count_before


# ---------------------------------------------------------------------------
# 4. Multi-session roundtrip and post-load querying
# ---------------------------------------------------------------------------


class TestMultiSession:
    def test_multi_session_roundtrip(self, tmp_path: Path) -> None:
        p = tmp_path / "e.jsonl"
        orig = PersistentEventLog(p)
        orig.append(execution_started("alice", "task a"))
        orig.append(execution_started("bob", "task b"))
        orig.append(execution_succeeded("alice", "task a"))

        loaded = PersistentEventLog.load(p)
        assert len(loaded.query(session_id="alice")) == 2
        assert len(loaded.query(session_id="bob")) == 1

    def test_query_by_kind_after_load(self, tmp_path: Path) -> None:
        p = tmp_path / "e.jsonl"
        orig = PersistentEventLog(p)
        for ev in _all_kinds("s1"):
            orig.append(ev)

        loaded = PersistentEventLog.load(p)
        assert len(loaded.query(kind="EXECUTION_STARTED")) == 1
        assert len(loaded.query(kind="POLICY_DECIDED")) == 1

    def test_sessions_after_load(self, tmp_path: Path) -> None:
        p = tmp_path / "e.jsonl"
        orig = PersistentEventLog(p)
        orig.append(execution_started("alice", "t"))
        orig.append(execution_started("bob", "t"))

        loaded = PersistentEventLog.load(p)
        assert loaded.sessions() == {"alice", "bob"}


# ---------------------------------------------------------------------------
# 5. Drop-in compatibility
# ---------------------------------------------------------------------------


class TestDropInCompatibility:
    def test_is_subclass_of_execution_event_log(self, tmp_path: Path) -> None:
        log = PersistentEventLog(tmp_path / "e.jsonl")
        assert isinstance(log, ExecutionEventLog)

    def test_len_after_load(self, tmp_path: Path) -> None:
        p = tmp_path / "e.jsonl"
        orig = PersistentEventLog(p)
        orig.append(_started())
        orig.append(_succeeded())
        loaded = PersistentEventLog.load(p)
        assert len(loaded) == 2

    def test_replay_after_load_matches_original(self, tmp_path: Path) -> None:
        p = tmp_path / "e.jsonl"
        orig = PersistentEventLog(p)
        events = _all_kinds("s1")
        for ev in events:
            orig.append(ev)
        loaded = PersistentEventLog.load(p)
        assert [e.event_id for e in loaded.replay()] == [e.event_id for e in orig.replay()]


# ---------------------------------------------------------------------------
# 6. Thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_appends_all_persisted(self, tmp_path: Path) -> None:
        p = tmp_path / "concurrent.jsonl"
        log = PersistentEventLog(p)
        n = 50
        barrier = threading.Barrier(n)

        def worker(i: int) -> None:
            barrier.wait()
            log.append(execution_started(f"session-{i}", f"task-{i}"))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(log) == n
        lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(lines) == n


# ---------------------------------------------------------------------------
# 7. Path management
# ---------------------------------------------------------------------------


class TestPathManagement:
    def test_nested_parent_dirs_created(self, tmp_path: Path) -> None:
        p = tmp_path / "a" / "b" / "c" / "events.jsonl"
        log = PersistentEventLog(p)
        log.append(_started())
        assert p.exists()

    def test_str_path_accepted(self, tmp_path: Path) -> None:
        p = str(tmp_path / "str_path.jsonl")
        log = PersistentEventLog(p)
        log.append(_started())
        assert Path(p).exists()
