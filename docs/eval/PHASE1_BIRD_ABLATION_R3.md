# Phase 1 — BIRD governor ablation (pre-registered)

> Generated 2026-07-13 06:50 UTC at commit `bcc11fb`. Corpus + protocol lock: `docs/eval/PHASE1_CORPUS.md`; methodology lock: `docs/eval/PHASE0_METRICS.md` v4. Field-of-record for passed/failed: `reforge.runtime.sql.comparator` (KNOWN_LIMITATIONS L6). Raw records: `docs/eval/phase1_records_r3.jsonl`.

- Sample: 20 cases × 2 modes × 5 seeds = 200 runs; codegen model pinned: `deepseek-v4-pro`.
- Token coverage: 200/200 runs with known usage.

## Headline — per-seed paired deltas (governor − naive)

| Metric | governor | naive | Δ mean | Δ 95% CI | seeds used | verdict |
|---|---|---|---|---|---|---|
| success_rate | 61.0% | 62.0% | -1.0% | [-9.1%, 7.1%] | 5/5 | no significant effect (CI includes 0) |
| first_try_rate | 56.0% | 61.0% | -5.0% | [-9.4%, -0.6%] | 5/5 | **significant** |
| recovery_rate | 11.4% | 2.5% | 8.9% | [-2.7%, 20.6%] | 5/5 | no significant effect (CI includes 0) |
| attempts_per_case | 1.31 | 1.01 | 0.30 | [0.12, 0.48] | 5/5 | **significant** |
| mean_attempts_on_unsolved | 1.53 | 1.00 | 0.53 | [0.14, 0.92] | 5/5 | **significant** |
| tokens_per_solved | 7,351 | 4,644 | 2,707 | [1,199, 4,215] | 5/5 | **significant** |
| wall_clock_per_solved_s | 66.82 | 36.08 | 30.74 | [12.07, 49.41] | 5/5 | **significant** |

Pre-registered rule: only rows marked **significant** are headline-eligible; every other delta is reported for completeness and may not appear in abstract / README / narrative claims.

## Appendix A — per-seed values

| Metric | mode | seed 0 | seed 1 | seed 2 | seed 3 | seed 4 |
|---|---|---|---|---|---|---|
| success_rate | governor | 70.0% | 65.0% | 55.0% | 60.0% | 55.0% |
| success_rate | naive | 65.0% | 60.0% | 55.0% | 70.0% | 60.0% |
| first_try_rate | governor | 60.0% | 55.0% | 50.0% | 60.0% | 55.0% |
| first_try_rate | naive | 60.0% | 60.0% | 55.0% | 70.0% | 60.0% |
| recovery_rate | governor | 25.0% | 22.2% | 10.0% | 0.0% | 0.0% |
| recovery_rate | naive | 12.5% | 0.0% | 0.0% | 0.0% | 0.0% |
| attempts_per_case | governor | 1.15 | 1.50 | 1.25 | 1.35 | 1.30 |
| attempts_per_case | naive | 1.05 | 1.00 | 1.00 | 1.00 | 1.00 |
| mean_attempts_on_unsolved | governor | 1.17 | 1.71 | 1.22 | 1.88 | 1.67 |
| mean_attempts_on_unsolved | naive | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| tokens_per_solved | governor | 7,234 | 8,475 | 8,551 | 6,216 | 6,278 |
| tokens_per_solved | naive | 5,167 | 4,517 | 4,461 | 4,621 | 4,453 |
| wall_clock_per_solved_s | governor | 48.80 | 54.76 | 85.76 | 76.52 | 68.25 |
| wall_clock_per_solved_s | naive | 32.84 | 39.00 | 40.48 | 29.96 | 38.10 |

## Appendix B — per-difficulty breakout

| Difficulty | cases | metric | governor | naive | Δ mean | Δ 95% CI |
|---|---|---|---|---|---|---|
| simple | 12 | success_rate | 65.0% | 70.0% | -5.0% | [-10.7%, 0.7%] |
| simple | 12 | attempts_per_case | 1.22 | 1.00 | 0.22 | [0.04, 0.39] |
| moderate | 6 | success_rate | 63.3% | 66.7% | -3.3% | [-30.3%, 23.6%] |
| moderate | 6 | attempts_per_case | 1.17 | 1.03 | 0.13 | [-0.04, 0.31] |
| challenging | 2 | success_rate | 30.0% | 0.0% | 30.0% | [-4.0%, 64.0%] |
| challenging | 2 | attempts_per_case | 2.30 | 1.00 | 1.30 | [0.74, 1.86] |

## Appendix C — case-level paired CI (supporting evidence only)

Per pre-registration: reported as robustness support, never a substitute for the seed-level CI. `delta_case` = per-case mean-over-seeds difference; CI over cases (df = n_cases − 1).

| Metric | Δ mean over cases | 95% CI | excludes zero |
|---|---|---|---|
| success_rate | -1.0% | [-12.6%, 10.6%] | no |
| first_try_rate | -5.0% | [-14.0%, 4.0%] | no |
| attempts_per_case | 0.30 | [-0.05, 0.65] | no |

## Appendix D — evaluator false-negative sensitivity (v4 §4)

Attempt-level false negative := comparator confirms the attempt's stdout matches gold rows AND the internal LLM evaluator rejected it.

| mode | FN / all attempts | FN / comparator-correct attempts | runs comparator-pass but runtime FAILED |
|---|---|---|---|
| governor | 0.0% | 0.0% | 0 |
| naive | 0.0% | 0.0% | 0 |

Paired per-seed FN-rate delta (governor − naive): mean 0.0%, 95% CI [0.0%, 0.0%] (5/5 seeds).

**Verdict: symmetric within noise.** Per the locked rule, paired subtraction cancels the common evaluator noise and the headline stands unqualified.

