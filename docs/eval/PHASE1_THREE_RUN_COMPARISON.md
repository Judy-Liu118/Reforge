# Phase 1 BIRD ablation — three-run comparison

> **Consolidation of existing records, not a new run.** Reads the three
> already-published rounds and lays them side by side; it introduces no new
> data. Same locked corpus (20 cases, `PHASE1_CORPUS.md`), same 2 arms × 5 seeds
> = 200 runs each, same pinned model `deepseek-v4-pro`. What changes between
> rounds is the **runtime under test**, never the questions. Per-round detail
> lives in the three source reports; the mechanism note is `KNOWN_LIMITATIONS.md`
> L3 / L6. This page exists to be read in one sitting.

## 1. Data sources

| round | date | commit | raw records | report | what changed vs previous |
|---|---|---|---|---|---|
| **R1** | 2026-07-11 | `69bc27a` | `phase1_records.jsonl` | `PHASE1_BIRD_ABLATION.md` | first run — **pre-calibration** evaluator |
| **R2** | 2026-07-11 | `4954708` | `phase1_records_r2.jsonl` | `PHASE1_BIRD_ABLATION_R2.md` | evaluator FN fix (held-out, `EVALUATOR_CALIBRATION.md`) |
| **R3** | 2026-07-13 | `bcc11fb` | `phase1_records_r3.jsonl` | `PHASE1_BIRD_ABLATION_R3.md` | added **L3 repeated-signature detector** |

The two arms (both rounds identical machinery, one env flag apart,
`benchmark/phase1/driver.py:255`):

- **governor** — full runtime: run → evaluator judges → retry-with-reflection if
  rejected → policy decides stop/continue.
- **naive** — `REFORGE_GOVERNOR_BYPASS=1`: run once, accept on `exit_code==0`,
  no evaluation, effectively single-shot. The baseline.

## 2. Field legend (what each row/column means)

All deltas are **governor − naive**, paired per seed (same seed feeds both arms),
aggregated over 5 seeds. Gold = the BIRD SQL comparator (`KNOWN_LIMITATIONS` L6).

| field | meaning | how computed (`benchmark/phase1/report.py`) |
|---|---|---|
| **success_rate** | fraction of cases solved (gold-correct), any attempt — **the thesis metric** | `success_rate`, L47 |
| **first_try_rate** | solved on attempt 1 (`passed ∧ attempts==1`) | `first_try_rate`, L51 |
| **recovery_rate** | of the cases *not* solved first try, fraction eventually solved by retry | `recovery_rate`, L55 |
| **attempts_per_case** | mean attempts per case (naive ≈ 1.0 = single-shot) | `attempts_per_case`, L62 |
| **tokens_per_solved** | prompt+completion tokens spent per solved case — the cost axis | `tokens_per_solved`, L73 |
| **Δ mean** | governor minus naive, mean of the 5 per-seed deltas | `paired_deltas` + `summarise` |
| **95% CI** | Student-t interval on that delta, `df = 5−1 = 4`, `t = 2.776` | `summarise`, `experience_multiseed.py:100` |
| **verdict** | **significant** iff the CI does not cross zero; else "no significant effect" | `_verdict`, L137 (pre-registered rule) |
| **FN rate** (§4) | evaluator false negatives ÷ comparator-correct attempts — how often it rejected a *correct* answer | `fn_rate_correct_attempts`, L159 |

Reading rule (pre-registered): the null on **success_rate** is the headline;
significant cost/attempt deltas mean "governor is more expensive," not "better."

## 3. Headline side-by-side (Δ = governor − naive)

| metric | R1 Δ [95% CI] | R2 Δ [95% CI] | R3 Δ [95% CI] |
|---|---|---|---|
| **success_rate** | +0.0pp [−4.4, +4.4] · null | +0.0pp [−4.4, +4.4] · null | −1.0pp [−9.1, +7.1] · null |
| first_try_rate | **−36.0pp** [−38.8, −33.2] · sig | −3.0pp [−10.1, +4.1] · null | −5.0pp [−9.4, −0.6] · sig* |
| recovery_rate | **+50.8pp** [+46.0, +55.5] · sig | +6.5pp [−5.0, +18.0] · null | +8.9pp [−2.7, +20.6] · null |
| attempts_per_case | +1.70 [+1.62, +1.78] · sig | +0.32 [+0.24, +0.40] · sig | +0.30 [+0.12, +0.48] · sig |
| tokens_per_solved | **+9,854** [+9.4k, +10.3k] · sig | +1,842 [+1.0k, +2.7k] · sig | +2,707 [+1.2k, +4.2k] · sig |

Absolute values (governor / naive): success_rate R1 65/65, R2 61/61, R3 61/62 %.
tokens_per_solved R1 14,449/4,594, R2 6,457/4,615, R3 7,351/4,644.

`*` R3 first_try is marginally significant (CI upper bound −0.6, ≈1 question);
see §5 — it cannot come from the L3 detector (which never fired) and is best read
as run-to-run seed noise.

## 4. Why R1 is not usable — evaluator false-negative asymmetry

The one row that separates R1 from R2/R3. FN = evaluator rejected an answer the
comparator confirmed correct.

| round | governor FN | naive FN | paired Δ | verdict |
|---|---|---|---|---|
| **R1** | **80.8%** | **52.3%** | +16.0pp [+11.0, +21.1] | **ASYMMETRIC** |
| R2 | 0.0% | 0.0% | 0.0pp [0, 0] | symmetric |
| R3 | 0.0% | 0.0% | 0.0pp [0, 0] | symmetric |

In R1 the pre-calibration evaluator rejected **80.8% of the governor arm's
correct attempts** (the length/contract-format bug fixed in
`EVALUATOR_CALIBRATION.md`, FN 42.7%→0% held-out). Because the FN pressure hits
the two arms *unequally*, R1's per-arm diagnostics are confounded — its headline
carries the ASYMMETRIC caveat and triggered the L6 revisit. R2/R3 have zero FN,
so their nulls are clean.

## 5. Per-round reading

**R1 — a contaminated first pass.** success_rate is already null (65 = 65), but
first_try shows governor 29% vs naive 65% and recovery 50.8% — *looks* like a
strong self-healing system. It is an artifact: the over-strict evaluator rejected
correct first attempts (80.8% FN), forcing retries, so correct answers stopped
counting as first-try and reappeared as "recoveries." The arithmetic closes:
governor 29% first-try + 71%×50.8% recovered = 65% — the same 65% naive reaches
in one shot. Governor did 3.1× the work to arrive where naive started.

**R2 — the clean null.** With the evaluator fixed (FN→0), governor first_try
jumps 29%→58% and recovery collapses 50.8%→6.5%; every governor−naive delta on
accuracy goes null and the sensitivity is symmetric. **This is the real result:
once the evaluator stops rejecting correct answers, governance buys no
success-rate gain over single-shot** — at 1.4× the tokens (6,457 vs 4,615).

**R3 — dormant detector, R2 reproduced.** The only addition, the L3
repeated-signature detector, measured **dormant on BIRD**: BIRD failures are
almost all quiet clean-exit wrong answers (30/31 retries quiet-triggered), loud
failures are near-absent (1 attempt) and never twice in a row, so the detector
had no raw material. Per `KNOWN_LIMITATIONS` L3, R3's governor made
"decision-for-decision the same choices the pre-L3 runtime would have made," and
the success_rate null reproduced. **From results, R3 ≈ R2.** (The one nominal
difference, first_try −5pp, is ~1 question, cannot be caused by the inert
detector, and sits within run-to-run noise — an observation, not a claim.)

## 6. Conclusions

1. **success_rate is null in all three rounds** — governance (retry-with-
   reflection) buys no accuracy over single-shot naive on this corpus.
2. **R1's apparent self-healing was an evaluator artifact**, dissolved once the
   42.7% false-negative bug was fixed (R2). Only R2/R3 are usable.
3. **Governor is consistently more expensive** — tokens_per_solved delta is
   significant every round (R2 +1.8k, R3 +2.7k) for zero accuracy gain. This is
   the compute-inefficiency the `PASS_AT_T_ANALYSIS.md` pass@t re-reading
   formalises.
4. **R3 adds no new confirmation of the null on BIRD** — its new detector is
   dormant here, so R3 is behaviourally R2. R3's real evidence is negative-space:
   the detector does *not* misfire on a corpus that cannot trigger it. Its value
   is confined to loud-and-persistent failures (Phase-0-style), not BIRD-style.

## 7. See also

- Per-round full reports: `PHASE1_BIRD_ABLATION{,_R2,_R3}.md`
- Evaluator FN fix: `EVALUATOR_CALIBRATION.md` · FP side: `EVALUATOR_FP_MEASUREMENT.md`
- Detector dormancy + edge: `KNOWN_LIMITATIONS.md` L3
- Compute-normalised (pass@t) re-reading: `PASS_AT_T_ANALYSIS.md`
