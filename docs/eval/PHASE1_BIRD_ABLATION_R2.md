# Phase 1 — BIRD governor ablation (pre-registered)

> Generated 2026-07-11 13:13 UTC at commit `4954708`. Corpus + protocol lock: `docs/eval/PHASE1_CORPUS.md`; methodology lock: `docs/eval/PHASE0_METRICS.md` v4. Field-of-record for passed/failed: `reforge.runtime.sql.comparator` (KNOWN_LIMITATIONS L6). Raw records: `docs/eval/phase1_records_r2.jsonl`.

- Sample: 20 cases × 2 modes × 5 seeds = 200 runs; codegen model pinned: `deepseek-v4-pro`.
- Token coverage: 200/200 runs with known usage.

## Headline — per-seed paired deltas (governor − naive)

| Metric | governor | naive | Δ mean | Δ 95% CI | seeds used | verdict |
|---|---|---|---|---|---|---|
| success_rate | 61.0% | 61.0% | 0.0% | [-4.4%, 4.4%] | 5/5 | no significant effect (CI includes 0) |
| first_try_rate | 58.0% | 61.0% | -3.0% | [-10.1%, 4.1%] | 5/5 | no significant effect (CI includes 0) |
| recovery_rate | 6.5% | 0.0% | 6.5% | [-5.0%, 18.0%] | 5/5 | no significant effect (CI includes 0) |
| attempts_per_case | 1.32 | 1.00 | 0.32 | [0.24, 0.40] | 5/5 | **significant** |
| mean_attempts_on_unsolved | 1.74 | 1.00 | 0.74 | [0.60, 0.87] | 5/5 | **significant** |
| tokens_per_solved | 6,457 | 4,615 | 1,842 | [1,001, 2,683] | 5/5 | **significant** |
| wall_clock_per_solved_s | 57.79 | 37.57 | 20.22 | [12.55, 27.89] | 5/5 | **significant** |

Pre-registered rule: only rows marked **significant** are headline-eligible; every other delta is reported for completeness and may not appear in abstract / README / narrative claims.

## Appendix A — per-seed values

| Metric | mode | seed 0 | seed 1 | seed 2 | seed 3 | seed 4 |
|---|---|---|---|---|---|---|
| success_rate | governor | 65.0% | 60.0% | 65.0% | 60.0% | 55.0% |
| success_rate | naive | 65.0% | 60.0% | 65.0% | 55.0% | 60.0% |
| first_try_rate | governor | 65.0% | 50.0% | 60.0% | 60.0% | 55.0% |
| first_try_rate | naive | 65.0% | 60.0% | 65.0% | 55.0% | 60.0% |
| recovery_rate | governor | 0.0% | 20.0% | 12.5% | 0.0% | 0.0% |
| recovery_rate | naive | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| attempts_per_case | governor | 1.25 | 1.40 | 1.25 | 1.35 | 1.35 |
| attempts_per_case | naive | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| mean_attempts_on_unsolved | governor | 1.71 | 1.75 | 1.57 | 1.88 | 1.78 |
| mean_attempts_on_unsolved | naive | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| tokens_per_solved | governor | 6,386 | 7,389 | 6,532 | 6,036 | 5,941 |
| tokens_per_solved | naive | 4,732 | 4,454 | 4,653 | 4,381 | 4,853 |
| wall_clock_per_solved_s | governor | 58.15 | 62.66 | 53.90 | 55.34 | 58.91 |
| wall_clock_per_solved_s | naive | 33.94 | 34.31 | 34.08 | 41.94 | 43.60 |

## Appendix B — per-difficulty breakout

| Difficulty | cases | metric | governor | naive | Δ mean | Δ 95% CI |
|---|---|---|---|---|---|---|
| simple | 12 | success_rate | 65.0% | 71.7% | -6.7% | [-15.3%, 2.0%] |
| simple | 12 | attempts_per_case | 1.22 | 1.00 | 0.22 | [0.12, 0.31] |
| moderate | 6 | success_rate | 66.7% | 60.0% | 6.7% | [-16.9%, 30.3%] |
| moderate | 6 | attempts_per_case | 1.27 | 1.00 | 0.27 | [-0.01, 0.54] |
| challenging | 2 | success_rate | 20.0% | 0.0% | 20.0% | [-14.0%, 54.0%] |
| challenging | 2 | attempts_per_case | 2.10 | 1.00 | 1.10 | [0.42, 1.78] |

## Appendix C — case-level paired CI (supporting evidence only)

Per pre-registration: reported as robustness support, never a substitute for the seed-level CI. `delta_case` = per-case mean-over-seeds difference; CI over cases (df = n_cases − 1).

| Metric | Δ mean over cases | 95% CI | excludes zero |
|---|---|---|---|
| success_rate | -0.0% | [-10.5%, 10.5%] | no |
| first_try_rate | -3.0% | [-11.7%, 5.7%] | no |
| attempts_per_case | 0.32 | [-0.04, 0.68] | no |

## Appendix D — evaluator false-negative sensitivity (v4 §4)

Attempt-level false negative := comparator confirms the attempt's stdout matches gold rows AND the internal LLM evaluator rejected it.

| mode | FN / all attempts | FN / comparator-correct attempts | runs comparator-pass but runtime FAILED |
|---|---|---|---|
| governor | 0.0% | 0.0% | 0 |
| naive | 0.0% | 0.0% | 0 |

Paired per-seed FN-rate delta (governor − naive): mean 0.0%, 95% CI [0.0%, 0.0%] (5/5 seeds).

**Verdict: symmetric within noise.** Per the locked rule, paired subtraction cancels the common evaluator noise and the headline stands unqualified.

