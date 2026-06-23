"""Tests for P14.4 — ResearchSession passes trajectory_store to RuntimeRunner."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from reforge.runtime.research.session import ResearchSession
from reforge.runtime.infrastructure.trajectory.store import TrajectoryStore


def test_research_session_default_runner_receives_trajectory_store(
    tmp_path: Path,
) -> None:
    """When trajectory_store is passed, RuntimeRunner must receive it."""
    traj_store = TrajectoryStore(path=tmp_path / "t.jsonl")

    with patch("reforge.runtime.research.session.RuntimeRunner") as mock_runner_cls:
        mock_runner_cls.return_value = MagicMock()
        ResearchSession(trajectory_store=traj_store)
        mock_runner_cls.assert_called_once_with(trajectory_store=traj_store)


def test_research_session_custom_runner_not_overridden() -> None:
    """When a custom runner is provided, the default RunnerVerifier wraps it
    instead of constructing its own RuntimeRunner."""
    custom_runner = MagicMock()

    with patch("reforge.runtime.research.session.RuntimeRunner") as mock_runner_cls:
        session = ResearchSession(runner=custom_runner)
        mock_runner_cls.assert_not_called()
        assert session._verifier._runner is custom_runner


def test_research_session_no_trajectory_store_creates_runner_without_it() -> None:
    """When trajectory_store is None, RuntimeRunner is created without it."""
    with patch("reforge.runtime.research.session.RuntimeRunner") as mock_runner_cls:
        mock_runner_cls.return_value = MagicMock()
        ResearchSession()
        mock_runner_cls.assert_called_once_with(trajectory_store=None)
