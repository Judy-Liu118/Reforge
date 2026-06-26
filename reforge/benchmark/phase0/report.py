"""Phase 0 calibration report — markdown emitter + go/no-go.

Single responsibility: turn a list of :class:`CalibrationRecord` into
the locked go / no-go assertions documented in PHASE0_CORPUS.md and
PHASE0_METRICS.md. The report deliberately stops at instrument
self-check; result-direction interpretation (governor advantage on
BIRD etc.) is Phase 1 hero territory and would pollute the
"instrument only" boundary if it leaked in here.

Four gates, each ≥1 occurrence required:
  1. execution_recovery   — ≥1 toy run in T2/T3 with passed AND attempts>1
  2. eval_driven_recovery — ≥1 toy run in T1     with passed AND attempts>1
  3. timeout_deliberate_STOP — ≥1 D1″ run with action=STOP,
        retry_count < config.max_retry, failure_mode=="timeout"
  4. BIRD recoverability  — ≥1 governor run on bird_7 / bird_1313 with
        passed AND attempts>1
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from reforge.benchmark.phase0.corpus import (
    BIRD_CASE_IDS,
    BIRD_RECOVERABILITY_GATE_IDS,
    D1_TIMEOUT,
    T1_SALES,
    T2_USERS,
    T3_ORDERS,
)
from reforge.benchmark.phase0.driver import CalibrationRecord
from reforge.config import config


def _filter(records, *, kind=None, case_id=None, mode=None):
    out = list(records)
    if kind:
        out = [r for r in out if r.kind == kind]
    if case_id:
        out = [r for r in out if r.case_id == case_id]
    if mode:
        out = [r for r in out if r.mode == mode]
    return out


def _gate_execution_recovery(records: list[CalibrationRecord]) -> tuple[int, list[CalibrationRecord]]:
    hits = [
        r for r in records
        if r.kind == "recovery_exec"
        and r.case_id in (T2_USERS.case_id, T3_ORDERS.case_id)
        and r.recovered
    ]
    return len(hits), hits


def _gate_eval_driven_recovery(records: list[CalibrationRecord]) -> tuple[int, list[CalibrationRecord]]:
    hits = [
        r for r in records
        if r.kind == "recovery_eval"
        and r.case_id == T1_SALES.case_id
        and r.recovered
    ]
    return len(hits), hits


def _gate_timeout_deliberate_stop(records: list[CalibrationRecord]) -> tuple[int, list[CalibrationRecord]]:
    # Governor-mode only: naive cannot issue a deliberate STOP by construction.
    hits = [
        r for r in records
        if r.case_id == D1_TIMEOUT.case_id
        and r.mode == "governor"
        and r.action == "STOP"
        and r.failure_mode == "timeout"
        and r.retry_count < config.max_retry
    ]
    return len(hits), hits


def _gate_bird_recoverability(records: list[CalibrationRecord]) -> tuple[int, list[CalibrationRecord]]:
    hits = [
        r for r in records
        if r.mode == "governor"
        and r.case_id in BIRD_RECOVERABILITY_GATE_IDS
        and r.recovered
    ]
    return len(hits), hits


def evaluate(records: list[CalibrationRecord]) -> dict:
    """Return a dict the markdown emitter renders. Pure function over records."""
    n_exec, exec_hits = _gate_execution_recovery(records)
    n_eval, eval_hits = _gate_eval_driven_recovery(records)
    n_stop, stop_hits = _gate_timeout_deliberate_stop(records)
    n_bird, bird_hits = _gate_bird_recoverability(records)

    gates = [
        ("execution_recovery (T2/T3)", n_exec, exec_hits),
        ("eval_driven_recovery (T1)", n_eval, eval_hits),
        ("timeout_deliberate_STOP (D1″)", n_stop, stop_hits),
        ("bird_recoverability (bird_7 / bird_1313 governor)", n_bird, bird_hits),
    ]
    all_pass = all(n >= 1 for _, n, _ in gates)
    return {
        "gates": gates,
        "all_pass": all_pass,
        "records": records,
    }


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def _hits_one_line(hits: list[CalibrationRecord]) -> str:
    if not hits:
        return "_none_"
    return ", ".join(
        f"{h.case_id} (seed={h.seed}, attempts={h.attempts}, mode={h.mode})"
        for h in hits[:5]
    ) + (" …" if len(hits) > 5 else "")


def render_markdown(result: dict, *, n_seeds: int) -> str:
    records: list[CalibrationRecord] = result["records"]
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines: list[str] = []
    lines.append("# Phase 0 calibration — instrument self-check")
    lines.append("")
    lines.append(
        "> **Scope**: instrument only. This report verifies the four runtime "
        "code paths Phase 1 / 2 depend on are reachable; it deliberately does "
        "NOT interpret governor vs naive result-direction deltas on BIRD "
        "(that is Phase 1 hero territory and would pollute the "
        "\"instrument only\" boundary if it leaked into the calibration "
        "report). See `docs/eval/PHASE0_CORPUS.md` and "
        "`docs/eval/PHASE0_METRICS.md` for the locked methodology, and "
        "`docs/KNOWN_LIMITATIONS.md` L3 for the deliberate-STOP coverage "
        "boundary."
    )
    lines.append("")
    lines.append(f"- Run timestamp: {ts}")
    lines.append(f"- Seeds: {n_seeds}")
    lines.append(f"- `config.max_retry`: {config.max_retry}")
    lines.append(f"- Total records: {len(records)} "
                 f"(2 modes × {n_seeds} seeds × 9 cases)")
    lines.append("")

    # Top-level go / no-go banner
    verdict = "GO" if result["all_pass"] else "**NO-GO**"
    lines.append(f"## Verdict: {verdict}")
    lines.append("")
    if not result["all_pass"]:
        lines.append(
            "Any gate at zero occurrences means the calibrated path "
            "did not fire; the gate is fake-green and Phase 1 must not "
            "start until the underlying mechanism is fixed. Do NOT relax "
            "the gate threshold to ship the report."
        )
        lines.append("")

    # Per-gate detail
    lines.append("## Gate results")
    lines.append("")
    lines.append("| Gate | Occurrences | Required | Status | Evidence |")
    lines.append("|---|---|---|---|---|")
    for name, n, hits in result["gates"]:
        status = "✅ pass" if n >= 1 else "❌ FAIL"
        lines.append(f"| {name} | {n} | ≥ 1 | {status} | {_hits_one_line(hits)} |")
    lines.append("")

    # Coverage boundary — explicit honesty
    lines.append("## Coverage boundary (not a metric)")
    lines.append("")
    lines.append(
        "- D1″ probes **only the timeout sub-path** of deliberate STOP "
        "(`failure_mode == \"timeout\"`, set in `classifier.py:36-40` when "
        "sandbox watchdog kills the process). The "
        "**terminal_intentional sub-path** (`is_expected_failure=True AND "
        "retryable=False`, set when IntentStage classifies the user request "
        "as EXPECTED_ERROR / TRACEBACK_DEMO) is NOT exercised here — "
        "constructing a prompt that triggers it would leak intent into the "
        "calibration corpus. See `docs/KNOWN_LIMITATIONS.md` L3 for the "
        "architectural gap and why a pattern-based detector is deferred."
    )
    lines.append(
        "- BIRD result-direction comparison (governor vs naive solve / "
        "recovery rates) is **out of Phase 0 scope** and is intentionally "
        "not computed in this report. See Phase 1 (`docs/eval/...` to be "
        "written) for the headline ablation."
    )
    lines.append("")

    # Per-mode raw counts (data only, no interpretation)
    lines.append("## Per-mode raw counts (data, no interpretation)")
    lines.append("")
    lines.append("| Mode | Kind | passed | recovered | first_try | n_runs |")
    lines.append("|---|---|---|---|---|---|")
    for mode in ("governor", "naive"):
        for kind in ("bird", "recovery_eval", "recovery_exec", "decoy_timeout"):
            slc = _filter(records, kind=kind, mode=mode)
            if not slc:
                continue
            n = len(slc)
            lines.append(
                f"| {mode} | {kind} | {sum(r.passed for r in slc)} | "
                f"{sum(r.recovered for r in slc)} | "
                f"{sum(r.first_try for r in slc)} | {n} |"
            )
    lines.append("")

    # Token coverage
    lines.append("## Token accounting coverage")
    lines.append("")
    total = len(records)
    unknown = sum(1 for r in records if r.tokens_unknown)
    measured = total - unknown
    total_calls = sum(r.n_llm_calls for r in records)
    total_prompt = sum(r.tokens_prompt for r in records)
    total_completion = sum(r.tokens_completion for r in records)
    lines.append(
        f"- Runs with full token coverage: {measured} / {total} "
        f"({100 * measured / total:.1f}%)"
    )
    if unknown:
        lines.append(
            f"- Runs with `unknown=True` (provider returned `usage=None` for "
            f"≥ 1 call): {unknown}. Reporting rule (PHASE0_METRICS.md "
            f"v2 §3): these runs are excluded from `tokens_per_solved` "
            f"numerator and denominator; >20% exclusion blocks headline."
        )
    lines.append(
        f"- Total LLM calls captured by harness-side accumulator: "
        f"{total_calls} (prompt tokens: {total_prompt}, completion tokens: "
        f"{total_completion})"
    )
    lines.append("")

    # Appendix: raw records (JSON-ish), so the markdown is self-contained
    lines.append("## Appendix — raw records")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps([asdict(r) for r in records], indent=2, ensure_ascii=False))
    lines.append("```")
    lines.append("")

    return "\n".join(lines)


def write_report(
    records: list[CalibrationRecord],
    *,
    out_path: Path,
    n_seeds: int,
) -> bool:
    """Compute go/no-go, render markdown, write to disk. Returns ``all_pass``."""
    result = evaluate(records)
    md = render_markdown(result, n_seeds=n_seeds)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    return result["all_pass"]
