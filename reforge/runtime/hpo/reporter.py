"""Render an HpoBenchReport to Markdown."""

from __future__ import annotations

from datetime import datetime, timezone

from reforge.runtime.hpo.models import HpoBenchReport, HpoRun


_STATUS_LABEL = {
    "ok": "OK",
    "parse_error": "PARSE_ERROR",
    "runtime_error": "RUNTIME_ERROR",
}


def render_markdown(report: HpoBenchReport, *, title: str | None = None) -> str:
    title = title or "Reforge HPO benchmark report"
    parts = [f"# {title}\n", _summary(report), _per_case_table(report), _details(report)]
    return "\n".join(parts)


def _summary(report: HpoBenchReport) -> str:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return (
        "## Summary\n\n"
        f"- Cases: **{report.total_cases}** "
        f"(with a score: **{report.cases_with_a_score}**)\n"
        f"- Total trials: {report.total_trials} "
        f"(successful: {report.total_successful_trials}, "
        f"success rate: {report.trial_success_rate*100:.1f}%)\n"
        f"- Wall time: **{report.total_duration_ms/1000:.1f} s**\n"
        f"- Generated at: {ts}\n"
    )


def _per_case_table(report: HpoBenchReport) -> str:
    rows = [
        "## Per case",
        "",
        "| Case | Task | Best score | Best trial | Trials | Stopped | Duration (s) |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in report.runs:
        best = f"{r.best_cv_score:.4f}" if r.best_cv_score is not None else "—"
        best_trial = str(r.best_trial_index) if r.best_trial_index is not None else "—"
        rows.append(
            f"| `{r.case_id}` | {r.task} | **{best}** | {best_trial} | "
            f"{len(r.trials)} ({r.successful_trials} ok) | {r.stopped_reason} | "
            f"{r.duration_ms/1000:.1f} |"
        )
    return "\n".join(rows) + "\n"


def _details(report: HpoBenchReport) -> str:
    parts = ["## Trial details", ""]
    for r in report.runs:
        parts.append(f"### `{r.case_id}` ({r.task})")
        parts.append("")
        parts.append(_case_header(r))
        parts.append("")
        parts.append("| # | Status | CV score | Attempts | Duration (s) | Pipeline |")
        parts.append("|---|---|---|---|---|---|")
        for t in r.trials:
            score = f"{t.cv_score:.4f}" if t.cv_score is not None else "—"
            pipeline = (t.pipeline_summary or t.error or "").replace("|", "/")
            parts.append(
                f"| {t.trial_index} | {_STATUS_LABEL.get(t.status, t.status)} | "
                f"{score} | {t.attempts} | {t.duration_ms/1000:.1f} | "
                f"{pipeline[:120]} |"
            )
        parts.append("")
    return "\n".join(parts)


def _case_header(r: HpoRun) -> str:
    best = f"{r.best_cv_score:.4f}" if r.best_cv_score is not None else "—"
    return (
        f"**Best score:** {best}  "
        f"**Best trial:** {r.best_trial_index or '—'}  "
        f"**Stopped:** {r.stopped_reason}  "
        f"**Duration:** {r.duration_ms/1000:.1f}s"
    )
