"""CLI history/replay/memory/trace command handlers."""

from __future__ import annotations

import sys

from reforge.memory.retrieval import MemoryRetriever, format_memory_results
from reforge.observability.tracing.renderer import render_timeline
from reforge.runtime.infrastructure.history.replay import format_replay
from reforge.runtime.infrastructure.history.storage import HistoryStorage


def _eval_trend(attempts: list) -> str:
    """Format eval score trend across attempts, e.g. '0.40→0.80→1.00'."""
    scores = [a.eval_score for a in attempts if hasattr(a, "eval_score")]
    if not scores:
        return "-"
    if len(scores) == 1:
        return f"{scores[0]:.2f}"
    return "→".join(f"{s:.2f}" for s in scores)


def handle_history() -> None:
    storage = HistoryStorage()
    records = storage.list_all()
    if not records:
        print("No execution history yet.")
        return
    print(f"{'Session':<10} {'Status':<6} {'Retry':<6} {'Eval Trend':<18} {'Duration':<10} Request")
    print("-" * 84)
    for r in records:
        dur = f"{r.total_duration_ms:.0f}ms"
        req = r.user_request[:50].replace("\n", " ")
        trend = _eval_trend(r.attempts)
        print(f"  {r.session_id:<8}  {r.execution_status:<4}  {r.retry_count:<6} {trend:<18} {dur:<10} {req}")


def handle_replay(session_id: str) -> None:
    storage = HistoryStorage()
    record = storage.find(session_id)
    if record is None:
        print(f"Session not found: {session_id}")
        print("Use --history to see available sessions.")
        sys.exit(1)
    print(format_replay(record))


def handle_memory(query: str) -> None:
    retriever = MemoryRetriever()
    results = retriever.search(query)
    print(format_memory_results(results))


def handle_trace(session_id: str) -> None:
    output = render_timeline(session_id)
    if output is None:
        print(f"Trace not found: {session_id}")
        print("Use --history to see available sessions.")
    else:
        print(output)
