"""Phase 1 BIRD ablation report — Tier A paired metrics + appendices.

Consumes ``list[Phase1Record]`` and emits the markdown report locked in
``docs/eval/PHASE1_CORPUS.md``:

- headline table: per-seed paired deltas (governor − naive) with the
  Student-t 95% CI over seeds, worded per the pre-registered
  significance rule (CI crossing zero → "no significant effect");
- per-seed and per-difficulty appendices;
- case-level paired-CI robustness appendix (supporting evidence only);
- v4 §4 sensitivity appendix: attempt-level evaluator false-negative
  rates per arm, their paired delta, and the locked asymmetry verdict.

Aggregation reuses :func:`reforge.benchmark.experience_multiseed.summarise`
so the CI machinery is identical to the shipped memory-ablation harness.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean

from reforge.benchmark.experience_multiseed import StatSummary, summarise
from reforge.benchmark.phase1.driver import Phase1Record
from reforge.config import config

_MODES = ("governor", "naive")
_DIFFICULTIES = ("simple", "moderate", "challenging")

# Pre-registered: tokens_per_solved cells with >20% of solved runs
# excluded (unknown usage) may not feed a headline.
_TOKEN_EXCLUSION_HEADLINE_CAP = 0.20


# ---------------------------------------------------------------------------
# Per-(mode, seed) Tier A metrics
# ---------------------------------------------------------------------------


def _leg(records: Sequence[Phase1Record], mode: str, seed: int) -> list[Phase1Record]:
    return [r for r in records if r.mode == mode and r.seed == seed]


def success_rate(leg: Sequence[Phase1Record]) -> float | None:
    return fmean(1.0 if r.passed else 0.0 for r in leg) if leg else None


def first_try_rate(leg: Sequence[Phase1Record]) -> float | None:
    return fmean(1.0 if r.first_try else 0.0 for r in leg) if leg else None


def recovery_rate(leg: Sequence[Phase1Record]) -> float | None:
    denom = [r for r in leg if not r.first_try]
    if not denom:
        return None
    return sum(1 for r in denom if r.recovered) / len(denom)


def attempts_per_case(leg: Sequence[Phase1Record]) -> float | None:
    return fmean(r.attempts for r in leg) if leg else None


def mean_attempts_on_unsolved(leg: Sequence[Phase1Record]) -> float | None:
    unsolved = [r for r in leg if not r.passed]
    if not unsolved:
        return None
    return fmean(r.attempts for r in unsolved)


def tokens_per_solved(leg: Sequence[Phase1Record]) -> float | None:
    known = [r for r in leg if r.passed and not r.tokens_unknown]
    if not known:
        return None
    return sum(r.tokens_prompt + r.tokens_completion for r in known) / len(known)


def token_excluded_count(leg: Sequence[Phase1Record]) -> int:
    return sum(1 for r in leg if r.passed and r.tokens_unknown)


def wall_clock_per_solved_s(leg: Sequence[Phase1Record]) -> float | None:
    if not leg:
        return None
    solved = sum(1 for r in leg if r.passed)
    return sum(r.duration_ms for r in leg) / max(solved, 1) / 1000.0


_METRICS: tuple[tuple[str, Callable[[Sequence[Phase1Record]], float | None]], ...] = (
    ("success_rate", success_rate),
    ("first_try_rate", first_try_rate),
    ("recovery_rate", recovery_rate),
    ("attempts_per_case", attempts_per_case),
    ("mean_attempts_on_unsolved", mean_attempts_on_unsolved),
    ("tokens_per_solved", tokens_per_solved),
    ("wall_clock_per_solved_s", wall_clock_per_solved_s),
)


# ---------------------------------------------------------------------------
# Pairing + aggregation
# ---------------------------------------------------------------------------


def paired_deltas(
    records: Sequence[Phase1Record],
    metric: Callable[[Sequence[Phase1Record]], float | None],
    n_seeds: int,
) -> tuple[list[float], int]:
    """Per-seed governor−naive deltas; seeds where either arm is N/A are
    dropped (the count of used seeds is reported alongside)."""
    deltas: list[float] = []
    for seed in range(n_seeds):
        gov = metric(_leg(records, "governor", seed))
        nai = metric(_leg(records, "naive", seed))
        if gov is None or nai is None:
            continue
        deltas.append(gov - nai)
    return deltas, len(deltas)


def _mode_mean(
    records: Sequence[Phase1Record],
    mode: str,
    metric: Callable[[Sequence[Phase1Record]], float | None],
    n_seeds: int,
) -> float | None:
    vals = [
        v for seed in range(n_seeds)
        if (v := metric(_leg(records, mode, seed))) is not None
    ]
    return fmean(vals) if vals else None


def _verdict(stat: StatSummary) -> str:
    if stat.n <= 1:
        return "insufficient seeds"
    return "**significant**" if stat.excludes_zero else "no significant effect (CI includes 0)"


# ---------------------------------------------------------------------------
# Sensitivity appendix (v4 §4)
# ---------------------------------------------------------------------------


def fn_rate_all_attempts(leg: Sequence[Phase1Record]) -> float | None:
    """Attempt-level evaluator false negatives / all observed attempts."""
    total = fn = 0
    for r in leg:
        for obs in r.attempt_observations:
            total += 1
            if obs.comparator_correct and not obs.eval_passed:
                fn += 1
    return fn / total if total else None


def fn_rate_correct_attempts(leg: Sequence[Phase1Record]) -> float | None:
    """Evaluator false negatives / comparator-correct attempts."""
    correct = fn = 0
    for r in leg:
        for obs in r.attempt_observations:
            if obs.comparator_correct:
                correct += 1
                if not obs.eval_passed:
                    fn += 1
    return fn / correct if correct else None


def l6_run_count(leg: Sequence[Phase1Record]) -> int:
    """Runs where the comparator passed but the runtime said FAILED."""
    return sum(1 for r in leg if r.passed and r.runtime_outcome == "FAILED")


# ---------------------------------------------------------------------------
# Markdown emission
# ---------------------------------------------------------------------------


def _fmt(v: float | None, pct: bool = False) -> str:
    if v is None:
        return "N/A"
    if pct:
        return f"{v * 100:.1f}%"
    return f"{v:.2f}" if abs(v) < 1000 else f"{v:,.0f}"


_PCT_METRICS = {"success_rate", "first_try_rate", "recovery_rate"}


def _git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True, timeout=10,
        ).stdout.strip()
    except Exception:
        return "unknown"


def write_report(
    records: list[Phase1Record],
    *,
    out_path: Path,
    n_seeds: int,
) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    head = _git_head()
    case_ids = sorted({r.case_id for r in records})
    n_cases = len(case_ids)

    lines: list[str] = []
    add = lines.append
    add("# Phase 1 — BIRD governor ablation (pre-registered)")
    add("")
    add(f"> Generated {now} at commit `{head}`. Corpus + protocol lock: "
        "`docs/eval/PHASE1_CORPUS.md`; methodology lock: `docs/eval/PHASE0_METRICS.md` v4. "
        "Field-of-record for passed/failed: `reforge.runtime.sql.comparator` "
        "(KNOWN_LIMITATIONS L6). Raw records: `docs/eval/phase1_records.jsonl`.")
    add("")
    add(f"- Sample: {n_cases} cases × 2 modes × {n_seeds} seeds = {len(records)} runs; "
        f"codegen model pinned: `{config.llm_model}`.")
    unknown = sum(1 for r in records if r.tokens_unknown)
    add(f"- Token coverage: {len(records) - unknown}/{len(records)} runs with known usage.")
    add("")

    # ----- headline table -----
    add("## Headline — per-seed paired deltas (governor − naive)")
    add("")
    add("| Metric | governor | naive | Δ mean | Δ 95% CI | seeds used | verdict |")
    add("|---|---|---|---|---|---|---|")
    token_low_confidence = False
    for name, metric in _METRICS:
        pct = name in _PCT_METRICS
        deltas, used = paired_deltas(records, metric, n_seeds)
        stat = summarise(deltas)
        gov = _mode_mean(records, "governor", metric, n_seeds)
        nai = _mode_mean(records, "naive", metric, n_seeds)
        verdict = _verdict(stat) if used else "N/A in all seeds"
        if name == "tokens_per_solved":
            for mode in _MODES:
                for seed in range(n_seeds):
                    leg = _leg(records, mode, seed)
                    solved = sum(1 for r in leg if r.passed)
                    excluded = token_excluded_count(leg)
                    if solved and excluded / solved > _TOKEN_EXCLUSION_HEADLINE_CAP:
                        token_low_confidence = True
            if token_low_confidence:
                verdict += " — LOW CONFIDENCE (>20% excluded)"
        ci = (
            f"[{_fmt(stat.ci95_low, pct)}, {_fmt(stat.ci95_high, pct)}]"
            if used >= 2 else "N/A"
        )
        add(f"| {name} | {_fmt(gov, pct)} | {_fmt(nai, pct)} | "
            f"{_fmt(stat.mean if used else None, pct)} | {ci} | {used}/{n_seeds} | {verdict} |")
    add("")
    add("Pre-registered rule: only rows marked **significant** are headline-eligible; "
        "every other delta is reported for completeness and may not appear in "
        "abstract / README / narrative claims.")
    add("")

    # ----- per-seed appendix -----
    add("## Appendix A — per-seed values")
    add("")
    add("| Metric | mode | " + " | ".join(f"seed {s}" for s in range(n_seeds)) + " |")
    add("|---|---|" + "---|" * n_seeds)
    for name, metric in _METRICS:
        pct = name in _PCT_METRICS
        for mode in _MODES:
            cells = [
                _fmt(metric(_leg(records, mode, seed)), pct) for seed in range(n_seeds)
            ]
            add(f"| {name} | {mode} | " + " | ".join(cells) + " |")
    add("")

    # ----- per-difficulty appendix -----
    add("## Appendix B — per-difficulty breakout")
    add("")
    add("| Difficulty | cases | metric | governor | naive | Δ mean | Δ 95% CI |")
    add("|---|---|---|---|---|---|---|")
    for diff in _DIFFICULTIES:
        subset = [r for r in records if r.difficulty == diff]
        if not subset:
            continue
        diff_cases = len({r.case_id for r in subset})
        for name, metric in (("success_rate", success_rate),
                             ("attempts_per_case", attempts_per_case)):
            pct = name in _PCT_METRICS
            deltas, used = paired_deltas(subset, metric, n_seeds)
            stat = summarise(deltas)
            gov = _mode_mean(subset, "governor", metric, n_seeds)
            nai = _mode_mean(subset, "naive", metric, n_seeds)
            ci = (
                f"[{_fmt(stat.ci95_low, pct)}, {_fmt(stat.ci95_high, pct)}]"
                if used >= 2 else "N/A"
            )
            add(f"| {diff} | {diff_cases} | {name} | {_fmt(gov, pct)} | "
                f"{_fmt(nai, pct)} | {_fmt(stat.mean if used else None, pct)} | {ci} |")
    add("")

    # ----- case-level robustness appendix -----
    add("## Appendix C — case-level paired CI (supporting evidence only)")
    add("")
    add("Per pre-registration: reported as robustness support, never a "
        "substitute for the seed-level CI. `delta_case` = per-case "
        "mean-over-seeds difference; CI over cases (df = n_cases − 1).")
    add("")
    add("| Metric | Δ mean over cases | 95% CI | excludes zero |")
    add("|---|---|---|---|")
    for name, metric in (("success_rate", success_rate),
                         ("first_try_rate", first_try_rate),
                         ("attempts_per_case", attempts_per_case)):
        pct = name in _PCT_METRICS
        case_deltas: list[float] = []
        for cid in case_ids:
            subset = [r for r in records if r.case_id == cid]
            gov = _mode_mean(subset, "governor", metric, n_seeds)
            nai = _mode_mean(subset, "naive", metric, n_seeds)
            if gov is None or nai is None:
                continue
            case_deltas.append(gov - nai)
        stat = summarise(case_deltas)
        add(f"| {name} | {_fmt(stat.mean, pct)} | "
            f"[{_fmt(stat.ci95_low, pct)}, {_fmt(stat.ci95_high, pct)}] | "
            f"{'yes' if stat.excludes_zero else 'no'} |")
    add("")

    # ----- sensitivity appendix -----
    add("## Appendix D — evaluator false-negative sensitivity (v4 §4)")
    add("")
    add("Attempt-level false negative := comparator confirms the attempt's "
        "stdout matches gold rows AND the internal LLM evaluator rejected it.")
    add("")
    add("| mode | FN / all attempts | FN / comparator-correct attempts | "
        "runs comparator-pass but runtime FAILED |")
    add("|---|---|---|---|")
    for mode in _MODES:
        legs = [r for r in records if r.mode == mode]
        add(f"| {mode} | {_fmt(fn_rate_all_attempts(legs), pct=True)} | "
            f"{_fmt(fn_rate_correct_attempts(legs), pct=True)} | {l6_run_count(legs)} |")
    add("")
    deltas, used = paired_deltas(records, fn_rate_all_attempts, n_seeds)
    stat = summarise(deltas)
    add(f"Paired per-seed FN-rate delta (governor − naive): mean "
        f"{_fmt(stat.mean if used else None, pct=True)}, 95% CI "
        f"[{_fmt(stat.ci95_low, pct=True)}, {_fmt(stat.ci95_high, pct=True)}] "
        f"({used}/{n_seeds} seeds).")
    add("")
    if used >= 2 and stat.excludes_zero:
        add("**Verdict: ASYMMETRIC.** Evaluator false-negative pressure differs "
            "between arms; per the locked rule, every headline claim above "
            "must carry this caveat explicitly.")
    else:
        add("**Verdict: symmetric within noise.** Per the locked rule, paired "
            "subtraction cancels the common evaluator noise and the headline "
            "stands unqualified.")
    add("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
