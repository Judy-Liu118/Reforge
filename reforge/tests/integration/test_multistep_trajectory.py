"""Integration tests for multi-step trajectory aggregation."""

from __future__ import annotations

from pathlib import Path


from reforge.runtime.infrastructure.trajectory.models import MultiStepTrajectory
from reforge.runtime.infrastructure.trajectory.store import TrajectoryStore


def test_save_and_list_multistep_trajectory(tmp_path: Path) -> None:
    """save_multistep writes a record that list_multistep can read back."""
    store = TrajectoryStore(path=tmp_path / "traj.jsonl")
    record = store.save_multistep(
        original_request="Step 1: load CSV. Step 2: analyze data.",
        subtask_session_ids=["sess-a", "sess-b"],
        subtask_outcomes=["SUCCESS", "RECOVERED"],
        subtask_descriptions=["Load data", "Analyze"],
        overall_outcome="COMPLETE",
        total_attempts=3,
    )

    assert isinstance(record, MultiStepTrajectory)
    assert record.overall_outcome == "COMPLETE"
    assert len(record.subtask_session_ids) == 2

    records = store.list_multistep()
    assert len(records) == 1
    assert records[0].original_request == "Step 1: load CSV. Step 2: analyze data."


def test_multistep_trajectory_persists_across_instances(tmp_path: Path) -> None:
    """Data written by one TrajectoryStore is readable by a new instance at the same path."""
    traj_path = tmp_path / "traj.jsonl"
    store1 = TrajectoryStore(path=traj_path)
    store1.save_multistep(
        original_request="multi request",
        subtask_session_ids=["s1"],
        subtask_outcomes=["SUCCESS"],
        subtask_descriptions=["step 1"],
        overall_outcome="COMPLETE",
    )

    store2 = TrajectoryStore(path=traj_path)
    records = store2.list_multistep()
    assert len(records) == 1
    assert records[0].original_request == "multi request"


def test_multistep_trajectory_empty_on_no_data(tmp_path: Path) -> None:
    store = TrajectoryStore(path=tmp_path / "nonexistent_traj.jsonl")
    assert store.list_multistep() == []


def test_multistep_path_isolated_from_main_path(tmp_path: Path) -> None:
    """Custom store paths should not share multistep storage with default store."""
    custom_store = TrajectoryStore(path=tmp_path / "custom.jsonl")
    custom_store.save_multistep(
        original_request="only in custom",
        subtask_session_ids=["s1"],
        subtask_outcomes=["SUCCESS"],
        subtask_descriptions=["step 1"],
        overall_outcome="COMPLETE",
    )

    # A second custom store at a different path should have empty multistep
    other_store = TrajectoryStore(path=tmp_path / "other.jsonl")
    assert other_store.list_multistep() == []
