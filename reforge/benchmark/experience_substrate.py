"""Substrate factories for the Experience Memory Benchmark.

Two flavours, both backed by `CompositeMemorySubstrate(MemoryStore(base_dir=...))`
so the global `~/.reforge/memory/` store is never touched:

  FreshSubstrateFactory  — each call returns a brand-new substrate rooted in
                           a unique subdirectory. Used for Cold-leg cases so
                           Round A never contaminates Round A'.
  StickySubstrateFactory — every call returns the same underlying substrate
                           instance. Used for the Warm leg so Round A's
                           lessons are visible to Round A'.

The BenchmarkRunner wraps every returned substrate in a `_CountingSubstrate`
to track per-case recall hits, so sharing the underlying store is fine —
counters stay scoped to each case run.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from reforge.memory.store import MemoryStore
from reforge.memory.substrate import CompositeMemorySubstrate, MemorySubstrate


def _make_composite(path: Path) -> CompositeMemorySubstrate:
    path.mkdir(parents=True, exist_ok=True)
    return CompositeMemorySubstrate(MemoryStore(base_dir=path))


class FreshSubstrateFactory:
    """Each `__call__` returns a brand-new substrate in a unique subdir."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._counter = 0

    def __call__(self) -> MemorySubstrate:
        self._counter += 1
        return _make_composite(self._root / f"fresh_{self._counter:03d}")


class StickySubstrateFactory:
    """All calls return the same underlying substrate (Warm leg)."""

    def __init__(self, root: Path) -> None:
        self._substrate = _make_composite(root)

    def __call__(self) -> MemorySubstrate:
        return self._substrate


class ExperienceTmpRoot:
    """Context-managed tmp dir for one benchmark invocation.

    Keeps Cold and Warm dirs side-by-side under one root so the user can
    inspect them post-run if needed. `keep=True` skips cleanup for forensics.
    """

    def __init__(self, *, keep: bool = False, prefix: str = "reforge_exp_") -> None:
        self._keep = keep
        self._prefix = prefix
        self.path: Path | None = None

    def __enter__(self) -> Path:
        self.path = Path(tempfile.mkdtemp(prefix=self._prefix))
        return self.path

    def __exit__(self, *exc_info) -> None:
        if self.path and not self._keep and self.path.exists():
            shutil.rmtree(self.path, ignore_errors=True)
