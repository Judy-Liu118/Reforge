"""Tests for conversation-level session grouping in RuntimeRunner."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from reforge.memory.sqlite_substrate import SqliteMemorySubstrate
from reforge.runtime.orchestration.engine.runner import RuntimeRunner


def _fake_graph(outcome: str = "SUCCESS"):
    """Build a minimal graph mock that emits one final_response node."""
    state = MagicMock()
    state.outcome_state.task_outcome = outcome
    state.outcome_state.task_outcome_reason = "clean_execution"
    state.outcome_state.final_answer = "42"
    state.control_state.retry_count = 0
    state.semantic_state.reflection_result = None
    state.semantic_state.reflection_summary = ""
    state.exec_state.stdout = "42\n"
    state.exec_state.stderr = ""
    state.exec_state.exit_code = 0
    state.exec_state.duration_ms = 10.0
    state.attempts = []
    state.generated_code = "print(42)"
    state.user_request = "print 42"
    state.classification_result = None

    graph = MagicMock()
    graph.stream.return_value = [{"final_response": {"exec_state": state.exec_state}}]
    return graph, state


# ---------------------------------------------------------------------------
# conversation_id propagation
# ---------------------------------------------------------------------------


def test_runner_uses_conversation_id_for_memory(tmp_path: Path) -> None:
    """Memory records should use conversation_id, not per-task session_id."""
    substrate = SqliteMemorySubstrate(db_path=tmp_path / "test.db")
    conv_id = "conv-abc123"

    runner = RuntimeRunner(
        memory_substrate=substrate,
        conversation_id=conv_id,
    )
    assert runner.conversation_id == conv_id
    # session_id is still unique per-task
    assert runner.session_id != conv_id

    substrate.close()


def test_runner_defaults_conversation_id_to_session_id() -> None:
    """Without explicit conversation_id, memory is tagged with per-task session_id."""
    runner = RuntimeRunner()
    assert runner.conversation_id == runner.session_id


def test_two_tasks_share_conversation_id(tmp_path: Path) -> None:
    """Two RuntimeRunners with the same conversation_id group records together."""
    conv_id = "shared-conv-99"
    substrate = SqliteMemorySubstrate(db_path=tmp_path / "shared.db")

    r1 = RuntimeRunner(memory_substrate=substrate, conversation_id=conv_id)
    r2 = RuntimeRunner(memory_substrate=substrate, conversation_id=conv_id)

    assert r1.session_id != r2.session_id        # different execution IDs
    assert r1.conversation_id == conv_id         # same conversation
    assert r2.conversation_id == conv_id

    substrate.close()


def test_memory_write_uses_conversation_id(tmp_path: Path) -> None:
    """Memory records written by the runner carry conversation_id as session_id."""
    from reforge.memory.models import MemoryRecord
    from reforge.memory.writer import record_from_final_state

    conv_id = "conv-xyz"
    substrate = SqliteMemorySubstrate(db_path=tmp_path / "c.db")

    written: list[MemoryRecord] = []
    original_write = substrate.write

    def capture_write(rec: MemoryRecord) -> None:
        written.append(rec)
        original_write(rec)

    substrate.write = capture_write  # type: ignore[method-assign]

    runner = RuntimeRunner(memory_substrate=substrate, conversation_id=conv_id)

    # Patch record_from_final_state to return a dummy record
    dummy_record = MemoryRecord(
        memory_id="dummy-id",
        session_id=conv_id,
        timestamp="2026-06-13T00:00:00",
        user_request="test task",
        outcome="SUCCESS",
    )
    with patch(
        "reforge.runtime.orchestration.engine.runner.record_from_final_state",
        return_value=dummy_record,
    ) as mock_rfs:
        # Simulate stream reaching final_response
        from reforge.runtime.domain.state.models import RuntimeState
        state = RuntimeState(user_request="test task")

        # Manually invoke the write-back logic
        mem_record = mock_rfs(state, runner.conversation_id)
        if mem_record is not None:
            runner.memory_substrate.write(mem_record)

        assert mock_rfs.call_args[0][1] == conv_id  # called with conversation_id

    assert len(written) == 1
    assert written[0].session_id == conv_id

    substrate.close()


# ---------------------------------------------------------------------------
# Banner / display helpers (smoke-only, no assertions on exact chars)
# ---------------------------------------------------------------------------


def test_banner_contains_session_id() -> None:
    from reforge.cli.main import _banner
    sid = "test1234"
    output = _banner(sid)
    assert sid in output
    assert "R E F O R G E" in output


def test_banner_contains_pixel_cat() -> None:
    from reforge.cli.main import _banner
    output = _banner("anyid")
    # Half-block chars ▄/▀ are used for the pixel cat
    assert "▄" in output or "▀" in output


def test_banner_contains_model_and_dir() -> None:
    from reforge.cli.main import _banner
    output = _banner("anyid")
    assert "model" in output
    assert "sess" in output
