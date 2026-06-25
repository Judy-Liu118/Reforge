# Phase 0 — Instrument calibration + locked metric definitions

> **Status**: PROPOSAL. Awaiting reviewer sign-off before any real-data run.
> Once approved, this file is the pre-registration record — every figure
> reported downstream cites the commit hash of this file at lock time.

This document does three things:

1. **Audits contamination** — were `governor` parameters ever tuned on a
   set that will later be used for evaluation?
2. **Verifies the instrument** — does `REFORGE_GOVERNOR_BYPASS=1`
   actually change the execution path, or is it a read-but-never-acted-on
   flag (the project has been burned by exactly this before).
3. **Locks metric definitions** — formulas, paired-vs-unpaired,
   per-seed-then-CI vs pooled, before any number gets shown to anyone.

---

## 1. HPO / parameter-tuning contamination audit

### Findings

Audited every file matching `max_retry|threshold|weight|param.*search|
optuna|grid_search` under `scripts/` and `reforge/`. The runtime carries
several **a-priori-set thresholds** wired into governor / evaluator
behavior. None were tuned on an eval task — but the eval chapter must
disclose what they are and how they were chosen, not claim there's
nothing to disclose. Verdict per knob:

| Governor / evaluator knob | Value | Where | How it was set | Tuned on eval? |
|---|---|---|---|---|
| `config.max_retry` | `3` | `reforge/config.py:18` | `MAX_RETRY` env var, prod default | No |
| `_PATTERN_THRESHOLD` (failure-pattern recognition) | `2` | `reforge/runtime/orchestration/governor/classify_stage.py:12` | Manually picked (≥2 same-type failures → pattern) | No |
| `MIN_OUTPUT_LENGTH` (eval heuristic) | `5` | `reforge/runtime/orchestration/evaluation/heuristics.py:40` | Manually picked. Flagged as a known limitation (see `docs/KNOWN_LIMITATIONS.md` L1) — fails legitimate short answers | No |
| Visual self-heal acceptance threshold | `0.85` | `reforge/runtime/skills/builtin/image_compare.py` prompt fragment + `test_prompts_self_heal_threshold.py` | Empirically settled on demo corpus (qwen-vl-max + UI reproduction); **not** the BIRD or Phase-2 set | No (corpus disjoint) |
| `_TRUTHY` bypass-flag set | constant | `retry_decision.py:31` | Implementation detail of env parsing | N/A |

`reforge/runtime/hpo/` is **not** an HPO over governor parameters —
it's an *application* of the runtime as a sklearn AutoML solver. Its
trial-budget loop sits **outside** the governor:

- inner loop (governor): "this attempt crashed → fix the syntax and retry"
- outer loop (HpoSession.run_case): "this pipeline scored 0.83 — pick a
  different one"

HpoSession runs against sklearn toy datasets (iris / wine /
breast_cancer / diabetes — see `docs/hpo_toy_bench.md`), which share
zero overlap with the BIRD SQL eval corpus or the Phase-2 pandas/CSV
corpus. There is no path by which HPO results contaminate evaluation.

### Defensible default

`max_retry = 3` is the production default; it has never been tuned on
any eval task. We use it unchanged across all phases. If a future need
arises to vary it (e.g., for axis-3 retry-policy ablation), the variants
are chosen *a priori* from {1, 3, 5} — not selected from eval results.

The eval-chapter disclosure will read approximately:
> Several runtime thresholds (`max_retry=3`, `_PATTERN_THRESHOLD=2`,
> `MIN_OUTPUT_LENGTH=5`, vision self-heal `0.85`) are set a priori —
> production defaults or empirical settlements on corpora disjoint from
> the eval sets. None were searched against BIRD or the Phase-2 task
> set. Phase-2 axis-3 compares a priori choices {0 retries, naive ×N,
> governor RETRY} — not a tuned policy.

---

## 2. Instrument verification — does `REFORGE_GOVERNOR_BYPASS` really bypass?

### Code path verified

`reforge/runtime/orchestration/graph/nodes/retry_decision.py:71-94` —
when `REFORGE_GOVERNOR_BYPASS=1`, the `ExecutionGovernor.resolve(state)`
call is replaced with `_naive_resolution(state)`. The latter:

- emits `failure_mode=""` (no typed classification)
- emits `task_intent=""` (no intent inference)
- emits `is_expected_failure=False` (no expected-failure recognition)
- emits `reason="naive: …"` (governor never uses the `naive:` prefix)
- has only three branches: `exit==0 → ACCEPT`, `exit!=0 + budget left
  → RETRY`, `exit!=0 + budget out → STOP`

The bypass test (`reforge/tests/test_governor_bypass.py`) pins all
four contracts above, so any future regression that re-introduces
governor behavior under the bypass flag would fail CI.

### Phase 0 calibration corpus (5-10 trivial tasks)

Before any real Phase-1/2 run, we execute a tiny trivial corpus under
both modes and verify the *instrument*, not the conclusion. The gate
checks **mechanism only** — whether the path swap, seed plumbing,
pairing, and metric pipeline are wired right. **Result direction
(e.g., "bypass ≤ governor") is NOT a gate** — that would be
circular: it would gate the experiment on its own conclusion.

| Check | What it verifies | Pass condition |
|---|---|---|
| `mode=naive`: every passing run logs `policy_reason` starting with `naive:` | path swap actually happened | 100% |
| `mode=governor`: every run logs a non-empty `failure_mode` or `task_intent` on failed attempts | governor pipeline ran | 100% |
| `mode=governor` on the decoy toy: at least one run emits a non-budget-exhaustion STOP (`is_expected_failure=True` or terminal classification) | STOP-path code is reachable, not dead | ≥1 occurrence |
| Seed variation: ≥2 of 3 calibration seeds produce a different decision trace on at least one task | seeds actually plumb through the LLM call layer | yes |
| Paired diff: per (case, seed) pair, the two modes share `case_id` and seed key; record counts align | paired aggregation will be valid downstream | 100% |
| All Tier-A metrics computable on the calibration runs (no NaN / divide-by-zero / missing field) | metric formulas survive contact with the real state shape | 100% |

Calibration corpus construction: 5 from BIRD-easy + 3 hand-built toys
exercising an intentional first-try failure (e.g., the `$1,234.56`
coercion Phase 2 uses at scale) + **≥1 unsolvable decoy toy** (e.g.,
"GET this nonexistent URL and report the response") so the
governor's STOP path gets exercised in the gate. This is *not* an
evaluation set — it's a fixture, discarded for reporting.

**Calibration deliverable**: `docs/eval/PHASE0_CALIBRATION.md` —
the table above with concrete numbers, plus a "go / no-go" line. If
any *mechanism* check fails, we don't enter Phase 1. Result-direction
numbers (who solved more) are reported but never gate.

---

## 3. Locked metric definitions

All metrics are computed against runs that came out of the same
`SqlBenchSession.run()` / Phase-2-driver path, so `attempts`,
`runtime_outcome`, `eval_score`, and the oracle-side `correct` boolean
are all available on every run record.

### Notation

- `N_seeds` — number of independent seeds. **Locked:**
  - **Headline axis 1 (governor on/off): `N_seeds = 5`.** df=2 (N=3)
    Student-t critical value is 4.30 vs 2.78 at df=4 — CI half-width
    nearly doubles for the same std; a headline can't survive that.
  - **Secondary axes (memory on/off, retry-policy): `N_seeds = 3`.**
    Already aligns with the existing experience-memory harness's
    default. Effects there are auxiliary, not headline.
  - **Robustness appendix**: if a per-seed delta's CI95 over seeds is
    inconclusive but consistent in sign, we additionally report
    **case-level paired CI** — for each case, take `delta_case =
    metric(governor, case)_mean_over_seeds − metric(naive,
    case)_mean_over_seeds`, then summarise across cases (df = N_cases
    − 1, typically ≫ N_seeds). This is reported as supporting
    evidence, never as a substitute for the seed-level CI.
- `N_cases` — number of cases in the set being reported.
- `mode ∈ {governor, naive}` — `REFORGE_GOVERNOR_BYPASS` off / on.
- `passed(run)` — oracle says the run's output matches ground truth.
  On BIRD: `comparator.compare_results(predicted_rows, expected_rows)`.
  On Phase 2: comparison of stdout against the case's pre-baked
  `expected_output` string.
- `attempts(run)` — `state.control_state.retry_count + 1`.
- `first_try(run)` — `attempts(run) == 1 AND passed(run)`.

### Pairing rule

For axis-1 (governor on/off), the report unit is a **per-seed paired
delta**:

```
delta_seed[i] = metric(governor, seed=i)  −  metric(naive, seed=i)
```

with `metric(·, seed=i)` averaged over all `N_cases`. Aggregate across
seeds with the existing `MultiSeedDriver.summarise()` → mean, std,
95% CI half-width via Student-t (df = `N_seeds - 1`), plus
`excludes_zero` flag. The aggregate level is "per seed first, then CI
over seeds", which is what the existing harness does and which the
README's experience-memory result already uses.

### Tier A — computable on BIRD (no oracle on recoverability)

| Metric | Formula | Reports |
|---|---|---|
| **Success rate** | `sum(passed(r)) / N_cases` | Per-seed value + paired delta + CI |
| **First-try rate** | `sum(first_try(r)) / N_cases` | Per-seed value + paired delta + CI |
| **Recovery rate** | `sum(passed(r) AND attempts(r) > 1) / sum(NOT first_try(r))`. Denominator is **first-try-failure cases for that mode-seed**. The *formula* is mode-independent (each mode-seed computes its own rate without referencing the other arm), so per-seed paired deltas are well-defined. The case-seed denominator value can and does differ between modes — that's expected, since each mode has its own first-try success pattern. | Per-seed value + paired delta + CI |
| **Attempts per case** | `sum(attempts(r)) / N_cases` | Per-seed value + paired delta + CI. *Lower = less work spent overall.* |
| **Mean attempts on unsolved** | `sum(attempts(r) for r where NOT passed(r)) / sum(NOT passed(r))`. How much budget the runtime burned on cases it ultimately failed. Replaces the earlier ambiguous `wasted_attempts` — this version doesn't conflate "spent budget on a winner" with "spent budget on a loser". | Per-seed value + paired delta + CI. If a mode has zero unsolved cases, the metric is reported as N/A for that mode-seed cell, not zero. |
| **Tokens per solved** | `sum(tokens(r) for r in solved_with_known_tokens) / max(count(solved_with_known_tokens), 1)`. Where `tokens(r)` is `prompt_tokens + completion_tokens` accumulated across all LLM calls in the run by the harness-side accumulator (`reforge.observability.llm_events.token_accounting`). See sentinel rule below. | Per-seed value + paired delta + CI. Also report the excluded-run count for transparency. |
| **Cost per solved** | `tokens_per_solved × $/M-tok`, with `$/M-tok` posted in the eval chapter (model-pinned). | Same |
| **Wall-clock per solved** | `sum(duration_ms(r)) / max(sum(passed(r)), 1) / 1000` seconds. | Same |

**Sentinel rule for `usage=None` (pre-registered).** When a provider
doesn't populate `response.usage`, the LLM client emits
`prompt_tokens = -1` and `completion_tokens = -1` for that call. The
accumulator must NOT silently add `-1` (which would underflow and
spuriously inflate apparent throughput). It marks the run's token
totals as `unknown=True`. Reporting rule:

- Runs with `unknown=True` are **excluded from the
  `tokens_per_solved` numerator and denominator** — not zeroed.
- Each reported `tokens_per_solved` line MUST also carry the
  excluded-run count (e.g., "12,400 tokens/solved, n=37, 3 excluded
  (unknown usage)").
- If excluded-run fraction exceeds 20% for any (mode, seed) cell, the
  metric is flagged as low-confidence and not used as a headline.

**Scope of token coverage.** The harness-side accumulator captures
all LLM calls that flow through `LLMClient._dispatch` — primary text
codegen, reflection, planner ×2, decomposer, task_intent, and
multimodal codegen via `LLMClient.chat_multimodal()`. Vision skills
(`vision_describe`, `compare_images`) currently instantiate `OpenAI`
directly and bypass the hook — see `docs/KNOWN_LIMITATIONS.md` L2.
Because the BIRD SQL corpus and the Phase-2 pandas/CSV corpus contain
no image inputs, the planning LLM does not invoke these skills, and
coverage on the measured paths is 100%. If a future axis introduces
image-bearing tasks (e.g., UI reproduction), the vision skills must
be re-routed through `LLMClient` before that axis can ship measured.

### Tier B — requires Phase-2's recoverability oracle

These three need a ground-truth `task_kind ∈ {recoverable, decoy}` per
case, which only Phase 2 provides (BIRD has no notion of "unsolvable
by design"). We list them here so the definitions are locked early.

**Definition: deliberate STOP.** Throughout Tier B we distinguish two
kinds of STOP:
- **Deliberate STOP** — issued *while budget remains* via the typed
  classification path (`is_expected_failure=True` or a terminal
  failure_mode that the governor recognizes as unrecoverable). This
  is the governor saying "more attempts won't help."
- **Budget-exhausted STOP** — issued because `retry_count == max_retry`.
  Both modes hit this; it is not evidence of judgement.

Naive baseline has **zero deliberate STOPs by construction** — it only
issues STOP when budget runs out. That asymmetry is exactly the
ablation surface, not a measurement bug.

**Decoy diversity constraint (pre-registered).** For the
deliberate-STOP metrics to measure "recognizing unrecoverability" as a
general capability — rather than "recognizes `.invalid` URLs" — the
Phase-2 decoy slice MUST cover **≥3 distinct root-cause categories**,
no single category exceeding ~40% of decoys. Locked categories:

1. **Resolver / dependency failure** (e.g., RFC2606 `.invalid` host,
   import of non-existent module).
2. **Logically unsatisfiable constraint** (e.g., "find a prime < 4
   that is even and > 2").
3. **Missing environment dependency** (e.g., requires a credential /
   file / network resource not present in the sandbox).
4. **Self-contradictory requirements** (e.g., "sort ascending and
   simultaneously preserve original order, do not stable-sort").

A trivial grep-style rule (e.g., `if ".invalid" in request: STOP`)
must NOT achieve the headline deliberate-STOP precision. We will
spot-check this by computing the precision a naive keyword-rule
baseline would achieve on our decoy mix; if that ceiling is >50% the
mix is too easy and gets rebalanced before Phase 2 runs.

| Metric | Formula | Notes |
|---|---|---|
| **False-retry rate** | `count(decoy cases with ≥1 RETRY decision) / count(decoy cases)`. Counted per *case*, not per decision, so larger budgets don't inflate the rate. On a decoy, any RETRY is wasted budget. | Reported only on Phase-2's decoy slice. Bypass naturally retries until budget exhaustion → near-100% by construction. Governor's lift is the gap. |
| **False-stop rate (paired)** | `count(recoverable cases where naive(seed) solved within full budget AND governor(seed) issued a deliberate STOP AND did NOT solve) / count(recoverable cases where naive solved)`. Uses naive's success at full budget as the **proof of recoverability** on a per-(case,seed) basis — i.e., the case is recoverable *in this run* because the other arm just demonstrated it. This is paired evidence; it does not depend on a synthetic ground-truth label of "recoverable." | Reported only on Phase-2's recoverable slice. Pair-conditional: the case-seed cell only contributes if naive solves it. |
| **Deliberate-STOP precision** | `count(deliberate STOPs on decoy cases) / count(deliberate STOPs)`. The positive class is deliberate STOPs only — budget-exhaustion STOPs are excluded from both numerator and denominator. | Reported on Phase-2 only. Naive's denominator = 0 by construction → metric N/A for naive, which is the differentiation, not a problem. |
| **Deliberate-STOP recall** | `count(deliberate STOPs on decoy cases) / count(decoy cases)`. Of all unrecoverable cases, what fraction did the runtime correctly recognize *before* budget ran out. | Same. Naive's recall = 0 by construction. |
| **Deliberate-STOP calibration** | If the governor exposes a confidence (`is_expected_failure=True` as high-confidence vs a generic terminal classification as low-confidence), bin and check accuracy vs `task_kind ∈ {decoy, recoverable}`. If no confidence axis is exposed, report a single (precision, recall) point and note the absence. | Final format decided after auditing the actual `RuntimeResolution` fields on real Phase-2 runs. |

### Significance decision rule (pre-registered)

**Headline claims require the paired-delta 95% CI to not cross zero.**
Any paired delta whose 95% CI includes zero is reported as
*"no significant effect (CI includes 0)"* and may NOT be used as a
headline, an abstract bullet, a README hero line, or a directional
narrative statement ("governor helps / hurts X"). It can still appear
in tables and appendices for completeness.

The case-level paired CI (robustness appendix) is *supporting*
evidence, never a substitute. A delta that is inconclusive at the seed
level but conclusive at the case level is described as "directionally
consistent across cases but seed-CI inconclusive" — never promoted to
a headline on the strength of the case-level CI alone.

This rule is locked **before any real-data run**. We are committing
to it without having seen a single eval number; switching to a looser
rule after the fact would be p-hacking by another name.

### Reporting hygiene

- **Sample size**: every figure cites `N_cases × N_seeds`.
- **CI**: every delta cites the 95% CI half-width. If the CI crosses
  zero we say so explicitly — we do not bold the mean.
- **Per-difficulty breakout** (BIRD only): the headline table averages
  over difficulty; an appendix table splits by `{easy, medium,
  challenging}`.
- **Wallclock / token**: reported as paired delta + raw governor-mode
  number so the reader can sanity-check magnitude.
- **Negative results**: if `delta` is non-significant we keep the
  number in the table with an explicit "(CI includes 0)" tag — not
  removed.

### Out of scope (deliberately)

- Absolute leaderboard placement on BIRD. We are doing an ablation,
  not a leaderboard run, and the model is pinned (not best-of-K).
- Multi-axis interaction effects (governor × memory) in Phase 1. The
  experience-memory harness already covers memory on/off in isolation;
  combining the axes is Phase 2/3 if and only if Phase 1 motivates it.
- Tuning *any* of the metric thresholds (e.g., the 0.92 visual
  self-heal threshold) on Phase-2 data. Those are locked from the
  prior memory snapshot.

---

## Revision log (v2 — review round 1)

Changes from v1 in response to reviewer corrections:

1. **`recovery_rate` denominator** → `sum(NOT first_try(r))`, which is
   mode-independent at the case-seed level (paired delta well-defined).
2. **Calibration gate** no longer includes a result-direction check;
   `bypass ≤ governor` removed (was circular). Decoy-toy added so the
   STOP path is exercised.
3. **STOP precision/recall positives** restricted to **deliberate
   STOPs** (budget remaining + typed classification). Budget-exhaustion
   STOPs excluded from both num and den. Naive's denominator/recall =
   0 by construction is the *signal*, not a defect.
4. **`false_stop` is now paired-evidence**: a recoverable case
   qualifies for the numerator only if its naive-arm twin solved within
   full budget. No synthetic "recoverable" label needed.
5. **`wasted_attempts` removed** (ambiguous). Replaced by
   `attempts_per_case` (already present) + `mean_attempts_on_unsolved`
   (new) which separates winner-budget from loser-budget cleanly.
6. **Contamination disclosure** now lists actual a-priori-set thresholds
   (`max_retry=3`, `_PATTERN_THRESHOLD=2`, `MIN_OUTPUT_LENGTH=5`,
   visual self-heal `0.85`) instead of the false "none exposed" claim.
7. **`N_seeds = 5` for headline axis 1** (was 3). df=4 vs df=2 nearly
   halves the t-critical, so the headline can survive realistic
   per-seed std. Secondary axes stay at 3. Case-level paired CI added
   as robustness appendix.
8. **Significance decision rule** locked: headline claims require
   paired-delta 95% CI to not cross zero. Anything else is reported as
   "no significant effect (CI includes 0)" and is forbidden from
   abstract / README / headline copy. Locked before seeing any number.
9. **Decoy diversity** locked at ≥3 root-cause categories, no single
   category >40% — so deliberate-STOP precision measures "recognizing
   unrecoverability" rather than "recognizes `.invalid`". Naive
   grep-rule precision spot-check added as sanity gate (if a keyword
   rule clears >50%, the mix is rebalanced).
10. **Token accounting**: locked at harness side via
    `reforge.observability.llm_events.token_accounting(case_id, seed)`
    context manager (contextvars-keyed, measurement-only, does NOT
    touch `RuntimeState` / governor decision path / vision skills).
    `usage=None → -1 sentinel` rule pre-registered: such runs are
    excluded (not zeroed) from `tokens_per_solved`; excluded-run count
    is reported alongside; >20% exclusion → no headline. Vision-skill
    coverage gap documented as KNOWN_LIMITATIONS L2; eval-chapter
    scope sentence: token coverage = 100% of measured corpora because
    BIRD/pandas-CSV contain no image inputs.

## Status

**Signed off (v2 + sig-rule + decoy-diversity, this commit).** This
file is the pre-registration record. The commit hash of this file at
sign-off is the reference any downstream eval-chapter number must
cite. Subsequent edits are tracked via the revision log above; any
post-data edit that loosens a metric or relaxes the significance rule
must be flagged in the eval chapter as a post-hoc change with the
reason it was made.

Phase-0 calibration decoy: `http://example.invalid/...` (RFC2606
reserved TLD, resolver-layer determinism) — accepted.

Open prerequisite before Phase 1: token-accounting access on
`RuntimeState`. To be audited read-only next; results reported as a
fact, then a decision made on whether the instrumentation PR is
required as a Phase-1 blocker.
