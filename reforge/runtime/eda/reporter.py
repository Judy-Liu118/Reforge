"""Render EdaReport to a self-contained Markdown document.

Layout choices:
  - one top-level summary table so reviewers see the headline metrics
  - one section per stage, with status pill + raw model output in a fenced
    block; reviewer can grep for FAIL to find broken stages instantly
  - footer lists how many retries the runtime burned — that's the
    self-healing story the report is meant to tell
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from reforge.runtime.eda.models import EdaReport
from reforge.runtime.eda.stages import DEFAULT_STAGES

_STATUS_LABEL = {
    "ok": "OK",
    "recovered": "RECOVERED",
    "failed": "FAILED",
    "skipped": "SKIPPED",
    "pending": "PENDING",
}


def render_markdown(report: EdaReport, *, title: str | None = None) -> str:
    title = title or f"EDA report: {Path(report.dataset_path).name}"
    parts: list[str] = []
    parts.append(f"# {title}\n")
    parts.append(_overview_section(report))
    parts.append(_summary_table(report))
    parts.append(_stage_details(report))
    parts.append(_footer(report))
    return "\n".join(parts)


# ---------------------------------------------------------------------------


def _overview_section(report: EdaReport) -> str:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return (
        "## Overview\n\n"
        f"- Dataset path: `{report.dataset_path}`\n"
        f"- Stages run: **{report.stage_count}** "
        f"(ok={report.ok_count}, recovered={report.recovered_count}, "
        f"failed={report.failed_count})\n"
        f"- Total attempts (across all stages): **{report.total_attempts}**\n"
        f"- Wall time: **{report.total_duration_ms/1000:.1f} s**\n"
        f"- Generated at: {ts}\n"
    )


def _summary_table(report: EdaReport) -> str:
    rows = [
        "## Per stage",
        "",
        "| Stage | Status | Attempts | Eval | Duration (s) |",
        "|---|---|---|---|---|",
    ]
    for r in report.stages:
        rows.append(
            f"| `{r.stage_id}` | {_STATUS_LABEL.get(r.status, r.status)} "
            f"| {r.attempts} | {r.eval_score:.2f} "
            f"| {r.duration_ms/1000:.1f} |"
        )
    return "\n".join(rows) + "\n"


def _stage_details(report: EdaReport) -> str:
    titles = {s.id: s.title for s in DEFAULT_STAGES}
    parts = ["## Stage outputs", ""]
    for r in report.stages:
        title = titles.get(r.stage_id, r.stage_id)
        parts.append(f"### {title} (`{r.stage_id}`)")
        parts.append("")
        parts.append(
            f"**Status:** {_STATUS_LABEL.get(r.status, r.status)}  "
            f"**Attempts:** {r.attempts}  "
            f"**Eval:** {r.eval_score:.2f}  "
            f"**Duration:** {r.duration_ms/1000:.1f}s\n"
        )
        if r.status == "failed":
            parts.append("```text")
            parts.append(r.error or "(no error text captured)")
            parts.append("```\n")
        else:
            parts.append("```text")
            parts.append(_truncate(r.output, 4000) or "(empty output)")
            parts.append("```\n")
    return "\n".join(parts)


def _footer(report: EdaReport) -> str:
    extra_attempts = report.total_attempts - report.stage_count
    line = (
        "_Self-healing footprint: the runtime burned "
        f"**{extra_attempts}** extra attempt(s) beyond the {report.stage_count} "
        "first-shots, recovering "
        f"{report.recovered_count} stage(s) after failure._"
    )
    return "---\n\n" + line + "\n"


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit - 100]
    tail = text[-50:]
    return f"{head}\n... [truncated {len(text) - limit} chars] ...\n{tail}"
