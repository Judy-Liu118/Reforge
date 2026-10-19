"""Loop-level event log — the online loop's single source of truth.

Separate from runtime ExecutionEvent on purpose: those are facts about one
graph run (attempts, policy decisions); these are facts about the loop
(task picked, verdict, card admitted/evicted, canary result). Same storage
discipline as PersistentEventLog: synchronous JSONL append, corrupt lines
skipped on load.

Commit semantics. Every event a task produces carries that task's `try_id`;
the try's last event is TASK_COMMITTED. Readers (CardBank, metrics) only see
events of committed tries — so a crash mid-task leaves no half-admitted card
behind, and the resumed run simply reruns the task under a fresh try_id.
Events with an empty try_id (manual promote/evict, canary results, metric
snapshots) stand on their own.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

KINDS = frozenset({
    "LOOP_STARTED",
    "TASK_STARTED", "ATTEMPT_FINISHED", "VERDICT", "REVIEW_ENQUEUED", "TASK_COMMITTED",
    "CARD_RECALLED", "CARD_ADMITTED", "CARD_REJECTED", "CARD_OBSERVED", "CARD_PROMOTED", "CARD_EVICTED",
    "CANARY_RESULT", "METRICS",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class LoopEvent:
    kind: str
    task_id: str = ""
    try_id: str = ""
    payload: dict = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    timestamp: str = field(default_factory=_now)


class LoopEventLog:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._events: list[LoopEvent] = []
        self._committed: dict[str, str] = {}          # try_id → task_id
        if self.path.exists():
            bad = 0
            for raw in self.path.read_text(encoding="utf-8").splitlines():
                if not raw.strip():
                    continue
                try:
                    self._index(LoopEvent(**json.loads(raw)))
                except Exception:  # noqa: BLE001 — a torn last line after a kill
                    bad += 1
            if bad:
                logger.warning("LoopEventLog(%s): %d corrupted line(s) skipped", self.path, bad)

    def _index(self, e: LoopEvent) -> None:
        self._events.append(e)
        if e.kind == "TASK_COMMITTED":
            self._committed[e.try_id] = e.task_id

    def append(self, kind: str, *, task_id: str = "", try_id: str = "", **payload) -> LoopEvent:
        if kind not in KINDS:
            raise ValueError(f"unknown loop event kind {kind!r}")
        e = LoopEvent(kind=kind, task_id=task_id, try_id=try_id, payload=payload)
        line = json.dumps(asdict(e), ensure_ascii=False)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._index(e)
        return e

    # -- reads ---------------------------------------------------------------

    def is_visible(self, e: LoopEvent, pending_try: str | None = None) -> bool:
        return not e.try_id or e.try_id in self._committed or e.try_id == pending_try

    def events(self, kind: str | None = None, *, pending_try: str | None = None,
               committed_only: bool = True) -> list[LoopEvent]:
        return [e for e in self._events
                if (kind is None or e.kind == kind)
                and (not committed_only or self.is_visible(e, pending_try))]

    def committed_tasks(self) -> dict[str, str]:
        """task_id → the try_id that committed it."""
        return {t: tr for tr, t in self._committed.items()}

    def abandoned_tries(self) -> list[str]:
        started = [e.try_id for e in self._events if e.kind == "TASK_STARTED"]
        return [t for t in started if t not in self._committed]

    def __len__(self) -> int:
        return len(self._events)
