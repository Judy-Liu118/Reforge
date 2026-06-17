"""Render BenchmarkReport as Markdown — pasteable into README / résumé."""

from __future__ import annotations

from reforge.benchmark.models import BenchmarkReport


def render_markdown(report: BenchmarkReport, *, title: str = "Reforge Benchmark") -> str:
    """Render the headline + per-category + per-case tables."""
    lines: list[str] = [f"# {title}", ""]
    lines.extend(_overview_section(report))
    lines.append("")
    lines.extend(_per_category_section(report))
    lines.append("")
    lines.extend(_per_case_section(report))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def _overview_section(report: BenchmarkReport) -> list[str]:
    return [
        "## Overview",
        "",
        f"- Total cases       : **{report.total}**",
        f"- Passed            : **{report.passed} ({report.pass_rate:.0%})**",
        f"- First-shot success: **{report.first_shot_success_rate:.0%}**",
        f"- Recovered         : **{report.recovery_rate:.0%}**",
        f"- Hard failures     : **{report.hard_failure_rate:.0%}**",
        f"- Average attempts  : **{report.average_attempts:.2f}**",
        f"- Average eval score: **{report.average_eval_score:.2f}**",
        f"- Average duration  : **{report.average_duration_ms / 1000:.2f} s**",
    ]


def _per_category_section(report: BenchmarkReport) -> list[str]:
    out: list[str] = ["## Per category", ""]
    out.append("| Category | Cases | Pass | Recovered | Avg attempts | Avg score |")
    out.append("|---|---|---|---|---|---|")
    for cat, sub in sorted(report.by_category().items()):
        out.append(
            f"| {cat} | {sub.total} | {sub.passed}/{sub.total} ({sub.pass_rate:.0%}) | "
            f"{sub.recovery_rate:.0%} | {sub.average_attempts:.2f} | {sub.average_eval_score:.2f} |"
        )
    return out


def _per_case_section(report: BenchmarkReport) -> list[str]:
    out: list[str] = ["## Per case", ""]
    out.append("| Case | Expected | Actual | Pass | Attempts | Score | Recalls | Duration (s) |")
    out.append("|---|---|---|---|---|---|---|---|")
    for r in report.runs:
        ok = "PASS" if r.passed else "FAIL"
        out.append(
            f"| `{r.case_id}` | {r.expected_outcome} | {r.actual_outcome} | {ok} | "
            f"{r.attempts} | {r.eval_score:.2f} | {r.memory_recalls} | "
            f"{r.duration_ms / 1000:.2f} |"
        )
    return out


# ---------------------------------------------------------------------------
# Learning-curve dedicated renderer
# ---------------------------------------------------------------------------


def render_learning_curve_markdown(
    report: BenchmarkReport, *, title: str = "Cross-session learning curve"
) -> str:
    """Render the per-round eval_score / attempts curve for one case."""
    out: list[str] = [f"# {title}", ""]
    curve = report.learning_curve()

    out.append("## Score evolution")
    out.append("")
    for case_id, scores in curve.items():
        ascii_curve = " → ".join(f"{s:.2f}" for s in scores)
        out.append(f"- `{case_id}` : {ascii_curve}")
    out.append("")

    out.append("## Per-round detail")
    out.append("")
    out.append("| Round | Outcome | Attempts | Score | Memory recalls |")
    out.append("|---|---|---|---|---|")
    for i, r in enumerate(report.runs, start=1):
        out.append(
            f"| {i} | {r.actual_outcome} | {r.attempts} | {r.eval_score:.2f} | {r.memory_recalls} |"
        )
    return "\n".join(out)
