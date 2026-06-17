"""CLI handlers for memory substrate observability.

Commands:
    --memory-list [TYPE]   list all records, optionally filtered by type
    --memory-show <id>     show full detail for a single record
    --memory-stats         aggregate statistics
"""

from __future__ import annotations

import sys

from reforge.memory.models import MemoryRecord
from reforge.memory.sqlite_substrate import SqliteMemorySubstrate
from reforge.paths import describe_global

_TYPE_ALIASES = {
    "recovery": "RECOVERY",
    "success": "SUCCESS_PATTERN",
    "pattern": "SUCCESS_PATTERN",
    "success_pattern": "SUCCESS_PATTERN",
    "failure": "FAILURE",
}

_SEP = "─" * 80


def _normalize_type(raw: str) -> str:
    return _TYPE_ALIASES.get(raw.lower(), raw.upper())


def _fmt_request(r: str, width: int = 44) -> str:
    r = r.replace("\n", " ")
    return r[:width] + "…" if len(r) > width else r


def _fmt_type(t: object) -> str:
    s = t if isinstance(t, str) else t.value  # type: ignore[union-attr]
    return s.replace("_PATTERN", "")  # SUCCESS_PATTERN → SUCCESS


def handle_memory_list(type_filter: str | None = None) -> None:
    substrate = SqliteMemorySubstrate()
    try:
        mem_type = _normalize_type(type_filter) if type_filter else None
        records = substrate.list_all(mem_type)
    finally:
        substrate.close()

    print(f"  Memory store: {describe_global()}")

    if not records:
        label = f" [{mem_type}]" if mem_type else ""
        print(f"  No memory records{label}.")
        return

    print()
    print(f"  {'ID':<10} {'TYPE':<10} {'ERROR':<22} {'OUTCOME':<10} {'RT':<4} Request")
    print(f"  {_SEP}")
    for rec in records:
        mid = rec.memory_id[:8]
        mtype = _fmt_type(rec.memory_type)
        err = (rec.error_type or "-")[:20]
        outcome = (rec.outcome or "-")[:9]
        rt = str(rec.retry_count)
        req = _fmt_request(rec.user_request)
        print(f"  {mid:<10} {mtype:<10} {err:<22} {outcome:<10} {rt:<4} {req}")

    print(f"\n  {len(records)} record(s)")


def handle_memory_show(memory_id: str) -> None:
    substrate = SqliteMemorySubstrate()
    try:
        # Support prefix lookup: match first record whose id starts with memory_id.
        if len(memory_id) < 32:
            all_records = substrate.list_all()
            rec: MemoryRecord | None = next(
                (r for r in all_records if r.memory_id.startswith(memory_id)), None
            )
        else:
            rec = substrate.find(memory_id)
    finally:
        substrate.close()

    if rec is None:
        print(f"Memory record not found: {memory_id}")
        print("Use --memory-list to see available records.")
        sys.exit(1)

    mtype = rec.memory_type.value
    print(f"\n  Memory Record: {rec.memory_id}")
    print(f"  {_SEP}")
    print(f"  {'Session':<20} {rec.session_id}")
    print(f"  {'Type':<20} {mtype}")
    print(f"  {'Timestamp':<20} {rec.timestamp}")
    print(f"  {'Outcome':<20} {rec.outcome or '-'}")
    print(f"  {'Retry Count':<20} {rec.retry_count}")
    print(f"  {'Error Type':<20} {rec.error_type or '-'}")
    print(f"  {'Request':<20} {rec.user_request}")
    if rec.reflection_summary:
        print(f"  {'Reflection':<20} {rec.reflection_summary[:120]}")
    if rec.recovery_action:
        print(f"  {'Recovery Action':<20} {rec.recovery_action[:120]}")
    if rec.tags:
        print(f"  {'Tags':<20} {', '.join(rec.tags)}")
    if rec.problem_signature:
        items = ", ".join(f"{k}={v}" for k, v in rec.problem_signature.items())
        print(f"  {'Signature':<20} {items}")
    print()


def handle_memory_stats() -> None:
    substrate = SqliteMemorySubstrate()
    try:
        data = substrate.stats()
        recent = substrate.list_all()[:5]
    finally:
        substrate.close()

    total: int = data["total"]  # type: ignore[assignment]
    by_type: dict[str, int] = data["by_type"]  # type: ignore[assignment]
    top_errors: list[tuple[str, int]] = data["top_errors"]  # type: ignore[assignment]

    print(f"\n  Memory Statistics")
    print(f"  Source: {describe_global()}")
    print(f"  {_SEP}")
    print(f"  {'Total Records':<22} {total}")
    print()
    print(f"  By Type:")
    if by_type:
        for t, cnt in by_type.items():
            print(f"    {t:<24} {cnt}")
    else:
        print("    (none)")

    if top_errors:
        print()
        print(f"  Top Error Types:")
        for err, cnt in top_errors:
            print(f"    {err:<30} {cnt}")

    if recent:
        print()
        print(f"  Recent Records:")
        for rec in recent:
            mtype = _fmt_type(rec.memory_type)
            ts = rec.timestamp[:19] if rec.timestamp else "-"
            req = _fmt_request(rec.user_request, width=40)
            print(f"    {ts}  {mtype:<10}  {req}")
    print()
