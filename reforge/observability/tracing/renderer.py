"""Render a trace.json into a readable execution timeline."""

from __future__ import annotations

from reforge.observability.tracing.storage import load_trace


def render_timeline(session_id: str) -> str | None:
    """Load and render a trace as a readable timeline. Returns None if not found."""
    data = load_trace(session_id)
    if data is None:
        return None

    lines = [
        "=" * 64,
        f"  Session: {data['session_id']}",
        f"  Outcome: {data['outcome']}",
        f"  Events:  {data['total_events']}",
        "=" * 64,
        "",
    ]

    attempt = 0
    for e in data["events"]:
        etype = e["event_type"]
        att = e.get("attempt", 0)
        dur = e.get("duration_ms", 0)
        status = e.get("status", "")
        out = e.get("output_summary", "")

        # Show attempt separator
        if att != attempt:
            attempt = att
            if any(x in etype for x in ("PLAN_STARTED", "CODEGEN_STARTED", "EXECUTION_STARTED")):
                lines.append(f"  --- Attempt {att} ---")

        icon = _icon_for(etype)
        dur_str = f"({dur:.0f}ms)" if dur else ""
        status_str = f"[{status}]" if status else ""
        out_str = f": {out[:70]}" if out else ""

        line = f"  {icon} {etype:<25} {dur_str:<10} {status_str:<12} {out_str}"
        lines.append(line.rstrip())

        # Add visual separator before retry
        if etype == "RETRY_TRIGGERED":
            lines.append("  " + "·" * 58)

    lines.append("")
    lines.append("=" * 64)
    return "\n".join(lines)


def _icon_for(event_type: str) -> str:
    icons = {
        "PLAN_STARTED": "▶",
        "PLAN_COMPLETED": "✅",
        "CODEGEN_STARTED": "▶",
        "CODEGEN_COMPLETED": "✅",
        "EXECUTION_STARTED": "▶",
        "EXECUTION_COMPLETED": "⏰",
        "REFLECTION_STARTED": "▶",
        "REFLECTION_COMPLETED": "🔍",
        "EVALUATION_STARTED": "▶",
        "EVALUATION_COMPLETED": "📊",
        "RETRY_TRIGGERED": "♻",
        "TASK_COMPLETED": "🏁",
    }
    return icons.get(event_type, "  ")
