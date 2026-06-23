"""Pytest fixtures — isolate runtime data paths so tests can't pollute D:\\Reforge\\data\\.

Several stores (MemoryStore, TrajectoryStore, HistoryStorage, ExecutionMemory,
ResearchStore, SqliteMemorySubstrate) fall back to a module-level default path
when constructed with no arguments. Tests that go through RuntimeRunner() /
reflection_node / CompositeMemorySubstrate() without an explicit substrate
trigger that fallback and write to the real ``data/`` directory.

This autouse fixture monkeypatches each default to a per-test tmp_path, so the
real ``data/`` stays clean while existing tests continue to work unchanged.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_runtime_data(monkeypatch, tmp_path):
    # 1) Belt: override the resolver. Anything that calls reforge.paths.*
    # at *call* time (e.g. handle_serve, describe_global) lands in tmp.
    global_dir = tmp_path / "global"
    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True)
    monkeypatch.setenv("REFORGE_HOME", str(global_dir))
    monkeypatch.chdir(project_dir)

    # 2) Suspenders: monkeypatch the legacy module-level constants. These
    # were resolved once at import time before the env vars were set, so
    # they still point at the old location until we rebind them.
    data_dir = tmp_path / "data"
    memory_dir = data_dir / "memory"
    memory_dir.mkdir(parents=True)

    monkeypatch.setattr("reforge.memory.store._MEMORY_DIR", memory_dir)
    monkeypatch.setattr(
        "reforge.memory.sqlite_substrate._DEFAULT_DB",
        memory_dir / "memory.db",
    )
    monkeypatch.setattr(
        "reforge.runtime.infrastructure.trajectory.store._DEFAULT_PATH",
        data_dir / "trajectories.jsonl",
    )
    monkeypatch.setattr(
        "reforge.runtime.infrastructure.trajectory.store._MULTISTEP_PATH",
        data_dir / "multistep_trajectories.jsonl",
    )
    monkeypatch.setattr(
        "reforge.runtime.infrastructure.history.storage._HISTORY_FILE",
        data_dir / "history.jsonl",
    )
    monkeypatch.setattr(
        "reforge.runtime.research.store._DEFAULT_PATH",
        data_dir / "research.jsonl",
    )
