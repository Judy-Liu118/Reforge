"""Replay a saved session — displays trace without re-executing."""

from __future__ import annotations

from reforge.runtime.infrastructure.history.models import SessionRecord

SEP = "-" * 60


def format_replay(record: SessionRecord) -> str:
    """Format a saved session record as a readable trace."""

    lines = [
        "=" * 60,
        f"  Session: {record.session_id}",
        f"  Time:    {record.timestamp}",
        "=" * 60,
        "",
        f"  Request: {record.user_request}",
        "",
        f"  Status:    {record.execution_status}",
        f"  Retries:   {record.retry_count}",
        f"  Duration:  {record.total_duration_ms:.0f}ms",
        "",
    ]

    if record.attempts:
        lines.append(SEP)
        lines.append("  Execution Trace")
        lines.append(f"  {'#':<4} {'Status':<10} {'Duration':<12} {'Error'}")
        lines.append(f"  {'-' * 48}")
        for a in record.attempts:
            status = "OK" if a.exit_code == 0 else "FAIL"
            dur = f"{a.duration_ms:.0f}ms"
            err = a.error_type if a.error_type else "-"
            lines.append(f"  {a.attempt:<4} {status:<10} {dur:<12} {err}")
        lines.append(SEP)

    lines.append("")
    bar = "=" * 60
    answer = record.final_answer.strip()
    if record.execution_status == "OK":
        lines.append(f"{bar}\n  Result\n{bar}\n{answer}\n{bar}")
    else:
        lines.append(f"{bar}\n  Result — FAILED\n{bar}\n{answer}\n{bar}")

    return "\n".join(lines)
