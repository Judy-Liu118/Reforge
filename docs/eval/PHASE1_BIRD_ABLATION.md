# Phase 1 — BIRD governor ablation (pre-registered)

> Generated 2026-07-11 05:38 UTC at commit `69bc27a`. Corpus + protocol lock: `docs/eval/PHASE1_CORPUS.md`; methodology lock: `docs/eval/PHASE0_METRICS.md` v4. Field-of-record for passed/failed: `reforge.runtime.sql.comparator` (KNOWN_LIMITATIONS L6). Raw records: `docs/eval/phase1_records.jsonl`.

- Sample: 20 cases × 2 modes × 5 seeds = 200 runs; codegen model pinned: `deepseek-v4-pro`.
- Token coverage: 200/200 runs with known usage.

## Headline — per-seed paired deltas (governor − naive)

| Metric | governor | naive | Δ mean | Δ 95% CI | seeds used | verdict |
|---|---|---|---|---|---|---|
| success_rate | 65.0% | 65.0% | 0.0% | [-4.4%, 4.4%] | 5/5 | no significant effect (CI includes 0) |
| first_try_rate | 29.0% | 65.0% | -36.0% | [-38.8%, -33.2%] | 5/5 | **significant** |
| recovery_rate | 50.8% | 0.0% | 50.8% | [46.0%, 55.5%] | 5/5 | **significant** |
| attempts_per_case | 2.70 | 1.00 | 1.70 | [1.62, 1.78] | 5/5 | **significant** |
| mean_attempts_on_unsolved | 2.86 | 1.00 | 1.86 | [1.63, 2.09] | 5/5 | **significant** |
| tokens_per_solved | 14,449 | 4,594 | 9,854 | [9,444, 10,264] | 5/5 | **significant** |
| wall_clock_per_solved_s | 118.40 | 36.70 | 81.70 | [72.24, 91.15] | 5/5 | **significant** |

Pre-registered rule: only rows marked **significant** are headline-eligible; every other delta is reported for completeness and may not appear in abstract / README / narrative claims.

## Appendix A — per-seed values

| Metric | mode | seed 0 | seed 1 | seed 2 | seed 3 | seed 4 |
|---|---|---|---|---|---|---|
| success_rate | governor | 65.0% | 65.0% | 70.0% | 65.0% | 60.0% |
| success_rate | naive | 65.0% | 70.0% | 65.0% | 65.0% | 60.0% |
| first_try_rate | governor | 30.0% | 30.0% | 30.0% | 30.0% | 25.0% |
| first_try_rate | naive | 65.0% | 70.0% | 65.0% | 65.0% | 60.0% |
| recovery_rate | governor | 50.0% | 50.0% | 57.1% | 50.0% | 46.7% |
| recovery_rate | naive | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| attempts_per_case | governor | 2.70 | 2.80 | 2.65 | 2.70 | 2.65 |
| attempts_per_case | naive | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| mean_attempts_on_unsolved | governor | 2.86 | 3.14 | 2.83 | 2.86 | 2.62 |
| mean_attempts_on_unsolved | naive | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| tokens_per_solved | governor | 14,221 | 14,446 | 14,921 | 14,060 | 14,595 |
| tokens_per_solved | naive | 4,571 | 4,643 | 4,511 | 4,495 | 4,752 |
| wall_clock_per_solved_s | governor | 122.12 | 123.68 | 108.02 | 113.29 | 124.89 |
| wall_clock_per_solved_s | naive | 39.97 | 37.06 | 38.67 | 32.00 | 35.78 |

## Appendix B — per-difficulty breakout

| Difficulty | cases | metric | governor | naive | Δ mean | Δ 95% CI |
|---|---|---|---|---|---|---|
| simple | 12 | success_rate | 75.0% | 70.0% | 5.0% | [-4.3%, 14.3%] |
| simple | 12 | attempts_per_case | 2.50 | 1.00 | 1.50 | [1.50, 1.50] |
| moderate | 6 | success_rate | 66.7% | 76.7% | -10.0% | [-21.3%, 1.3%] |
| moderate | 6 | attempts_per_case | 2.83 | 1.00 | 1.83 | [1.54, 2.13] |
| challenging | 2 | success_rate | 0.0% | 0.0% | 0.0% | [0.0%, 0.0%] |
| challenging | 2 | attempts_per_case | 3.50 | 1.00 | 2.50 | [1.88, 3.12] |

## Appendix C — case-level paired CI (supporting evidence only)

Per pre-registration: reported as robustness support, never a substitute for the seed-level CI. `delta_case` = per-case mean-over-seeds difference; CI over cases (df = n_cases − 1).

| Metric | Δ mean over cases | 95% CI | excludes zero |
|---|---|---|---|
| success_rate | 0.0% | [-5.3%, 5.3%] | no |
| first_try_rate | -36.0% | [-58.6%, -13.4%] | yes |
| attempts_per_case | 1.70 | [1.03, 2.37] | yes |

## Appendix D — evaluator false-negative sensitivity (v4 §4)

Attempt-level false negative := comparator confirms the attempt's stdout matches gold rows AND the internal LLM evaluator rejected it.

| mode | FN / all attempts | FN / comparator-correct attempts | runs comparator-pass but runtime FAILED |
|---|---|---|---|
| governor | 50.0% | 80.8% | 33 |
| naive | 34.0% | 52.3% | 34 |

Paired per-seed FN-rate delta (governor − naive): mean 16.0%, 95% CI [11.0%, 21.1%] (5/5 seeds).

**Verdict: ASYMMETRIC.** Evaluator false-negative pressure differs between arms; per the locked rule, every headline claim above must carry this caveat explicitly.

