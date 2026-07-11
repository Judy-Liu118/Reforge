# Evaluator calibration — held-out fix for the L6 false-negative pressure

> Executed 2026-07-11, after Phase 1 (`PHASE1_BIRD_ABLATION.md`) fired the
> KNOWN_LIMITATIONS L6 trigger-to-revisit (ASYMMETRIC evaluator
> false-negative verdict). This document records the diagnosis, the fix,
> and the held-out validation that gates re-running the governor-vs-naive
> axis. Reproduce with `scripts/calibrate_evaluator_heldout.py`.

## 1. What Phase 1 observed

The internal evaluator (`HeuristicEvaluator` — rule-based, not an LLM)
rejected 80.8% of the governor arm's comparator-correct attempts
(52.3% in the naive arm; paired Δ +16.0pp, 95% CI [+11.0, +21.1]).
The governor retries on evaluator rejection, so the retry loop mostly
re-solved already-solved cases at 3.1× tokens-per-solved for a null
success_rate delta.

## 2. Diagnosis (in-sample, attribution only)

Offline replay of all 169 Phase 1 false-negative attempts against the
evaluator (reconstructing its inputs from the recorded
`attempt_observations`) reproduced the recorded score in 167/169 cases
and attributed **100% of the false negatives to length-based checks**:

| failed checks | n | example stdout |
|---|---|---|
| `output_not_empty` only | 99 | `"5\n"` — correct scalar, 1 char < `MIN_OUTPUT_LENGTH=5` |
| `output_not_empty` + `output_contains_data` | 70 | `"-\n"` — correct non-numeric cell, no digit |

The SQL task prompt *itself* pins the output shape — "print rows one per
line … **Print nothing else** (no headers, no preamble, no trailing
summary)" — so a one-character stdout is contract compliance, which the
evaluator was scoring as a defect. No other check contributed.

## 3. The fix

`HeuristicEvaluator` now detects an **explicit output contract** in the
request (generic phrases: "print nothing else", "output only …",
"print only …", "no preamble", 只输出 / 只打印 / 不要输出其他). Under a
contract, the length/digit plausibility checks are suspended:

- `output_not_empty`: short-output floor drops from 5 chars to 1 —
  emptiness still fails;
- `output_contains_data` and `research_output_quality`: skipped — the
  contract defines what the output must look like.

Everything else is untouched: tracebacks, stderr, non-zero exit codes,
anti-cheating checks (blanket except, AST guard, retry integrity,
artifact freshness) behave exactly as before. The contract phrases are
generic output-format language, not strings from any eval question.

## 4. Held-out validation protocol (locked before running)

- **Population**: the locked Phase 1 pool (dialect-fn filter +
  calibration-question exclusion per `PHASE1_CORPUS.md`) **minus the 20
  Phase 1 picks** → 1360 questions. The 20 picks and 5 calibration
  questions are never touched, per the L6 anti-pattern ("fix the
  evaluator on held-out data, not on this corpus").
- **Sample**: 300 questions, `random.Random(20260711)` (differs from the
  corpus seed 20260710).
- **Positive cases**: run the *gold* SQL, synthesize stdout in the exact
  contract format (` | `-joined fields, `NULL` for None). Correct by
  construction — a calibrated evaluator must accept it.
- **Negative controls** (rejection integrity, per question): empty
  stdout; a traceback-shaped stdout; the correct stdout with
  `exit_code=1`. All must stay rejected before AND after.
- **"Before" arm**: identical evaluator with only the contract gate
  disabled — the gate is the sole behavioral delta of the fix.

## 5. Results

| | before fix | after fix |
|---|---|---|
| FN rate on correct contract-format output (n=300) | **42.7%** (128/300) | **0.0%** (0/300) |
| rejection integrity failures (3 × 300 controls) | 0 | 0 |

- 300/300 gold SQL ran clean; no empty gold result sets in the sample.
- The 42.7% before-rate shows the misfire is population-wide, not a
  quirk of the 20 Phase 1 picks.
- Descriptive (in-sample, not evidence): replaying the 169 Phase 1 FN
  attempts under the fixed evaluator leaves 0 false negatives.

## 6. What this does and does not license

- ✅ The L6 gating fix is validated held-out; the governor-vs-naive axis
  is cleared to re-run under the same locked corpus and protocol
  (`PHASE1_CORPUS.md` — the corpus lock is unchanged).
- ❌ No headline may be updated from replays of the *old* Phase 1
  records: the evaluator drives the governor's runtime behavior
  (retry/stop decisions), so only a fresh run measures the fixed system.
- ❌ The false-*positive* side (evaluator accepts a comparator-wrong
  answer; 28 attempts in Phase 1) is out of scope here — a rule-based
  evaluator without gold access cannot verify semantic correctness.
  That remains the comparator's job as field-of-record (L6).
