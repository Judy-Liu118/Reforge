"""Render an ExperienceReport as Markdown.

Renders four sections:
  1. Overview          — the headline KPIs (transfer rate, attempt delta)
  2. Cold vs Warm      — side-by-side bar comparison
  3. Per-pair detail   — one row per PairedCase across both legs
  4. Per-run trace     — raw BenchmarkRun fields (attempts, score, recalls)

Markdown is pasteable straight into the project README / a résumé snippet.
"""

from __future__ import annotations

from reforge.benchmark.experience_driver import (
    ExperienceReport,
    PairResult,
    pair_passed,
)
from reforge.benchmark.models import BenchmarkRun


def render_experience_markdown(
    report: ExperienceReport,
    *,
    title: str = "Reforge Experience Memory Benchmark",
) -> str:
    lines: list[str] = [f"# {title}", ""]
    lines.extend(_overview(report))
    lines.append("")
    lines.extend(_cold_vs_warm(report))
    lines.append("")
    lines.extend(_per_pair_table(report))
    lines.append("")
    lines.extend(_per_run_trace(report))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def _overview(report: ExperienceReport) -> list[str]:
    return [
        "## Overview",
        "",
        f"- Pairs run                  : **{report.total_pairs}**",
        "",
        "### Headline KPIs (the three that survive any single metric blinding the others)",
        "",
        f"- **Transfer success rate**  : **{report.transfer_success_rate:+.0%}** "
        "(warm pass rate − cold pass rate; positive = memory rescued a failure)",
        f"- **First-try rate delta**   : **{report.first_try_delta:+.0%}** "
        "(warm first-try − cold first-try; positive = memory saved an attempt "
        "even when both legs ultimately passed)",
        f"- **Attempts reduction**     : **{report.attempts_reduction:+.2f}** "
        "(avg cold attempts − avg warm attempts)",
        "",
        "### Supporting metrics",
        "",
        f"- Cold-A' pass rate          : {report.cold_a_prime_pass_rate:.0%}",
        f"- Warm-A' pass rate          : {report.warm_a_prime_pass_rate:.0%}",
        f"- Cold-A' first-try success  : {report.cold_first_try_rate:.0%}",
        f"- Warm-A' first-try success  : {report.warm_first_try_rate:.0%}",
        f"- Avg attempts (cold A')     : {report.avg_cold_attempts:.2f}",
        f"- Avg attempts (warm A')     : {report.avg_warm_attempts:.2f}",
        f"- Warm-A' recall hit rate    : {report.warm_recall_hit_rate:.0%} "
        "(recall API fired; does NOT mean the planner used the result — see "
        "`docs/experience_benchmark.md` §6.3)",
    ]


def _cold_vs_warm(report: ExperienceReport) -> list[str]:
    cold_pass = report.cold_a_prime_pass_rate
    warm_pass = report.warm_a_prime_pass_rate
    cold_ft = report.cold_first_try_rate
    warm_ft = report.warm_first_try_rate
    return [
        "## Cold vs Warm",
        "",
        "```",
        "                     pass rate                first-try rate",
        f"Cold-A'  : {_bar(cold_pass)} {cold_pass:>4.0%}   "
        f"{_bar(cold_ft)} {cold_ft:>4.0%}",
        f"Warm-A'  : {_bar(warm_pass)} {warm_pass:>4.0%}   "
        f"{_bar(warm_ft)} {warm_ft:>4.0%}",
        "```",
    ]


def _per_pair_table(report: ExperienceReport) -> list[str]:
    out: list[str] = [
        "## Per pair",
        "",
        "| Pair | Fingerprint axis | Cold-A | Cold-A' | Warm-A | Warm-A' | "
        "Transfer | Att. Δ | Recall |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for p in report.pairs:
        out.append(
            f"| `{p.pair_id}` | {p.fingerprint_axis} "
            f"| {_run_cell(p.cold_a)} | {_run_cell(p.cold_a_prime)} "
            f"| {_run_cell(p.warm_a)} | {_run_cell(p.warm_a_prime)} "
            f"| {'PASS' if p.transfer_passed else '—'} "
            f"| {p.attempt_delta:+d} "
            f"| {p.warm_a_prime.memory_recalls} |"
        )
    return out


def _per_run_trace(report: ExperienceReport) -> list[str]:
    out: list[str] = [
        "## Per run trace",
        "",
        "| Pair | Leg | Case | Outcome | Attempts | Score | Recalls | Duration (s) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for p in report.pairs:
        for leg, run in (
            ("cold.A", p.cold_a),
            ("cold.A'", p.cold_a_prime),
            ("warm.A", p.warm_a),
            ("warm.A'", p.warm_a_prime),
        ):
            out.append(
                f"| `{p.pair_id}` | {leg} | `{run.case_id}` | {run.actual_outcome} "
                f"| {run.attempts} | {run.eval_score:.2f} | {run.memory_recalls} "
                f"| {run.duration_ms / 1000:.2f} |"
            )
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_cell(run: BenchmarkRun) -> str:
    """Compact run cell: PASS/FAIL + attempts."""
    tag = "PASS" if pair_passed(run) else "FAIL"
    return f"{tag} (a={run.attempts})"


def _bar(rate: float, width: int = 30) -> str:
    """ASCII proportion bar for a 0..1 rate."""
    n = max(0, min(width, round(rate * width)))
    return "█" * n + "·" * (width - n)
