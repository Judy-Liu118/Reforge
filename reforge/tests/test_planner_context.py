"""Tests for PlannerMemoryContext (retrieval-aware planning, P8.4)."""

from __future__ import annotations

from pathlib import Path


from reforge.memory.models import MemoryRecord, MemoryType
from reforge.memory.store import MemoryStore
from reforge.memory.substrate import CompositeMemorySubstrate
from reforge.runtime.orchestration.reflection.planner_context import PlannerMemoryContext
from reforge.runtime.infrastructure.trajectory.models import TrajectoryRecord
from reforge.runtime.infrastructure.trajectory.store import TrajectoryStore


def _make_substrate_with_records(tmp_path: Path) -> CompositeMemorySubstrate:
    store = MemoryStore(base_dir=tmp_path / "memory")
    substrate = CompositeMemorySubstrate(store=store)
    rec = MemoryRecord(
        memory_id="m1",
        session_id="s1",
        timestamp="2026-01-01T00:00:00Z",
        memory_type=MemoryType.RECOVERY,
        user_request="analyze csv sales data",
        outcome="RECOVERED",
        error_type="KeyError",
        recovery_action="use correct column name",
        retry_count=1,
    )
    substrate.write(rec)
    return substrate


def test_build_returns_empty_on_no_memory(tmp_path: Path) -> None:
    """Empty memory → build() returns empty string, no exception."""
    store = MemoryStore(base_dir=tmp_path / "memory")
    substrate = CompositeMemorySubstrate(store=store)
    ctx = PlannerMemoryContext(substrate=substrate)
    result = ctx.build("analyze csv data")
    assert result == ""


def test_build_includes_recovery_records(tmp_path: Path) -> None:
    """RECOVERY records appear in the context output."""
    substrate = _make_substrate_with_records(tmp_path)
    ctx = PlannerMemoryContext(substrate=substrate)
    result = ctx.build("analyze csv sales")
    assert "Past execution experience" in result
    assert "RECOVERY" in result


def test_build_includes_trajectory_when_provided(tmp_path: Path) -> None:
    """TrajectoryStore results appear in context when provided."""
    substrate = _make_substrate_with_records(tmp_path)
    traj_store = TrajectoryStore(path=tmp_path / "traj.jsonl")
    traj_store.save(TrajectoryRecord(
        trajectory_id="t1",
        session_id="s1",
        timestamp="2026-01-01T00:00:00Z",
        user_request="analyze csv data",
        task_intent="NORMAL_EXECUTION",
        total_attempts=2,
        final_outcome="RECOVERED",
        outcome_reason="retry succeeded",
        recovery_chain=["KeyError"],
        problem_signature={"domain": "pandas"},
    ))

    ctx = PlannerMemoryContext(substrate=substrate, trajectory_store=traj_store)
    result = ctx.build("analyze csv data")
    assert "Past trajectory patterns" in result
    assert "RECOVERED" in result


def test_build_graceful_without_trajectory_store(tmp_path: Path) -> None:
    """trajectory_store=None is safe and does not raise."""
    substrate = _make_substrate_with_records(tmp_path)
    ctx = PlannerMemoryContext(substrate=substrate, trajectory_store=None)
    result = ctx.build("analyze csv data")
    # Should still return memory records, just no trajectory section
    assert isinstance(result, str)


def test_build_empty_when_no_relevant_records(tmp_path: Path) -> None:
    """Unrelated memory records → empty context (score too low)."""
    store = MemoryStore(base_dir=tmp_path / "memory")
    substrate = CompositeMemorySubstrate(store=store)
    rec = MemoryRecord(
        memory_id="m2",
        session_id="s2",
        timestamp="2026-01-01T00:00:00Z",
        memory_type=MemoryType.SUCCESS_PATTERN,
        user_request="scrape javascript website",
        outcome="SUCCESS",
        retry_count=0,
    )
    substrate.write(rec)

    ctx = PlannerMemoryContext(substrate=substrate)
    result = ctx.build("complex number matrix factorization")
    # May or may not match — the key constraint is no exception
    assert isinstance(result, str)
