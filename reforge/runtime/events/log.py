"""ExecutionEventLog — thread-safe, append-only log with query, replay, and subscribers.

The log is the canonical in-process store for ExecutionEvents within a
running runtime session.  It supports:

  - append      : add a new immutable event
  - query       : filter by kind and/or session_id (AND semantics)
  - replay      : full ordered snapshot for projection / analysis
  - sessions    : set of all session_ids seen
  - subscribe   : register a callback fired on every new event
  - unsubscribe : cancel a prior subscription via SubscriptionHandle

Thread safety: all mutations and reads are protected by a single lock.
Subscriber callbacks are invoked outside the lock to prevent deadlock.
Exceptions raised by callbacks are swallowed so subscribers never crash
the runtime.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Callable

from reforge.runtime.events.models import EventKind, ExecutionEvent

logger = logging.getLogger(__name__)

SubscriberFn = Callable[[ExecutionEvent], None]


# ---------------------------------------------------------------------------
# Subscription handle
# ---------------------------------------------------------------------------


class SubscriptionHandle:
    """Opaque handle returned by ExecutionEventLog.subscribe().

    Call cancel() to stop receiving notifications.  Cancelling an already-
    cancelled handle is a no-op.
    """

    __slots__ = ("_log", "_sub_id")

    def __init__(self, log: ExecutionEventLog, sub_id: int) -> None:
        self._log = log
        self._sub_id = sub_id

    def cancel(self) -> None:
        """Remove this subscription from the log."""
        self._log.unsubscribe(self)


# ---------------------------------------------------------------------------
# Log
# ---------------------------------------------------------------------------


class ExecutionEventLog:
    """Thread-safe append-only log of ExecutionEvents with pub/sub support."""

    def __init__(self) -> None:
        self._events: list[ExecutionEvent] = []
        self._by_kind: dict[str, list[ExecutionEvent]] = defaultdict(list)
        self._by_session: dict[str, list[ExecutionEvent]] = defaultdict(list)
        self._lock = threading.Lock()
        self._subscribers: dict[int, SubscriberFn] = {}
        self._next_sub_id: int = 0

    # ------------------------------------------------------------------
    # Write

    def append(self, event: ExecutionEvent) -> None:
        """Append *event* to the log and notify subscribers.  O(1) amortised."""
        with self._lock:
            self._events.append(event)
            self._by_kind[event.kind].append(event)
            self._by_session[event.session_id].append(event)
        self._notify_subscribers(event)

    # ------------------------------------------------------------------
    # Read

    def query(
        self,
        *,
        kind: EventKind | None = None,
        session_id: str | None = None,
    ) -> list[ExecutionEvent]:
        """Return events matching ALL provided filters (AND semantics).

        No filters → all events.  Returns a snapshot copy.
        """
        with self._lock:
            if kind and session_id:
                return [
                    e
                    for e in self._by_kind.get(kind, [])
                    if e.session_id == session_id
                ]
            if kind:
                return list(self._by_kind.get(kind, []))
            if session_id:
                return list(self._by_session.get(session_id, []))
            return list(self._events)

    def replay(self) -> list[ExecutionEvent]:
        """Return all events in insertion order (snapshot copy)."""
        with self._lock:
            return list(self._events)

    def sessions(self) -> set[str]:
        """Return the set of all session_ids that have emitted events."""
        with self._lock:
            return set(self._by_session)

    # ------------------------------------------------------------------
    # Subscribers

    def subscribe(self, fn: SubscriberFn) -> SubscriptionHandle:
        """Register *fn* to be called for every future append.

        Returns a SubscriptionHandle; call handle.cancel() to unsubscribe.
        Past events are not replayed to new subscribers.
        """
        with self._lock:
            sub_id = self._next_sub_id
            self._next_sub_id += 1
            self._subscribers[sub_id] = fn
        return SubscriptionHandle(self, sub_id)

    def unsubscribe(self, handle: SubscriptionHandle) -> None:
        """Remove the subscription identified by *handle*.  Safe to call twice."""
        with self._lock:
            self._subscribers.pop(handle._sub_id, None)

    def _notify_subscribers(self, event: ExecutionEvent) -> None:
        """Call all registered subscriber functions with *event*.

        Executed outside the main lock.  Any exception raised by a subscriber
        is logged and isolated so it never propagates to the caller of append().
        """
        with self._lock:
            fns = list(self._subscribers.values())
        for fn in fns:
            try:
                fn(event)
            except Exception:
                logger.exception(
                    "Event subscriber %r raised on %s/%s",
                    fn,
                    event.kind,
                    event.session_id,
                )

    # ------------------------------------------------------------------
    # Dunder

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)

    def __repr__(self) -> str:  # pragma: no cover
        return f"ExecutionEventLog(events={len(self)})"
