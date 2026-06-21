"""Markdown reporter for the multi-seed Experience Memory Benchmark.

Renders mean ± std + 95% CI per headline KPI, plus per-pair seed
statistics. The "CI excludes zero" column is the deciding question for
whether any observed effect survives statistical scrutiny.
"""

from __future__ import annotations

from reforge.benchmark.experience_multiseed import (
    MultiSeedReport,
    PairMultiSeed,
    StatSummary,
)


def render_multiseed_markdown(
    report: MultiSeedReport,
    *,
    title: str = "Reforge Experience Memory Benchmark — Multi-Seed",
) -> str:
    lines: list[str] = [f"# {title}", ""]
    lines.extend(_overview(report))
    lines.append("")
    lines.extend(_headline_kpi_table(report))
    lines.append("")
    lines.extend(_per_pair_section(report))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def _overview(report: MultiSeedReport) -> list[str]:
    return [
        "## Overview",
        "",
        f"- Pairs           : **{report.n_pairs}**",
        f"- Seeds per pair  : **{report.n_seeds}**",
        f"- Total runs      : **{report.total_runs}** ({report.n_pairs} × "
        f"{report.n_seeds} × 4 legs)",
        "",
        "The headline KPIs below are *per-seed deltas* (each seed gives one "
        "warm-minus-cold number), then summarised across seeds. The 95% CI "
        "is two-tailed Student-t with df = n − 1. If the CI **excludes zero**, "
        "the effect is statistically distinguishable from null at α = 0.05; "
        "if it doesn't, the observation is consistent with noise.",
    ]


def _headline_kpi_table(report: MultiSeedReport) -> list[str]:
    transfer = report.transfer_success_rate
    first_try = report.first_try_delta
    attempts = report.attempts_reduction

    out = [
        "## Headline KPIs (mean ± std, 95% CI)",
        "",
        "| KPI | Mean | Std | 95% CI | CI excl. 0? | Verdict |",
        "|---|---|---|---|---|---|",
        _kpi_row("Transfer success rate (Δ pass rate)", transfer, as_percent=True),
        _kpi_row("First-try rate delta (Δ first-try)", first_try, as_percent=True),
        _kpi_row("Attempts reduction (cold − warm)", attempts, as_percent=False),
    ]
    return out


def _per_pair_section(report: MultiSeedReport) -> list[str]:
    out: list[str] = [
        "## Per pair — multi-seed stats",
        "",
        "Per-pair stats use the seed as the unit of observation. A pair "
        "with N=5 seeds and `Warm-A' first-try` mean=0.40, std=0.55 means "
        "warm hit first-try in 2 of 5 seeds — wide spread, noisy signal.",
        "",
        "| Pair | Axis | Cold pass | Warm pass | Cold 1st-try | Warm 1st-try | Δ first-try (CI) |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in report.pairs:
        out.append(_per_pair_row(p))
    return out


# ---------------------------------------------------------------------------
# Cell helpers
# ---------------------------------------------------------------------------


def _kpi_row(label: str, s: StatSummary, *, as_percent: bool) -> str:
    if as_percent:
        mean_s = f"{s.mean:+.0%}"
        std_s = f"{s.std:.0%}"
        ci_s = (
            f"[{s.ci95_low:+.0%}, {s.ci95_high:+.0%}]"
            if s.n >= 2 else "—"
        )
    else:
        mean_s = f"{s.mean:+.2f}"
        std_s = f"{s.std:.2f}"
        ci_s = (
            f"[{s.ci95_low:+.2f}, {s.ci95_high:+.2f}]"
            if s.n >= 2 else "—"
        )

    if s.n < 2:
        excl = "n/a"
        verdict = "need ≥2 seeds"
    elif s.excludes_zero:
        excl = "**YES**"
        verdict = (
            "**positive effect**" if s.mean > 0
            else "**negative effect**"
        )
    else:
        excl = "no"
        verdict = "consistent with noise"

    return f"| {label} | {mean_s} | {std_s} | {ci_s} | {excl} | {verdict} |"


def _per_pair_row(p: PairMultiSeed) -> str:
    cp = p.cold_pass_rate
    wp = p.warm_pass_rate
    cft = p.cold_first_try_rate
    wft = p.warm_first_try_rate
    delta = p.first_try_delta
    delta_cell = (
        f"{delta.mean:+.0%} [{delta.ci95_low:+.0%}, {delta.ci95_high:+.0%}]"
        if delta.n >= 2 else f"{delta.mean:+.0%}"
    )
    return (
        f"| `{p.pair_id}` | {p.fingerprint_axis} "
        f"| {cp.mean:.0%} ± {cp.std:.0%} "
        f"| {wp.mean:.0%} ± {wp.std:.0%} "
        f"| {cft.mean:.0%} ± {cft.std:.0%} "
        f"| {wft.mean:.0%} ± {wft.std:.0%} "
        f"| {delta_cell} |"
    )
