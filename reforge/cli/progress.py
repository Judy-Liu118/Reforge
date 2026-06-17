"""P37 — Live progress display during CLI task execution.

Attaches a subscriber to an ExecutionEventLog and prints one-line
event notifications to the terminal as execution proceeds, giving
real-time semantic feedback alongside the existing node-level trace.

Usage:
    printer = ProgressPrinter(event_log)
    # ... run the task ...
    printer.stop()

The output file defaults to sys.stdout but can be overridden for
testing without patching:
    buf = io.StringIO()
    printer = ProgressPrinter(log, file=buf)
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, TextIO

from reforge.runtime.events.models import ExecutionEvent

if TYPE_CHECKING:
    from reforge.runtime.events.log import ExecutionEventLog, SubscriptionHandle

# Symbols used in live output lines
_SYM: dict[str, str] = {
    "EXECUTION_STARTED": "→",
    "EXECUTION_SUCCEEDED": "✓",
    "EXECUTION_FAILED": "✗",
    "RECOVERY_ATTEMPTED": "~",
    "EVALUATION_COMPLETED": "=",
    "REFLECTION_GENERATED": "*",
    "POLICY_DECIDED": "■",
    "TASK_COMPLETED": "◆",
}


def format_live_event(event: ExecutionEvent) -> str | None:
    """Return a single display line for *event*, or None to suppress.

    The output is intentionally terse — one line per lifecycle fact.
    Callers should not rely on the exact format; use the payload directly
    for programmatic processing.
    """
    sym = _SYM.get(event.kind)
    if sym is None:
        return None
    p = event.payload

    if event.kind == "EXECUTION_STARTED":
        task = (p.get("task") or "")[:60]
        suffix = f": {task}" if task else ""
        return f"  [{sym}] Started{suffix}"

    if event.kind == "EXECUTION_SUCCEEDED":
        summary = (p.get("output_summary") or "")[:60]
        suffix = f"  {summary}" if summary else ""
        return f"  [{sym}] Execution succeeded{suffix}"

    if event.kind == "EXECUTION_FAILED":
        cat = p.get("category", "")
        err = (p.get("error") or "")[:60]
        suffix = f": {err}" if err else ""
        return f"  [{sym}] Failed ({cat}){suffix}"

    if event.kind == "RECOVERY_ATTEMPTED":
        strategy = p.get("strategy", "")
        attempt = p.get("attempt", "")
        return f"  [{sym}] Recovery → {strategy} (attempt #{attempt})"

    if event.kind == "EVALUATION_COMPLETED":
        score = p.get("score")
        passed = p.get("passed", False)
        status = "pass" if passed else "fail"
        score_str = f"{score:.2f}" if isinstance(score, float) else str(score)
        return f"  [{sym}] Eval: {score_str} ({status})"

    if event.kind == "REFLECTION_GENERATED":
        summary = (p.get("summary") or "")[:60]
        suffix = f": {summary}" if summary else ""
        return f"  [{sym}] Reflection{suffix}"

    if event.kind == "POLICY_DECIDED":
        decision = p.get("decision", "")
        reason = (p.get("reason") or "")[:60]
        suffix = f" — {reason}" if reason else ""
        return f"  [{sym}] Policy: {decision}{suffix}"

    if event.kind == "TASK_COMPLETED":
        outcome = p.get("outcome", "")
        reason = (p.get("reason") or "")[:60]
        suffix = f" — {reason}" if reason else ""
        return f"  [{sym}] Task: {outcome}{suffix}"

    return None  # pragma: no cover


class ProgressPrinter:
    """Prints live event lines to *file* (default: sys.stdout).

    Subscribes on construction; call stop() when execution is complete
    to remove the subscription and avoid leaking the callback.
    """

    def __init__(
        self,
        log: ExecutionEventLog,
        file: TextIO | None = None,
    ) -> None:
        self._file = file if file is not None else sys.stdout
        self._handle: SubscriptionHandle = log.subscribe(self._on_event)

    def _on_event(self, event: ExecutionEvent) -> None:
        line = format_live_event(event)
        if line:
            print(line, file=self._file, flush=True)

    def stop(self) -> None:
        """Cancel the subscription.  Safe to call multiple times."""
        self._handle.cancel()
