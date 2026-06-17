"""PersistentEventLog — JSONL-backed ExecutionEventLog for durability.

Extends ExecutionEventLog with synchronous append-to-disk persistence:
  - Every append() immediately writes one JSON line to the backing file
  - load(path) reconstructs the full in-memory index from disk
  - Drop-in replacement for ExecutionEventLog in all runtime contexts

Crash recovery pattern:
    log = PersistentEventLog.load("data/events/session_abc.jsonl")
    replay = SessionReplay(log)

File format: one ExecutionEvent per line, JSON-serialised via dataclasses.asdict.
Corrupted lines are silently skipped so partial state is never lost.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path

from reforge.runtime.events.log import ExecutionEventLog
from reforge.runtime.events.models import ExecutionEvent

logger = logging.getLogger(__name__)


class PersistentEventLog(ExecutionEventLog):
    """ExecutionEventLog with JSONL persistence.

    Pass a file path; the parent directory is created automatically.
    Use PersistentEventLog.load(path) to reconstruct from an existing file.
    """

    def __init__(self, path: Path | str) -> None:
        super().__init__()
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._loading = False

    @property
    def path(self) -> Path:
        """Path to the backing JSONL file."""
        return self._path

    def append(self, event: ExecutionEvent) -> None:
        """Append event to memory, write to disk, then notify subscribers.

        Memory update and disk write are performed under the same lock so
        concurrent appends are always consistent.  Subscriber notification
        happens outside the lock (same contract as ExecutionEventLog).
        During load() reconstruction, neither disk write nor subscriber
        notification fires — only the in-memory index is rebuilt.
        """
        with self._lock:
            self._events.append(event)
            self._by_kind[event.kind].append(event)
            self._by_session[event.session_id].append(event)
            if not self._loading:
                with open(self._path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(dataclasses.asdict(event)) + "\n")
        if not self._loading:
            self._notify_subscribers(event)

    @classmethod
    def load(cls, path: Path | str) -> PersistentEventLog:
        """Reconstruct a PersistentEventLog from a JSONL file.

        Returns an empty log if the file does not exist.
        Corrupted or unparseable lines are silently skipped.
        """
        instance = cls(path)
        if not instance._path.exists():
            return instance
        instance._loading = True
        corrupted = 0
        try:
            with open(instance._path, encoding="utf-8") as fh:
                for line_num, raw in enumerate(fh, start=1):
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        instance.append(ExecutionEvent(**data))
                    except Exception:
                        corrupted += 1
                        logger.warning(
                            "Skipped corrupted event at %s:%d", instance._path, line_num
                        )
                        continue
        finally:
            instance._loading = False
        if corrupted:
            logger.warning(
                "PersistentEventLog.load(%s): %d corrupted line(s) skipped",
                instance._path,
                corrupted,
            )
        return instance
