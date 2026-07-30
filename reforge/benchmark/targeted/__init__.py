"""Targeted governor-efficacy harness.

Phase 1's BIRD driver returned a null governor-vs-naive success delta
because BIRD's failures are mostly *silent semantic errors* (a query
that runs clean but computes the wrong answer) — a form the runtime
cannot detect without gold, so neither arm's retry can act on it
(KNOWN_LIMITATIONS L6, PHASE1_BIRD_ABLATION_R2/R3).

This package tests the governor where its value assumption actually
holds: failures that are runtime-observable (exit_code != 0 / traceback)
and repairable. It lifts Phase 1's three SQL-hardwired seams — case
supply, prompt construction, deterministic oracle — into an `EvalTask`
protocol, and reuses the untouched skeleton (arm switching, per-leg
cold-start memory with leg-internal accrual, token accounting,
attempt-level capture, Student-t paired CI) for any dataset.
"""
