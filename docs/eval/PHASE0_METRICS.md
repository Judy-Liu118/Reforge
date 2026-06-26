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
| `mode=governor` on D1″ (timeout decoy): at least one run emits `action == "STOP"` with `state.classification_result.failure_mode == "timeout"` and `state.control_state.retry_count < config.max_retry` | timeout deliberate-STOP code path reachable, not dead. `failure_mode` is intent-independent (set in `classifier.py:36-40` before any task_intent branch), so the gate stays robust even if IntentStage re-classifies the prompt. | ≥1 occurrence |
| Seed variation: ≥2 of 3 calibration seeds produce a different decision trace on at least one task | seeds actually plumb through the LLM call layer | yes |
| Paired diff: per (case, seed) pair, the two modes share `case_id` and seed key; record counts align | paired aggregation will be valid downstream | 100% |
| All Tier-A metrics computable on the calibration runs (no NaN / divide-by-zero / missing field) | metric formulas survive contact with the real state shape | 100% |

Calibration corpus construction: 5 from BIRD-simple + 3 hand-built
toys exercising an intentional first-try failure (T1 eval-driven
silent-wrong; T2/T3 execution-error recovery) + **D1″** (timeout
decoy: an infinite-loop prompt that hits `EXECUTION_TIMEOUT=30s`
watchdog) so the timeout deliberate-STOP path is exercised at the
gate. The locked corpus and rationale live in
`docs/eval/PHASE0_CORPUS.md`. This is *not* an evaluation set — it's
a fixture, discarded for reporting.

> **Note on STOP-path coverage.** Phase 0 calibration probes only the
> `failure_mode == "timeout"` deliberate-STOP sub-path. The
> `terminal_intentional_failure` sub-path
> (`is_expected_failure=True AND retryable=False`) fires only when
> `IntentStage` classifies the user's request text as
> `EXPECTED_ERROR` / `TRACEBACK_DEMO`; deliberately constructing such
> prompts would leak intent into the calibration corpus. The
> architectural gap (governor has no history-based unrecoverability
> detector) is documented in `docs/KNOWN_LIMITATIONS.md` L3 and
> motivated the v3 narrowing of Tier B (next section).

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

### Tier B — DEFERRED (out of current governor scope)

> **Status (v3): deferred.** The governor classify/policy audit
> (`reforge/runtime/classification/classifier.py:24-75`,
> `reforge/runtime/policy/retry_policy.py:19-53`,
> `reforge/runtime/orchestration/governor/classify_stage.py`,
> `reforge/runtime/policy/task_intent.py`) confirmed there is no
> history-based unrecoverability detector. Deliberate STOPs fire only
> from `is_expected_failure=True AND retryable=False` (set exclusively
> when IntentStage's LLM classifies the *user request text* as
> `EXPECTED_ERROR` / `TRACEBACK_DEMO`) or `failure_mode == "timeout"`
> (set when sandbox watchdog kills the process). All other decoys —
> resolver failure, missing env dep, logically unsatisfiable,
> self-contradictory — collapse to
> `execution_error → retryable=True → RETRY → budget exhaustion`.
>
> Tier B's `false-stop`, `deliberate-STOP precision`,
> `deliberate-STOP recall`, and `deliberate-STOP calibration` metrics
> all presume a recognizer that does not exist. Reporting them on
> Phase-2 decoys would yield near-zero numbers that measure an absent
> feature, not the runtime's actual capability. They are deferred
> until / unless the governor gains a pattern-based or learned
> unrecoverability detector (the architectural gap is recorded in
> `docs/KNOWN_LIMITATIONS.md` L3).
>
> **The decoy-diversity constraint (≥3 root-cause categories, no
> single >40%) is also dropped** — under the current governor, decoy
> "category" is not a measurement axis the runtime can distinguish,
> so the constraint would test corpus composition without coupling to
> any runtime behavior.
>
> **What Phase 2 retains from Tier B**: nothing. The Phase 2 headline
> collapses to a single pillar (recovery quality, see below). The
> v3-draft "Timeout-class deliberate-STOP efficiency" narrow headline
> is **also deferred** in v4 after the calibration falsified its
> baseline assumption — see v4 revision log below and
> `docs/KNOWN_LIMITATIONS.md` L5. `false-retry rate` was already
> dropped in v3 §8 for redundancy; that decision stands.
>
> **What Phase 2 stops claiming**: that governor recognizes *generic*
> unrecoverability across diverse failure root causes. That claim was
> upstream of an unverified capability. The eval chapter will state
> the narrowed scope explicitly.

The original Tier B definitions below are kept for reference so that
a future governor change which adds an unrecoverability detector can
re-enable them without re-deriving the formulas. They are NOT
reported in the current eval chapter.

<details>
<summary>Original Tier B definitions (deferred, retained for reference)</summary>

These three need a ground-truth `task_kind ∈ {recoverable, decoy}` per
case, which only Phase 2 provides (BIRD has no notion of "unsolvable
by design").

**Definition: deliberate STOP.** Throughout Tier B we distinguish two
kinds of STOP:
- **Deliberate STOP** — issued *while budget remains* via the typed
  classification path (`is_expected_failure=True` or a terminal
  failure_mode that the governor recognizes as unrecoverable).
- **Budget-exhausted STOP** — issued because `retry_count == max_retry`.

| Metric | Formula | Notes |
|---|---|---|
| **False-stop rate (paired)** | `count(recoverable cases where naive(seed) solved within full budget AND governor(seed) issued a deliberate STOP AND did NOT solve) / count(recoverable cases where naive solved)`. Uses naive's success at full budget as the **proof of recoverability** on a per-(case,seed) basis. | Pair-conditional: the case-seed cell only contributes if naive solves it. |
| **Deliberate-STOP precision** | `count(deliberate STOPs on decoy cases) / count(deliberate STOPs)`. | Naive's denominator = 0 by construction → metric N/A for naive. |
| **Deliberate-STOP recall** | `count(deliberate STOPs on decoy cases) / count(decoy cases)`. | Naive's recall = 0 by construction. |
| **Deliberate-STOP calibration** | If the governor exposes a confidence axis, bin and check accuracy vs `task_kind`. | Final format decided after auditing the actual `RuntimeResolution` fields on real Phase-2 runs. |

Original decoy-diversity constraint (deferred): ≥3 root-cause
categories spanning resolver failure, logically unsatisfiable, missing
environment dependency, self-contradictory requirements.

</details>

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
  over difficulty; an appendix table splits by `{simple, moderate,
  challenging}` (BIRD's actual labels — earlier drafts said
  `{easy, medium, challenging}` which was incorrect).
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

## Revision log (v3 — Phase-0 corpus governor audit)

Changes from v2 after auditing the governor's classify/policy code
paths to validate that the originally proposed D1′ (FileNotFoundError
decoy) would actually trigger deliberate-STOP. Audit conclusion: it
would NOT. The governor has no history-based unrecoverability
detector, so any NORMAL_EXECUTION-intent task whose first attempt
yields non-timeout `exit_code != 0` is RETRY'd until
`retry_count == max_retry` → `retry_limit_reached_*` STOP
(budget-exhausted, NOT deliberate). Deliberate STOPs fire only from
(a) `is_expected_failure=True AND retryable=False` — set exclusively
when IntentStage classifies the *user request text* as
`EXPECTED_ERROR` / `TRACEBACK_DEMO`; or (b) `failure_mode == "timeout"`
— set exclusively when sandbox watchdog kills the process.

Decisions taken (option α + β; option γ — governor surface extension —
deliberately not taken):

1. **Phase-0 deliberate-STOP probe rebased** from D1′
   (FileNotFoundError) to **D1″** (timeout: `Loop forever printing
   tick` → `EXECUTION_TIMEOUT=30s` watchdog →
   `failure_mode == "timeout"` → deliberate STOP). Probes the
   `retry_policy.py:34-35` branch. The terminal_intentional sub-path
   is structurally out of calibration scope (any prompt that fires it
   would leak intent into the corpus).
2. **Tier B (false-stop, deliberate-STOP precision/recall,
   calibration) marked DEFERRED**, with a banner explaining the
   architectural gap and the narrowed Phase-2 surface. Original
   definitions retained in a collapsed block for future re-enablement.
3. **Decoy-diversity constraint (≥3 root-cause categories, no single
   >40%) dropped**. Under the current governor, non-timeout decoy
   "category" does not influence runtime behavior, so the constraint
   would gate corpus composition without coupling to a measured
   capability.
4. **Phase 2 thesis narrowed**. Headline claim becomes "governor's
   typed classification + memory-driven retry-hint improves recovery
   on recoverable failures" (recovery rate, attempts on solved,
   tokens per solved). Separate narrow efficiency claim: "on
   timeout-class failures, deliberate STOP saves attempts /
   wall-clock / tokens vs naive's blind retry-to-budget." Phase 2
   no longer claims generic unrecoverability recognition. The
   `false-retry rate` is retained as a documented baseline-vs-governor
   comparison, but its interpretation is explicitly narrowed.
5. **`BIRD-easy` → `BIRD-simple`** throughout. BIRD's difficulty
   labels are `{simple, moderate, challenging}`; the v1/v2
   `BIRD-easy` was a misnomer.
6. **Calibration STOP-path gate rescoped** to
   `policy_reason == "timeout"` on D1″ (was: `is_expected_failure=True
   OR terminal classification` on an unspecified decoy).
7. **Architectural gap recorded** as `docs/KNOWN_LIMITATIONS.md` L3
   (STOP scope is intent + timeout, no history-based detector).
   Considered a pattern-based detector (≥N same-signature tracebacks
   → flip `retryable=False`); rejected for now because its precision
   on real workloads is unverified (same FileNotFoundError next
   attempt CAN recover if the codegen writes the file or changes the
   path) and adopting it would change the system-under-test
   mid-experiment.
8. **Pre-commit cross-check (three findings, pre-freeze)**:
   - `false-retry rate` **dropped** (was tentatively "retained,
     narrowed" in the v3 draft). On a timeout-only decoy slice it is
     a binary restatement of the timeout-deliberate-STOP efficiency
     point; on non-timeout decoys both modes are at ~100% with no
     measurable delta. Removed rather than left as a residual
     between Tier A and Tier B.
   - Calibration STOP-path gate field changed from
     `policy_reason == "timeout"` to
     `state.classification_result.failure_mode == "timeout"`.
     `policy_reason` is alive
     (`reforge/runtime/domain/state/models.py:150`, tested in
     `test_node_mutation_removed.py:101-106`) but its value passes
     through `outcome_resolver.resolve_outcome` and gets reinterpreted
     by intent overrides — STRESS_TEST + `EXECUTION_TIMEOUT` →
     `(SUCCESS, "task_fidelity_achieved")` (`outcome_resolver.py:48-51`).
     `failure_mode` is set in `classifier.py:36-40` before any
     task_intent branch, so it is intent-independent and reflects the
     actual code path under test.
   - D1″ prompt rebased from `Loop forever printing "tick" once per
     second.` to `Sleep for 120 seconds, then print "ok".` The first
     prompt matched STRESS_TEST's few-shot examples in
     `task_intent.py:42-43` and would have triggered the intent
     override above, hijacking `policy_reason` and reframing the
     STOP as success. The sleep prompt deliberately falls back to
     NORMAL_EXECUTION (no STRESS_TEST / EXECUTION_TIMEOUT override
     applies), so the deliberate-STOP path is isolated cleanly.
   - `config.max_retry` confirmed = 3 (default,
     `reforge/config.py:18`); both `retry_decision_node.py:74`
     (governor) and `retry_decision_node.py:50` (`_naive_resolution`)
     read it. `ExecutionGovernor.__init__(max_retries=2)` and
     `PolicyStage.__init__(max_retries=2)` defaults are dead in the
     production path (overridden at instantiation). D1″ wall-clock
     math corrected: `initial + 3 retries = 4 attempts × ~30s = ~120s`
     for naive vs `1 attempt × ~30s = ~30s` for governor.

## Revision log (v4 — post-calibration thesis narrowing)

Changes from v3 after the N_seeds=3 calibration (PHASE0_CALIBRATION.md,
run 2026-06-26 07:10 UTC, four go/no-go gates passed) surfaced two
descriptive observations whose methodological consequence is captured
below. v3's significance rule, paired-delta formulas, sentinel rule,
decoy ban on result-direction gates, N_seeds choices, Tier B defer,
and instrument-only calibration boundary all carry forward unchanged.

1. **Timeout-class deliberate-STOP efficiency headline DEFERRED**
   (was: "narrow win retained" in v3). The v3 headline assumed naive
   on timeout-class decoys burns the full budget
   (`initial + max_retry attempts ≈ 4 × T_attempt`), giving governor
   a clean `1 × T_attempt` advantage on attempts / wall / tokens.
   The calibration falsified that prediction (PHASE0_CALIBRATION O1):
   on D1″ across 3/3 naive seeds, the codegen LLM adapted under retry
   pressure — it shortened or removed the `time.sleep(120)`, produced
   `exit_code == 0`, and the naive baseline ACCEPTed. Governor still
   deliberately STOPped at attempt 1 in all 3 seeds, but naive's
   actual cost was 2-3 attempts (~70-110 s wall) rather than the
   predicted 4 attempts (~120 s). The remaining delta is real but
   marginal, and its magnitude is driven by codegen randomness rather
   than the governor's classifier — not a defensible headline. The
   architectural note is in `docs/KNOWN_LIMITATIONS.md` L5.
2. **Phase 2 headline converges to a single pillar — recovery
   quality driven by typed classification + memory-driven
   retry-hint** (`repair_hint` + `pattern_hint`). Reported on the
   recoverable slice of Phase 2's corpus + BIRD picks 1/2; measured
   as paired delta on **recovery rate**, **attempts on solved**,
   **tokens per solved**, and (when significant) **wall-clock per
   solved**. No claim about deliberate-STOP efficiency, decoy
   precision, or generic unrecoverability recognition. Recovery
   quality is what the audit confirmed governor actually does
   differently from naive (typed `repair_hint` vs blind retry); it
   is the honest single pillar.
3. **Phase 1 BIRD measurement field-of-record locked to the SQL
   comparator**, explicitly. Calibration already grades BIRD via
   `reforge.runtime.sql.comparator`, but v4 elevates this to a
   methodological rule: `state.outcome_state.task_outcome`,
   `state.control_state.policy_reason`, and the runtime's internal
   `evaluation_result.passed` MUST NOT be used as the Phase 1
   passed/failed signal. The calibration surfaced a pattern
   (PHASE0_CALIBRATION O2 — bird_1313 governor 3/3 seeds) where the
   LLM evaluator returned false-negatives on SQL-comparator-correct
   outputs, prompting RETRY-to-budget and `runtime_outcome ==
   "FAILED"` despite the answer being right. KNOWN_LIMITATIONS L6
   captures this as a governor-side failure mode bounded by
   evaluator precision.
4. **Phase 1 sensitivity appendix scope expanded** (was: only paired
   confidence-interval robustness). v4 adds a required check:
   **quantify the LLM-evaluator false-negative rate** across both
   modes' BIRD runs (per-case fraction of attempts whose SQL output
   the comparator confirms correct but the internal evaluator
   rejected), and **inspect its impact on the headline paired
   delta**. If both arms suffer roughly equal false-negative
   pressure, the paired delta is robust by construction (paired
   subtraction cancels common noise). Asymmetric pressure (e.g.,
   governor's repair_hint flow somehow attracts more evaluator
   rejections than naive's blind retry) would require reporting the
   headline with an explicit caveat. Pre-registration commits this
   check **before** seeing the v4 BIRD numbers.
5. **No re-run of Phase 0 calibration is required by v4.** The four
   go/no-go gates passed at ≥3 occurrences each on the recorded
   run; the v4 changes are thesis and methodology updates, not
   instrument changes. PHASE0_CALIBRATION.md remains the
   authoritative calibration artifact.

## Status

**Signed off (v4 + headline single-pillar narrowing, this commit).**
v4 supersedes v3; the v2 → v3 → v4 chain forms the pre-registration
record. The commit hash of this file at v4 sign-off is the reference
any downstream Phase 1 / Phase 2 / eval-chapter number must cite. Any
post-data edit that loosens a metric or relaxes the significance rule
must be flagged in the eval chapter as a post-hoc change with the
reason it was made.

Phase 0 calibration: **GO** as of `docs/eval/PHASE0_CALIBRATION.md`
(run 2026-06-26 07:10 UTC, four gates passed). Phase 1 unblocked.

Phase-0 calibration deliberate-STOP probe: **D1″** (timeout) — see
`docs/eval/PHASE0_CORPUS.md`. D1′ (FileNotFoundError) and the v2
`http://example.invalid/...` resolver-failure decoy are both
deprecated by the v3 audit (would not have triggered deliberate-STOP
under the current governor).

Token-accounting prerequisite resolved: harness-side
`token_accounting(case_id, seed)` context manager landed in commit
`47d1091` (`reforge/observability/llm_events.py`); measurement does
not touch `RuntimeState` or governor decision surface. Vision-skill
coverage gap documented as `docs/KNOWN_LIMITATIONS.md` L2; the eval
corpora contain no image inputs, so measured-path coverage is 100%.
