"""Render a SqlBenchReport to Markdown."""

from __future__ import annotations

from datetime import datetime, timezone

from reforge.runtime.sql.models import SqlBenchReport


_STATUS_LABEL = {
    "correct": "OK",
    "recovered": "RECOVERED",
    "wrong": "WRONG",
    "error": "ERROR",
}


def render_markdown(report: SqlBenchReport, *, title: str | None = None) -> str:
    title = title or "Reforge SQL benchmark report"
    parts: list[str] = [f"# {title}\n", _summary(report), _per_case_table(report), _details(report)]
    return "\n".join(parts)


def _summary(report: SqlBenchReport) -> str:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    extra_attempts = report.total_attempts - report.total
    return (
        "## Summary\n\n"
        f"- Cases: **{report.total}**\n"
        f"- Execution accuracy: **{report.execution_accuracy*100:.1f}%** "
        f"(first-shot {report.first_shot_accuracy*100:.1f}% + "
        f"recovered {report.recovery_rate*100:.1f}%)\n"
        f"- Correct: {report.correct_count}  Recovered: {report.recovered_count}  "
        f"Wrong: {report.wrong_count}  Error: {report.error_count}\n"
        f"- Self-healing footprint: **{extra_attempts}** extra attempt(s) "
        f"on top of the {report.total} first-shots\n"
        f"- Wall time: **{report.total_duration_ms/1000:.1f} s**\n"
        f"- Generated at: {ts}\n"
    )


def _per_case_table(report: SqlBenchReport) -> str:
    rows = [
        "## Per case",
        "",
        "| Case | Difficulty | Status | Attempts | Eval | Duration (s) |",
        "|---|---|---|---|---|---|",
    ]
    for r in report.runs:
        rows.append(
            f"| `{r.case_id}` | {r.difficulty} | "
            f"{_STATUS_LABEL.get(r.status, r.status)} | {r.attempts} "
            f"| {r.eval_score:.2f} | {r.duration_ms/1000:.1f} |"
        )
    return "\n".join(rows) + "\n"


def _details(report: SqlBenchReport) -> str:
    parts = ["## Case details", ""]
    for r in report.runs:
        parts.append(f"### `{r.case_id}` — {_STATUS_LABEL.get(r.status, r.status)}")
        parts.append("")
        parts.append(
            f"**Difficulty:** {r.difficulty}  "
            f"**Attempts:** {r.attempts}  "
            f"**Eval:** {r.eval_score:.2f}  "
            f"**Duration:** {r.duration_ms/1000:.1f}s\n"
        )
        if r.notes:
            parts.append(f"_{r.notes}_\n")
        if r.predicted_output:
            parts.append("**Predicted:**")
            parts.append("```text")
            parts.append(r.predicted_output or "(empty)")
            parts.append("```\n")
        if r.expected_output:
            parts.append("**Expected:**")
            parts.append("```text")
            parts.append(r.expected_output or "(empty)")
            parts.append("```\n")
        if r.error:
            parts.append("**Error:**")
            parts.append("```text")
            parts.append(r.error)
            parts.append("```\n")
    return "\n".join(parts)
