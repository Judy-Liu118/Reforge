# Known Limitations

English | [简体中文](KNOWN_LIMITATIONS.zh-CN.md)

Architectural debt the team has identified, evaluated, and deliberately
deferred. Each entry names the smell, the correct fix, and why it isn't
being applied right now. If you're tempted to "just patch" any of these
in place — re-read the *Anti-Patterns* line first.

---

## L1. Intent is re-derived from `user_request` in multiple places

### Symptom

Two subsystems carry their own regex/keyword lists for guessing what
kind of task the user asked for:

| Location | Lists |
|---|---|
| `reforge/runtime/orchestration/evaluation/heuristics.py` | `INTENTIONAL_ERROR_PATTERNS`, `DATA_TASK_KEYWORDS`, `RESEARCH_VERIFY_KEYWORDS`, `SUSPICIOUS_NUMERIC` (request-gated) |
| `reforge/models/prompts/directives.py` | `MUST_FAIL_FIRST_PATTERNS`, `EXPECTS_UNCAUGHT_PATTERNS` |

Each list tests `state.user_request` against hand-curated Chinese +
English phrases — `re.search` for the pattern lists, a bare `kw in
lowered_request` substring test for the keyword lists. Both directions
fail.

*Misses*: "make it fail on purpose" never matches `故意.*报错`;
"compute the median salary" matches no `DATA_TASK_KEYWORDS` entry, so a
genuine data task skips `output_contains_data`.

*False hits* are worse, because the keywords are matched as raw
substrings with no word boundary:

| Request | Trips | Because |
|---|---|---|
| "List every **account** holder's name" | `count` | ac**count** |
| "Write a one-line **summ**ary of the README" | `sum` | **sum**mary |
| "Explain the **mean**ing of this flag" | `mean` | **mean**ing |

All three are routed through `output_contains_data` as data tasks.

Review correspondence: items ① (directive hardcoding), ③ (regex misses),
④ (output-length floor), ⑦ (keyword breadth) — all four are surface
manifestations of the same root. ④ has since been retired on its own:
`MIN_OUTPUT_LENGTH` is gone and `output_not_empty` checks presence, not
length (see CHANGELOG). The remaining three stand.

### Root cause

`Governor.IntentStage` already produces a typed classification:
`state.semantic_state.task_intent` (`NORMAL_EXECUTION`,
`EXPECTED_FAILURE`, `RECOVERABLE_FAILURE`, ...) and
`state.task_requirements` (`must_fail_first`, `expects_uncaught_exception`,
...). The downstream consumers should **read** these typed fields, not
re-infer intent from the raw string. The current design has two
oracles — one structured, one stringly — and they drift.

### Right fix (deferred)

1. Promote `TaskKind` to a first-class enum on `RuntimeState` (likely
   on `task_requirements`):
   `Normal | ExpectedFailure | Recoverable | DataAnalysis | ResearchVerify`.
   IntentStage populates it once.
2. Evaluator selects its check set by `task_kind` switch — no
   keyword scan, no `_is_intentional_task()` private method, no
   `is_data_task = any(kw in lowered_request for kw in ...)`.
3. Directive selection (`build_retry_prompt`, `_extract_requirements`,
   etc.) reads `task_requirements`, not pattern lists.
4. Delete `INTENTIONAL_ERROR_PATTERNS`, `DATA_TASK_KEYWORDS`,
   `RESEARCH_VERIFY_KEYWORDS`, `MUST_FAIL_FIRST_PATTERNS`,
   `EXPECTS_UNCAUGHT_PATTERNS`. Their behavior is recoverable from
   the trajectory test corpus by running pre/post `task_kind` on the
   same inputs and checking equivalence.

### Ordering constraint the fix must respect (added 2026-07-25)

Step 2 above has an unstated prerequisite: **the evaluator cannot simply
read the typed field, because on attempt 1 it is still `None`.** The graph
runs `evaluation → retry_decision` (`graph/workflow.py:87-88`), while
`task_intent`'s only persistence point is `retry_decision.py:78` — so the
one consumer that would benefit runs *before* the value exists. Root cause
is placement: intent is a property of the request (`intent_stage.py:3`)
but is only landed on state at the second-to-last node. The fix is to
classify at an entry node (`capability_check` / `planner`) and write
`semantic_state.task_intent` there; `intent_stage.py:20`'s cache makes
every later governor resolve a hit, so the LLM call count is unchanged.

Measured impact while deferred: the drift does **not** reach the final
outcome — `resolve_outcome()` applies its intent override outside the
event map, so for `EXPECTED_ERROR` with `exit_code != 0` all six
`policy_action × eval_passed` combinations still return
`EXPECTED_FAILURE`. Damage is confined to `eval_score` noise written into
`attempts[-1]`, trajectory and memory. (For scale: the keyword list misses
1 of the 2 `intentional` benchmark cases — `intentional_syntax_error`'s
"故意在 print 前面加一个乱码字符让语法出错" matches no entry — and that
case's outcome is still classified correctly.) The unshielded direction is
the *false hit*: a `NORMAL_EXECUTION` request containing e.g. 故意包含
skips `no_error_in_output` + `stderr_clean` (`heuristics.py:193`), and at
`exit_code == 0` no override covers it — eval is the only gate.

### Why defer

- **Scope**: schema bump (new enum + migration for persisted
  `TaskRequirements` / `TrajectoryRecord` snapshots).
- **Risk window**: cleanup is happening close to release; the eval
  output keywords are battle-tested on the demo corpus and changing
  the classification path right before ship invites regression
  no one will catch in time.
- **Sequencing**: the fix is cleaner once the Governor's
  `IntentClassifier` LLM model selection is also locked (currently
  qwen3-vl-thinking is excluded — see `MEMORY.md`), because the new
  enum has to survive a classifier change without breaking
  consumers.

Plan to revisit: post-release, in one batched commit that introduces
the enum, migrates consumers, and deletes the legacy keyword lists in
the same change.

### Anti-patterns — do NOT apply

- ❌ Adding more Chinese / English variants to any of the keyword
  lists. Every addition entrenches the wrong design and adds a tax
  the proper fix has to pay back. The wordlist will never converge
  on the natural-language tail.
- ❌ Adding a new keyword list ("EXPECTED_OUTPUT_FORMAT_PATTERNS",
  "SHORT_ANSWER_PATTERNS", ...) to cover ④'s short-answer false
  positives. Same anti-pattern, same answer: read `task_kind`.
- ❌ Tightening individual regexes in place. Even a "perfect" regex
  for `EXPECTED_FAILURE` doesn't fix the design, it just hides the
  duplication behind a more confident-looking failure mode.
- ❌ Caching the keyword scan result on `RuntimeState` to "share"
  across consumers. That makes the duplication permanent by giving
  the wrong oracle a runtime address.

### Acceptable in-place edits while deferred

- Pure cleanup that doesn't change classification surface area
  (e.g., merging two equivalent `if is_intentional` branches into
  one — see review item ⑤). These don't add or remove a knob; they
  just stop the existing knob from being applied twice.
- Renaming dead variables (review item ⑥) and stale local names
  (review item ⑧). These touch lines but not behavior.
- Behavior-changing-but-isolated fixes that are demonstrably wrong
  *given the current design* (e.g., the `\bUI\b` word-boundary fix
  in the legacy `graph/vision_routing.py` — review item ②, shipped
  while that module still existed; the module itself was later
  removed in the `image_inputs` refactor, see L7). These buy time
  without making the larger problem worse.

---

## L2. Vision skills bypass `LLMClient` — no observability hook coverage

### Symptom

Two skills instantiate `openai.OpenAI` directly and call
`client.chat.completions.create(...)` themselves, never going through
`LLMClient._dispatch`:

| Skill | File:line | Config used |
|---|---|---|
| `VisionDescribeSkill` | `reforge/runtime/skills/builtin/vision.py:172` (OpenAI ctor), `:113-114` (call) | `VISION_LLM_*` |
| `CompareImagesSkill` | `reforge/runtime/skills/builtin/image_compare.py:190` (OpenAI ctor), `:128-129` (call) | `VISION_JUDGE_*` |

Consequence: the module-level hook (`reforge.observability.llm_events._emit
("llm_call_complete", ...)`) does not fire for vision-skill calls.
Token accumulation via `token_accounting(case_id, seed)` is therefore
blind to vision-skill LLM cost. The `compare_images()` helper used in
generated Python for visual self-heal is the heaviest offender — it
gets called once per attempt in a heal loop.

### Why this is a seam

The skills predate the unified `LLMClient` and chose direct SDK use
because:
- they target distinct config (`VISION_LLM_*` and `VISION_JUDGE_*`,
  separate from `LLM_*` / `CODEGEN_VISION_*`),
- they accept remote `http(s)://` image URLs,
- they return `SkillResult`, and
- they use a different retry helper (`call_with_retry` in
  `reforge/runtime/skills/builtin/_api_retry.py`).

Routing them through `LLMClient` would require extending the client
surface — new factory methods, multi-image multimodal support, and
threading the skill-result shape. Worth doing, not worth doing now.

### Right fix (deferred — trigger condition below)

Add `LLMClient.for_vision_describe()` and `LLMClient.for_vision_judge()`
factories that mirror `for_vision_codegen()`. Migrate both skills to
use `client.chat_multimodal(...)` — which already extracts `usage`
and emits the hook — instead of direct `OpenAI(...).chat.completions.
create(...)`. The skills keep their retry / downscaling / SkillResult
shape; only the network call is routed.

### Why defer

- **Measurement scope today**: the two eval corpora locked in
  `docs/eval/PHASE0_METRICS.md` (BIRD SQL, Phase-2 pandas/CSV)
  contain no image inputs. The planning LLM does not invoke vision
  skills on either, so the gap is not on the measured path —
  `tokens_per_solved` coverage is 100% on those corpora.
- **Risk of "painting over" the seam**: patching the hook into the
  skills' current shape (the cheap fix) ratifies the dual-LLM-path
  design instead of unifying it. The deferral keeps the pressure
  pointing toward the unified rewrite when it actually matters.

### Trigger to revisit

The deferral expires the moment a measured eval axis includes
image-bearing tasks (e.g., a future "UI reproduction" axis built on
the visual self-heal loop). Until then this is documented surface
area, not a bug.

### Anti-patterns — do NOT apply

- ❌ Adding a copy of the `_emit("llm_call_complete", ...)` block
  inside each vision skill. Ratifies the bypass; doubles the call
  sites that have to be kept in sync with the event schema; doesn't
  remove the dual-LLM-path code smell.
- ❌ Reading `response.usage` in the skills and stashing it on
  `SkillResult.metadata` for the driver to harvest. Same anti-pattern
  wearing a different hat — and it leaks measurement plumbing into
  the skill contract, which other skills don't carry.

### Acceptable in-place edits while deferred

- Pure logging additions inside the skills that don't change the
  network call path.
- Updates to the docstring / `prompt_fragment` of either skill.
- Adjustments to `call_with_retry` that don't change semantics.

---

## L3. Deliberate STOP is intent-driven + timeout-driven, not history-derived

### Status update (2026-07-13): the deferred fix has landed — narrowed to the repeated-identical-failure case

The detector described under "Right fix" below shipped after the Phase 1
BIRD ablation closed (post-R2), which is one of the two sanctioned landing
windows ("before Phase 0, or after the eval"). What landed:

- `ClassifyStage` flips a retryable classification to
  `failure_mode="repeated_signature"` when the last 2 consecutive attempts
  share one identical structural fingerprint
  (`semantic_state.failure_signature_history`, appended per failed attempt
  by the reflection node). The fingerprint is parsed deterministically from
  the traceback (`extract_fingerprint`), NOT from the LLM reflection text —
  the reflection node is merely where the append happens, so the
  "reflection has no runtime authority" boundary holds; the only
  LLM-influenced input is the `error_type` fallback used when a traceback
  has no parseable error line. `RetryPolicy` turns that into a deliberate STOP
  (`repeated_failure_signature`); the outcome resolver reports it as its
  own event rather than mislabeling it `RETRIES_EXHAUSTED`.
- Deviations from the sketch below, on purpose: **full-fingerprint equality**
  instead of a per-exception-type counter (same error class AND same target
  module/key/file/name — strictly higher precision than type-counting), and
  **threshold 2, consecutive** instead of `N = 3` cumulative, because with
  the default budget (`max_retry=3` → 4 attempts) a same-signature run
  would exhaust budget before a cumulative count of 3+1 mattered, and the
  saved attempts are the entire point. Expected-failure intents
  (`RECOVERABLE_DEMO`) are exempt — a stated recoverable intent outranks
  the history signal.
- **The Phase 1 R1 + R2 numbers measure the runtime WITHOUT this
  detector; run 3 (2026-07-13, `docs/eval/PHASE1_BIRD_ABLATION_R3.md`,
  200 runs at commit `bcc11fb`) measures it live — and found it
  dormant on this corpus: zero `repeated_failure_signature` STOPs.**
  Attribution from the raw records (`phase1_records_r3.jsonl`): of the
  31 retried attempts in the governor arm, 30 were *quiet* evaluator
  rejections (exit 0, no traceback — nothing is appended to
  `failure_signature_history`, so the detector is blind to them by
  design) and only 1 was a loud failure; zero runs contained two
  consecutive loud failures, which is the only shape the detector can
  act on. Consequently R3's governor arm made decision-for-decision the
  same choices the pre-L3 runtime would have made, the success_rate
  null reproduced (61.0% vs 62.0%, paired Δ 95% CI [-9.1, +7.1]pp), and
  the cost deltas are statistically consistent with R2 (tokens-per-solved
  Δ CI [1,199, 4,215] vs R2's [1,001, 2,683]). The detector's value
  proposition is therefore confined to workloads whose failures are
  loud AND persistent (missing dependency loops, unreachable resources)
  — Phase-0-style, not BIRD-style. Behavioral validation remains
  unit + integration tests (`reforge/tests/test_repeated_signature_stop.py`,
  `test_retry_loop.py::test_repeated_identical_failure_stops_early`).
- The quiet 4-attempt runs R3 did record (5 governor runs burned the
  full budget on repeated evaluator rejections) are the eval-side
  analogue the "Right fix" sketch mentions — detecting repeated
  identical `evaluation_result.failure_type`. Deliberately NOT built:
  the anti-pattern list below explains why repurposing eval-rejection
  recurrence as an unrecoverability signal needs its own
  precision study first.
- The precision caveat under "Why defer" is not closed, it is *disclosed*:
  a repeated identical fingerprint CAN in principle still recover on a
  later attempt. The threshold is a design choice, not a calibrated
  parameter.

The sections below are preserved as written for the historical record of
why this was deferred through Phase 0/Phase 1.

### Symptom

The governor's `RetryPolicy.decide()` (`reforge/runtime/policy/retry_policy.py:19-53`)
issues a *deliberate* STOP — i.e., a STOP with budget remaining — via
exactly two branches:

| Branch | Trigger | Set by |
|---|---|---|
| `terminal_intentional_failure` | `is_expected_failure=True AND retryable=False` | `FailureClassifier` (`classifier.py:48-52`) when `task_intent ∈ {EXPECTED_ERROR, TRACEBACK_DEMO}` |
| `timeout` | `failure_mode == "timeout"` | `FailureClassifier` (`classifier.py:36-40`) when `exit_code == TIMEOUT_EXIT_CODE` |

All other failures — including repeated identical `FileNotFoundError`,
`ImportError` for a missing module, RFC2606 `.invalid` host
resolution failure, logically unsatisfiable arithmetic, contradictory
constraints — fall through to `if execution.exit_code != 0: RETRY
"execution_error"` and loop until `retry_count == max_retries` →
`retry_limit_reached_with_error` STOP (budget-exhausted, NOT
deliberate).

`ClassifyStage._PATTERN_THRESHOLD` (`classify_stage.py:12, 46-58`)
exists but is **not** a STOP trigger. It only injects a
`"[recurring failure: …]"` prefix into `repair_hint`, which steers the
next attempt's prompt; it never flips `is_expected_failure` or
`retryable`. It also watches `evaluation_result.failure_type`, not
runtime traceback signatures.

### Root cause

The runtime classifies failures *deterministically* from `task_intent`
+ `exit_code` + `evaluation_result` only. There is no per-case error
history fed into classification — by deliberate design (deterministic,
reflection-free classification was a stated invariant). Consequently
the governor cannot conclude "I have seen the same exception type N
times in a row → this run is unrecoverable" without an additional
mechanism that does not currently exist.

### Practical impact

Surfaced during Phase 0 calibration corpus design (see
`docs/eval/PHASE0_CORPUS.md` v2 and `docs/eval/PHASE0_METRICS.md`
v3). The originally proposed D1′ (missing `config.yaml` →
`FileNotFoundError`) would not have probed deliberate-STOP; it would
have RETRY'd to budget exhaustion. Phase 0 rebased to D1″ (timeout
decoy) which exercises the `failure_mode == "timeout"` branch. The
`terminal_intentional` branch cannot be calibration-probed without
constructing an `EXPECTED_ERROR`-intent prompt, which would leak intent
into the corpus.

Phase 2's earlier deliberate-STOP precision / recall metrics
(`PHASE0_METRICS.md` v2 Tier B) presumed a recognizer covering
diverse decoy root causes — resolver failure, missing env dep,
logically unsatisfiable, self-contradictory. None of those triggers
deliberate STOP under the current runtime, so the metrics would have
reported near-zero values that measure an absent feature. Tier B is
marked deferred in v3.

The runtime's honest current scope is:

1. **Recovery quality on recoverable failures** — typed classification
   + `repair_hint` (memory recall + recurring-pattern hint) shaping
   each retry attempt. This is the headline ablation surface vs the
   naive baseline's blind retry.
2. **Efficiency on timeout-class and EXPECTED_ERROR-intent failures**
   — deliberate STOP avoids the full `max_retry × T_attempt` budget
   burn. A narrow but real delta on the runs that hit those paths.

### Right fix (deferred — see below for why not now)

Add a pattern-based unrecoverability detector to `ClassifyStage`
(*not* `_PATTERN_THRESHOLD`, which only shapes hints):

- Per-case, hash the top-level exception type from the runtime
  traceback (e.g., `FileNotFoundError`, `ImportError`, `KeyError`).
- Maintain a per-case `Counter[exception_type] → int` across attempts
  within the same case run.
- When `counter[top_level_exc_type] >= N` (suggested initial threshold
  `N = 3`), flip the next-attempt classification to
  `is_expected_failure=True, retryable=False,
  failure_mode="repeated_signature"`. PolicyStage then issues a
  deliberate STOP.
- Apply the same to repeated identical `evaluation_result.failure_type`
  on eval-driven failures (separately, since eval-failure history is
  already partially tracked by `_PATTERN_THRESHOLD`).

### Why defer

- **Precision is unverified, and probably worse than it looks**. A
  repeated `FileNotFoundError` next attempt CAN still recover — the
  codegen may decide to `os.makedirs` + write a placeholder, or
  switch to a different path, or import `pathlib` and use a default.
  A repeated `ImportError` may resolve when codegen swaps to a
  stdlib alternative. The threshold `N` and per-exception-type
  exemptions need empirical tuning *before* adoption, and tuning on
  the eval corpus would violate the v3 pre-registration ("no
  parameter tuning on eval data"). A pre-Phase-2 detector PR therefore
  needs its own non-eval calibration corpus, which is a project of
  its own.
- **Changes the system-under-test mid-experiment**. Adding the
  detector between Phase 0 sign-off and Phase 2 runs would mean the
  ablation compares "governor with new detector" vs "governor without
  detector" rather than vs naive, muddling the headline. Either the
  detector lands before Phase 0 (and is part of the locked runtime
  surface), or after Phase 2 (and motivates a Phase 4).
- **Honest scope today is fine**. The recovery-quality headline
  (point 1 above) is the actual differentiation between governor and
  naive on a typical workload; the deliberate-STOP efficiency is a
  secondary, narrow win. Forcing the secondary win to cover decoy
  classes the runtime can't recognize would dishonestly inflate the
  claim.

### Trigger to revisit

The deferral expires if either:

- A subsequent eval (Phase 4+) is explicitly designed to motivate the
  detector — i.e., a slice of recoverable+decoy cases where the
  detector demonstrably shifts the precision/recall point and the
  eval methodology accounts for the system-under-test change.
- A downstream user-facing requirement emerges (e.g., "the runtime
  should stop attempting unsolvable user tasks within ≤2 attempts
  rather than burning the full retry budget") that the current
  intent + timeout coverage cannot satisfy.

Until either trigger fires, this is documented surface area, not a
bug, and Phase 1 / Phase 2 ship within the narrowed scope above.

### Anti-patterns — do NOT apply

- ❌ Promoting `_PATTERN_THRESHOLD` from "inject repair_hint prefix"
  to "flip `retryable=False` and STOP". That conflates two different
  mechanisms (hint quality vs unrecoverability detection), repurposes
  an already-pre-registered threshold (contamination disclosure in
  `PHASE0_METRICS.md` would have to be revised), and gives the wrong
  signal (recurring `evaluation_result.failure_type` says "eval keeps
  rejecting", not "runtime keeps crashing identically").
- ❌ Inferring unrecoverability from `reflection` output. Reflection is
  explicitly excluded from classification by current design
  (`classifier.py` docstring: "Reflection = debugging hints only, no
  runtime authority"). Routing classification through reflection
  would re-introduce the boundary violation L1 already documents in
  spirit.
- ❌ Adding a keyword scan ("if 'invalid' or '.com.invalid' in
  traceback: STOP") to recognize resolver-failure decoys. Same
  anti-pattern as L1 — replaces a structural fix with a brittle
  string match that won't generalize and will rot.
- ❌ Quietly lowering `max_retry` to 1 on the eval corpus so the
  difference between deliberate-STOP and budget-exhausted-STOP
  disappears. That hides the gap by removing the measurement; it
  doesn't close it.

### Acceptable in-place edits while deferred

- Adding observability fields to `RuntimeResolution` that record
  *why* a STOP was issued (`policy_reason` already does this — keep
  it). No new STOP triggers, just better post-hoc analysis.
- Telemetry that counts per-case repeated-exception-type runs and
  surfaces it in the eval chapter as "this is what a future detector
  could have caught" — measurement, not behavior change.
- Adding more cases to `task_intent.py`'s few-shot prompt so
  IntentStage classifies more accurately (still only NORMAL_EXECUTION
  / EXPECTED_ERROR / TRACEBACK_DEMO / RECOVERABLE_DEMO / STRESS_TEST
  / SANDBOX_ESCAPE — no new enum members). Tightens the existing
  deliberate-STOP paths without adding new ones.

---

## L4. Constructor `max_retries` defaults (=2) diverge from `config.max_retry` (=3)

### Symptom

Three constructors / argument signatures carry the same hardcoded
default for max retries (a fourth, `PolicyEngine`, was an unconsumed
wrapper and has been deleted):

| Location | Signature |
|---|---|
| `reforge/runtime/policy/retry_policy.py:25` (`RetryPolicy.decide()` parameter) | `max_retries: int = 2` |
| `reforge/runtime/orchestration/governor/policy_stage.py:12` (`PolicyStage.__init__`) | `max_retries: int = 2` |
| `reforge/runtime/orchestration/governor/engine.py:31` (`ExecutionGovernor.__init__`) | `max_retries: int = 2` |

The production runtime path goes through
`reforge/runtime/orchestration/graph/nodes/retry_decision.py:70`:

```python
governor = ExecutionGovernor(max_retries=config.max_retry)
```

which reads `config.max_retry = int(os.getenv("MAX_RETRY", "3"))`
(`reforge/config.py:18`). The bypass `_naive_resolution`
(`retry_decision.py:46`) likewise reads `config.max_retry` directly.
**So in production all four constructor defaults are dead.**

### Why this is a seam

- **Test surface**: every unit test that instantiates these classes
  without passing `max_retries=` silently runs at budget `2`, not the
  production budget `3`. Behavioral assertions about "after the third
  RETRY, governor STOPs" are sensitive to this one-attempt gap.
- **Future callers**: anyone wiring a new entry point that omits the
  config injection silently gets budget `2` and won't notice until a
  production case differs from a unit-test case by one retry.

### Right fix (deferred)

Either:
- Drop the default entirely (make `max_retries` required), forcing
  every call site — tests included — to pass an explicit value; or
- Read `config.max_retry` lazily inside each constructor
  (`max_retries: int | None = None` + `if max_retries is None:
  max_retries = config.max_retry`), giving one source of truth.

### Why defer

- Cross-cuts test fixtures (`test_full_consistency_integration.py`,
  `test_policy_with_intent.py`, etc.) and lands as a separate
  cleanup PR after the eval framework is in place.
- Not on the eval measurement path: Phase 0 / 1 / 2 all go through
  `retry_decision_node`, where `config.max_retry` is injected. No
  measured result depends on the constructor default; this entry is
  reviewer-visibility insurance, not a calibration blocker.

### Anti-patterns — do NOT apply

- ❌ Quietly changing the default from `2` to `3` to "match
  production". Hides the divergence; tests pinned to a "2-retry"
  behavior would start producing different traces without anyone
  updating the assertion.

### Acceptable in-place edits while deferred

- New tests / new call sites that pass `max_retries=config.max_retry`
  explicitly, so the dead-default surface area does not grow.

---

## L5. Codegen LLM adapts away from long sleeps under retry pressure — timeout decoys are not budget-stable

### Symptom

Recorded during Phase 0 calibration (`docs/eval/PHASE0_CALIBRATION.md`
observation O1). The D1″ decoy is `Sleep for 120 seconds, then print
"ok"`, with `EXECUTION_TIMEOUT` pinned to 30 s; the pre-registered
design expected naive to RETRY 4 attempts × ~30 s ≈ 120 s wall before
budget exhaustion.

Observed under `REFORGE_GOVERNOR_BYPASS=1` (3/3 seeds):

| seed | attempts | action | retry_count | policy_reason |
|---|---|---|---|---|
| 0 | 3 | ACCEPT | 2 | `naive: exit_code == 0` |
| 1 | 3 | ACCEPT | 2 | `naive: exit_code == 0` |
| 2 | 2 | ACCEPT | 1 | `naive: exit_code == 0` |

The codegen LLM, when handed the previous attempt's traceback
("Execution timed out after 30s") on retry, did not re-emit
`time.sleep(120)`. It either shortened the sleep, removed it entirely,
or rewrote the script to exit cleanly — producing `exit_code == 0`
and an ACCEPT instead of the predicted budget-exhausted STOP.

### Root cause

There is no determinism between codegen attempts within a session.
Each attempt re-invokes the codegen LLM with a retry prompt that
includes the prior traceback; the model is free (and often inclined)
to "fix" the problem by sidestepping the original spec rather than
faithfully re-emitting the same code. For a decoy whose only purpose
is to time out, "fix the timeout" looks like rational repair to the
LLM. The result is that **the naive baseline's budget-burn behavior
on timeout decoys is not stable across seeds or models**.

### Practical impact

- **The governor's deliberate-STOP timeout code path remains
  verifiably reachable**: 3/3 governor seeds in the calibration hit
  `action=STOP, failure_mode="timeout", retry_count=0`. The L5
  observation does NOT undermine the calibration verdict; what it
  rebuts is the pre-registered prediction about naive's wall-clock /
  attempt-count cost on timeout decoys.
- **Phase 2 cannot headline a "timeout-class deliberate-STOP
  efficiency" win.** The remaining attempt / wall / token delta
  between governor (always 1 attempt) and naive (2-3 attempts,
  variable) is real but marginal, and its magnitude is dominated by
  codegen randomness rather than the governor's classifier.
  `docs/eval/PHASE0_METRICS.md` v4 §1 defers this headline.
- **More broadly: any decoy whose only failure mechanism is
  watchdog timeout is corpus-fragile.** The model can always "fix"
  it. Robust decoys would need a failure mode the codegen LLM cannot
  route around without changing the answer's correctness — and the
  current runtime has no such non-timeout, non-intent decoy class
  it can recognize (per `docs/KNOWN_LIMITATIONS.md` L3).

### Right fix (deferred)

Two non-exclusive paths exist:

- **Pin codegen determinism** (temperature=0 + fixed seed via the LLM
  client). Reduces inter-seed variance on D1″-style decoys, but does
  not eliminate the model's "fix the timeout" inclination — it just
  makes the same fix happen every time.
- **Replace timeout-decoy designs with adversarial-correctness
  decoys** that cannot be sidestepped without producing a verifiably
  wrong answer (e.g., a task that genuinely requires waiting for an
  external event the sandbox cannot provide, with a comparator that
  rejects any other output). Hard to construct without leaking
  intent.

### Why defer

- Phase 0 calibration is GO; the calibration gates do not depend on
  naive's budget-burn behavior, only on the governor-side
  deliberate-STOP path being reachable.
- Phase 2's headline has converged to "recovery quality" alone
  (PHASE0_METRICS v4 §2); the deliberate-STOP efficiency story this
  L5 entry undermines is already dropped.
- A timeout-decoy redesign is a corpus question, not a runtime
  question. It can be revisited if a future eval slice needs to
  measure deliberate-STOP efficiency specifically — and would need
  its own design + calibration pass.

### Trigger to revisit

- A future eval explicitly motivates measuring deliberate-STOP
  efficiency (separate from recovery quality), e.g., production
  cost-of-retry analysis where wall-time on unrecoverable tasks
  drives the SLA.
- The codegen model is changed to one whose retry behavior under
  timeout is documented and reproducible.

### Anti-patterns — do NOT apply

- ❌ Forcing the codegen LLM to "re-emit the same code on retry"
  via a system-prompt directive. Hides the underlying corpus
  fragility behind prompt engineering; the next eval that uses a
  different model has the problem back.
- ❌ Asserting in the calibration / Phase 2 gate that naive D1″ must
  end in STOP. The model is free to ACCEPT; the gate it must satisfy
  is "governor deliberately STOPs", not "naive does not".
- ❌ Quietly raising `EXECUTION_TIMEOUT` to make timeouts "cheaper"
  per attempt. Cosmetic — doesn't change the model's "fix the
  timeout" inclination.

### Acceptable in-place edits while deferred

- Adding more diagnostic fields to `CalibrationRecord` so the
  observation is visible without re-running (already done — actions
  / retry_count / policy_reason are now captured).
- Documenting the observation in PHASE0_CALIBRATION (already done as
  O1).

---

## L6. Governor recovery is upper-bounded by the internal LLM evaluator's precision

### Symptom

Recorded during Phase 0 calibration (`docs/eval/PHASE0_CALIBRATION.md`
observation O2). On `bird_1313_student_club` under governor mode, all
3 seeds produced the SQL-comparator-correct row at some attempt, yet:

| seed | attempts | action | policy_reason | runtime_outcome | passed (comparator) |
|---|---|---|---|---|---|
| 0 | 4 | STOP | `evaluation_failed` | FAILED | True |
| 1 | 4 | STOP | `evaluation_failed` | FAILED | True |
| 2 | 4 | STOP | `evaluation_failed` | FAILED | True |

The runtime's internal LLM evaluator
(`state.semantic_state.evaluation_result.passed`) returned `False` on
the attempt whose output the SQL comparator (ground truth) subsequently
confirmed as correct. PolicyStage took the
`if evaluation and not evaluation.passed: RETRY "evaluation_failed"`
branch (`retry_policy.py:50-51`) and the governor RETRY'd until
`retry_count == max_retry`, at which point it emitted
`retry_limit_reached_on_eval_fail` STOP with `runtime_outcome ==
"FAILED"` — recording the case as a failure even though the answer
was right.

### Root cause

`SemanticState.evaluation_result` is set by an LLM-based evaluator,
not a deterministic comparator. LLM evaluators have measurable
false-negative rates, especially on nuanced SQL outputs where the
correct row is correct but the evaluator quibbles about formatting,
column alias presentation, or NULL handling. The runtime cannot tell
the difference between a real evaluator-rejected output and a
false-negative; it must take the evaluator's signal at face value, so
it RETRYs. After `max_retry` cycles of "evaluator rejects, governor
RETRYs", the case STOPs with the evaluator's reason recorded.

### Practical impact

- **The governor's recovery rate carries an implicit ceiling set by
  the LLM evaluator's precision.** Evaluator false-negative ⇒ the
  governor burns retries on an already-solved case ⇒ if budget runs
  out, the case is recorded as `runtime_outcome="FAILED"` even
  though the answer is correct.
- **`runtime_outcome` and `policy_reason` are NOT reliable
  passed/failed signals for Phase 1 BIRD reporting.** Phase 1 BIRD
  measurement is locked to the SQL comparator
  (`reforge.runtime.sql.comparator`), per
  `docs/eval/PHASE0_METRICS.md` v4 §3.
- **Paired delta vs naive is partially insulated.** Naive does not
  consult the LLM evaluator (`_naive_resolution` reads only
  `exit_code`), so naive does not suffer L6's false-negative
  RETRYs. This asymmetry can cut either direction depending on case
  shape:
  - Governor's wasted retries on already-solved cases inflate
    `attempts_per_case` and `tokens_per_solved` against governor.
  - Naive's ignorance of evaluator signals means naive ACCEPTs on
    `exit_code=0` outputs that may be silently wrong by the
    comparator — inflating naive's apparent solve rate at a quality
    cost.
  Phase 1 sensitivity appendix (PHASE0_METRICS v4 §4) is required to
  quantify the false-negative rate and check headline robustness.

### Right fix (deferred)

The cleanest fix is a hybrid evaluator: trust the SQL comparator
(or any deterministic oracle the task provides) when one exists, and
fall back to the LLM evaluator only when no comparator is available.
`reforge.runtime.sql.comparator` already encodes BIRD's ground-truth
comparison logic; integrating it into PolicyStage's "should I RETRY"
decision would close most of the SQL-domain false-negative surface
without changing the governor's recovery-quality story.

Secondary: tune the LLM evaluator's prompt to be less strict on
formatting / alias / NULL details (which are responsible for most of
the observed false-negatives), or downweight evaluator-driven retries
relative to execution-error retries in the policy.

### Why defer

- **Changes the system-under-test mid-experiment.** Either fix
  rewires PolicyStage; the Phase 1 ablation would then compare a
  modified governor vs naive instead of the locked v4 surface.
- **Phase 1 sensitivity appendix is the v4-locked mitigation.** It
  surfaces the false-negative rate explicitly and lets the headline
  be qualified rather than silently affected.
- **Not a calibration blocker.** The calibration uses the SQL
  comparator for BIRD grading throughout, so this L6 entry does not
  affect any of the four go/no-go gates.

### Trigger to revisit

- Phase 1 sensitivity appendix shows the evaluator false-negative
  rate is asymmetric across modes (governor's repair_hint flow
  attracting disproportionate evaluator rejections), or material in
  magnitude (e.g., >20% of governor STOPs are evaluator-rejected
  correct outputs).
- A future eval domain has no deterministic comparator and so cannot
  rely on the v4 SQL-comparator-locked rule.

> **Trigger FIRED — Phase 1, 2026-07-11**
> (`docs/eval/PHASE1_BIRD_ABLATION.md` Appendix D). The false-negative
> rate on comparator-correct attempts is 80.8% (governor) vs 52.3%
> (naive); the per-seed paired FN delta is +16.0pp, 95% CI
> [+11.0, +21.1] — **ASYMMETRIC**, so every Phase 1 headline carries
> the caveat. Case-level accounting: 34/100 governor runs retried an
> attempt-1 answer the comparator had already confirmed (3 lost the
> correct answer on retry), vs 5 genuine wrong→right recoveries.
> Consequence: success_rate delta is null (65.0% both arms) at 3.1×
> tokens-per-solved. Evaluator calibration is now the gating fix
> before the governor-vs-naive axis is re-run; the anti-patterns
> below still apply (fix the evaluator on held-out data, not on this
> corpus).
>
> **Gating fix landed — 2026-07-11**
> (`docs/eval/EVALUATOR_CALIBRATION.md`). Attribution: 100% of the
> Phase 1 false negatives were length-based checks penalizing
> contract-compliant scalar answers ("Print nothing else" is the
> task's own instruction). Fix: the evaluator now recognizes an
> explicit output contract in the request and suspends the
> length/digit plausibility checks (emptiness, tracebacks, exit
> codes unchanged). Validated on 300 held-out pool questions (seed
> 20260711, picks untouched): FN 42.7% → 0.0%, rejection integrity
> 0 failures. The axis is cleared to re-run; headlines may NOT be
> recomputed from the old records (the evaluator drives runtime
> retry behavior, so only a fresh run measures the fixed system).
>
> **Run 2 confirms the fix in vivo — 2026-07-11**
> (`docs/eval/PHASE1_BIRD_ABLATION_R2.md`). Evaluator FN 0.0% in
> both arms across all 5 seeds, sensitivity verdict **symmetric**;
> zero comparator-pass/runtime-FAILED runs (the L6 symptom is gone).
> The remaining L6 exposure is the false-*positive* side only: a
> rule-based evaluator cannot detect a semantically wrong query that
> exits cleanly, which is why the SQL comparator stays the
> field-of-record for benchmark grading.

### Anti-patterns — do NOT apply

- ❌ Using `runtime_outcome` or `policy_reason` as the Phase 1 BIRD
  pass/fail signal. v4 §3 locks the SQL comparator as the
  field-of-record.
- ❌ Silently disabling the LLM evaluator's RETRY branch (lines 50-51
  in `retry_policy.py`) to mask the false-negative pressure. That
  hides a governor failure mode rather than reporting it.
- ❌ Loosening the LLM evaluator's prompt on the eval corpus to
  reduce the false-negative rate observed in this study. That tunes
  on the eval corpus and violates the v3 pre-registration ("no
  parameter tuning on eval data").

### Acceptable in-place edits while deferred

- Adding diagnostic fields that capture the
  comparator-vs-LLM-evaluator disagreement on a per-attempt basis,
  so the sensitivity appendix has the data it needs.
- Tightening the LLM evaluator's prompt on **non-eval** corpora
  (the demo / regression suite), as long as those changes do not
  flow into the Phase 1 / Phase 2 governor surface.

---

## L7. Visual codegen has no CLI entry point — programmatic-only

### Symptom

The CLI `reforge run "<prompt>"` (`reforge/cli/commands/run.py`) does
not accept image attachments. Visual reproduction tasks that need to
route through the multimodal codegen LLM
(`LLMClient.for_vision_codegen().chat_multimodal(...)`) can only be
launched programmatically via:

```python
RuntimeRunner().run(user_request, image_inputs=["/abs/path/target.png", ...])
```

### History

Before the `image_inputs` refactor, visual reproduction was reachable
through the CLI only via a filesystem convention: the user cd'd into
a workspace, manually placed `target.{png,jpg,jpeg,webp}` in `cwd()`,
and ran `reforge run "复刻 …"`. A `vision_routing_node` then did a
double-gate match (visual-intent regex on the request × filesystem
scan for the magic filenames) and wrote the routing decision onto
state for `code_generation_node` to consume.

That implicit path has been removed. Routing is now driven by an
explicit `state.image_inputs` declaration provided by the caller, not
by guessing intent from prose plus scanning the workspace. The
disambiguation between "user-declared input image" and "data task
produced PNG that happens to live in the workspace" is now structural
(only what the caller declared lands in image_inputs; a loop-boundary
invariant in `RuntimeRunner.stream` prevents any graph node from
mutating the field). The trade-off is that the CLI lost its sole
visual entry point in the same change.

### Right fix (deferred)

Add a `--image PATH` flag to `cli/commands/run.py` (repeatable for
multiple inputs):

```bash
reforge run --image ./target.png "复刻 target.png 前端页面"
```

The flag would collect into a list and pass it through to
`RuntimeRunner.run(image_inputs=...)`. Validation of path existence
and basic image format checks belong at the CLI boundary, not in the
Runner.

### Why defer

- **Scope discipline.** The `image_inputs` refactor was a routing /
  state-shape change with a hard fence against touching neighbour
  files. Bundling a CLI flag would have inflated the diff and risked
  test churn unrelated to the routing decision.
- **No measured eval path depends on it.** Phase 0 / 1 / 2 corpora
  are SQL and pandas/CSV (no image inputs); the calibration driver
  goes through the Python API directly, not the CLI.
- **The Python API is sufficient for the visual self-heal demo.**
  Programmatic callers (including the existing visual reproduction
  workspace) can switch to `RuntimeRunner.run(..., image_inputs=...)`
  in one line; the CLI flag is convenience, not unblocker.

### Trigger to revisit

- A user-facing demo / docs flow needs `reforge run` (the CLI) to
  cover the visual reproduction loop.
- A measured eval axis adds image-bearing tasks and needs the CLI
  for case-loader-driven invocation.

### Anti-patterns — do NOT apply

- ❌ Re-introducing a filesystem scan or visual-intent regex
  inside the CLI as a "fallback when no `--image` is passed". That
  resurrects the exact gate this refactor removed and re-opens the
  data-task-produced-PNG false-positive surface.
- ❌ Reading `image_inputs` from an environment variable or
  workspace-local config file as a CLI-side shim. Same anti-pattern
  wearing a different hat — it routes the decision through an
  implicit channel instead of an explicit kwarg.
- ❌ Adding the flag in the same PR as the routing refactor. The
  routing refactor's fence ("do not touch neighbour files") is the
  reason its diff is reviewable; a CLI surface change is a separate
  conversation about flag naming, validation, and `--image @file.list`
  expansion that deserves its own PR.

### Acceptable in-place edits while deferred

- Updating the Python-API examples in README / docs to demonstrate
  the `image_inputs=...` kwarg.
- Adding a docstring on `RuntimeRunner.run` that points at this L7
  entry as the reason there is no equivalent CLI flag yet.

---

## L8. `ExecutionMemory.recall_similar()` matches on failure *shape*, not identity — can inject a hint keyed to the wrong specific value

### Symptom

`_score()` (`reforge/memory/execution_memory.py`) awards points per
structural field independently — `error_class`, `root_cause`,
`domain`, `failure_mode` each contribute their own weight regardless
of the others. The specific-identity fields (`missing_key` /
`missing_module` / `missing_file` / `undefined_name`) only contribute
their weight on an exact string match; when the value differs, that one
field's weight is simply omitted — every other structural field still
matches, so the structural score stays comfortably positive and the
record is still recalled as the top hint. Note the admission gate does
*not* catch this: it requires a non-zero **structural** score
specifically to keep text overlap from admitting records on function
words alone, and here the structural score is genuinely non-zero —
same error class, same root cause, same domain. Tightening the gate
further is the deferred fix below, not something the existing gate
does. There is no embedding or semantic model anywhere in
the path (`reforge/memory/retrieval.py` docstring: "No embedding. No
vector DB. Pure heuristic ranking.") — "similar problem" in this
codebase means "same shape of failure," not "same or related
underlying cause."

Confirmed live (2026-07-21): a session seeded a `RECOVERED` record for
`KeyError: 'user_id'` (repair: introspect and rename to the real CSV
header). A later, unrelated session failing on `KeyError: 'order_id'`
(a different CSV, different column, different task) recalled that
same record — the retry prompt's `repair_hint` read `"...for uid in
df['user_id_column']: print(uid)"`, naming a column that does not
exist anywhere in the second task. Raw evidence in
`runs/dc4cb32a/code.txt` (the seeding session) and
`runs/3c9cc5ae/code.txt` (the mismatched recall, `[repair_hint used]`
line visible in the persisted per-attempt code — see the `code.txt`
persistence entry in `CHANGELOG.md`).

### Update (2026-08-02): admission gate tightened; `domain` demoted

The admission gate described above changed. `recall_similar` now admits on
the **existence of a qualifying structural signal** (failure_mode /
root_cause / a specific fingerprint field), not on `structural > 0`, and
`domain` no longer grants eligibility — it is a ranking tie-breaker only.
Rationale: in the current single-language scope `domain` is near-constant
across records, so as an admission condition it degenerates into an
always-true predicate (see the `_QUALIFYING_FINGERPRINT_KEYS` note in
`execution_memory.py`, and L10 for the sibling planner/CLI path that was
NOT tightened). This does **not** close L8: the confirmed case still
qualifies on `error_class` + `root_cause`, so the shape-not-identity
mismatch stands. The Symptom text above is preserved as written; where it
says the gate "requires a non-zero structural score … same domain,"
`domain` no longer contributes to admission (only to ranking).

### What actually prevents this from corrupting outcomes

Not recall precision — codegen's treatment of `repair_hint` as loose
prompt context, not a literal patch. In the confirmed case, the retry
attempt's generated code discarded the hint's wrong specific detail
(`user_id_column`) and kept only its general strategy (introspect
`df.columns`, match adaptively), because the LLM is free to disregard
any part of a hint. This is incidental safety, not designed safety: a
differently-shaped mismatch, or a less careful codegen prompt, could
propagate a wrong literal value straight into generated code.

### Right fix (deferred)

Require the specific identifying value(s) to match — exactly, or via
a cheap normalization (case-fold, underscore/space-fold) — before
crediting any score above the bare `error_class` match, or gate hint
injection on a minimum score materially higher than what "same shape,
different identity" alone produces.

### Why defer

- No measured evidence this changes outcomes. Real recovery rates are
  already low (R2: 3/100 runs, Phase 1 BIRD: 5/100 runs); the
  mitigating codegen behavior observed above means the failure mode
  an identity gate would prevent (a corrupted retry from a bad literal
  suggestion) has been shown to be *possible*, not shown to *occur*.
- Tightening the match would also lose genuine cross-case value: two
  failures with different specific missing keys but the same actual
  root cause (e.g. a CSV loader assuming the wrong date format,
  surfacing as different downstream `KeyError`s) currently share a
  hint productively; an identity gate loses that pairing too.
- Not on the eval measurement path — Phase 0/1/2 corpora were not
  designed to probe recall precision specifically.

### Trigger to revisit

- A measured eval axis is designed to isolate recall precision (e.g.
  paired cases with structurally-identical-but-causally-unrelated
  failures) and shows the mismatch actually degrading retry outcomes,
  not just producing an unused hint.
- A corpus surfaces a codegen prompt style that follows hints more
  literally, removing the incidental safety this entry currently
  relies on.

### Anti-patterns — do NOT apply

- ❌ Treating this as proof the memory system "doesn't work." The
  confirmed case is simultaneously proof the `RECOVERED` write path
  works end-to-end — wrong-detail recall and functioning recall are
  not mutually exclusive; see the `code.txt` persistence entry in
  `CHANGELOG.md` for the same evidence read the other way.
- ❌ "Fixing" this by hardcoding a semantic mapping for common
  identifier variants (`user_id`/`uid`, `order_id`/`oid`, etc.). That
  is a special case dressed as a fix, and quietly encodes assumptions
  a different corpus won't hold.

---

## L9. A named input that does not exist is treated as a recoverable failure — the runtime can "recover" a task that should hard-fail

### Symptom

`csv_recovery_missing_file` (`reforge/benchmark/cases.py:71`) asks the
runtime to read `nonexistent_data.csv`, a file that is not there. The case
declares `expected_outcome="FAILED"` — "file truly missing — should fail
after retry exhaustion". In the recorded run
(`docs/benchmark_sample.md`) it came back `RECOVERED` in 2 attempts with
an eval score of 1.00, and is marked FAIL in the suite only because
expected ≠ actual. The runtime did not report that it could not do the
task; it produced a passing answer for a task whose input does not exist.

### Root cause

Nothing in the pipeline distinguishes "the input this request names is
absent" from an ordinary recoverable execution error.
`FileNotFoundError` maps to root cause `missing_file`
(`reforge/memory/fingerprint.py:204`) — a normal recoverable fingerprint
that feeds recall and a `repair_hint` like any other. `TaskIntent`
(`reforge/runtime/policy/task_intent.py:15-20`) has no value covering
"the request names an input that isn't there", so the governor's
intent-driven STOP path never applies and the retry proceeds on budget.
Codegen is then free to satisfy the request some other way — which, for
this class of request, is exactly the behaviour that should not happen.

### Right fix (deferred)

Classify an absent declared input as a **precondition** failure rather
than an execution failure — checked once against the task's declared
inputs, before or at the first attempt — so the governor stops instead of
retrying. This is the intent/precondition axis, not the retry-policy
axis: the retry loop is behaving correctly given the classification it
was handed.

### Why defer

- Evidence is one descriptive run (n=1, no seeds, no CI). It is a tuning
  signal, not a measured defect rate.
- The fixture itself is weak — the sibling experience-benchmark fixtures
  in the same family are flagged as too easy in
  `docs/experience_benchmark.md` §8.5 and are scheduled for rework;
  pinning behaviour against a fixture already slated to change would
  encode the wrong target.
- Not on the pre-registered eval path: the Phase 0/1 BIRD corpora contain
  no absent-input case, so this axis has no calibrated instrument behind
  it (see `docs/eval/PHASE0_CORPUS.md`).
- A precondition gate has a real false-STOP cost: tasks that legitimately
  create a file before reading it would look identical at check time.

### Trigger to revisit

- A reworked fixture (seeded, repeated) shows the over-recovery is
  reproducible rather than a single-run artefact.
- A real workload produces a confidently wrong answer built on a
  substituted input — i.e. the over-recovery reaches the final answer,
  not just the outcome label.

### Anti-patterns — do NOT apply

- ❌ Making `FileNotFoundError` globally non-retryable. That kills the
  legitimate recovery this same fingerprint exists for (the
  underscore-vs-hyphen path-typo case, `experience_cases.py:106`), which
  is a genuine repair, not an over-recovery.
- ❌ Keyword-matching the request for "nonexistent" / benchmark filenames.
  That pins the benchmark, not the behaviour.
- ❌ Reading this as evidence that self-heal "doesn't work". The same run
  recovered every other `csv_recovery` case correctly; the defect is in
  where the boundary sits, not in the loop.

---

## L10. Two memory-recall paths use divergent admission strategies

### Symptom

Two independent recall subsystems gate admission differently:

| Path | Entry | Admission | Feeds |
|---|---|---|---|
| repair_hint | `ExecutionMemory.recall_similar` (`reforge/memory/execution_memory.py`) | must match ≥1 *qualifying* structural signal (failure_mode / root_cause / a specific fingerprint field); `domain` + request-word overlap are ranking tie-breakers only | `ClassifyStage` → `ctx.repair_hint` → the next retry's codegen prompt |
| planner / CLI | `MemoryRetriever.search` (`reforge/memory/retrieval.py`) | `score > 0`, where request-word overlap (`×0.3`), tag overlap (`×0.5`), and `domain` (`+3.0`) each add to that score **independently** — a candidate can be admitted on low-specificity signals alone | `CompositeMemorySubstrate.recall / recall_for_planning` → `PlannerMemoryContext.build`; `cli/commands/history.py` display |

As of 2026-08-02 the repair_hint path was tightened to admit on
structural-signal existence (方案甲, see L8's dated update). The
planner/CLI path was deliberately left on `score > 0`.

### Why the two were NOT unified (the premise, not just the verdict)

- **Consumer blast radius differs.** `recall_similar`'s top hit is
  forwarded verbatim as `repair_hint` into the next retry's codegen prompt
  — a wrong record can steer generated code (that is L8). `search`'s output
  only (a) prepends a short "past experience" summary to the *planner*
  prompt (`planner_context.py` truncates each record to ~60 chars) and (b)
  renders a human-facing CLI history list. Both are advisory context a
  human or the planner reads, not a literal patch — the noise tolerance is
  materially higher, so the same admission laxity costs less there.
- **The query interface is not shared.** `search` scores against a
  free-form text query and carries no typed `failure_mode` /
  `problem_signature` argument, so the qualifier concept ("match a
  structural signal") does not map onto it without also changing its
  signature and every call site.
- **Unifying requires mirroring a third scorer.** `retrieval._score` is
  deliberately duplicated by `sqlite_substrate._score` ("mirrors
  MemoryRetriever._score() so retrieval quality is identical across
  backends"). Any admission change in `retrieval.py` must be applied to
  both or the JSONL and SQLite backends rank differently — a larger,
  separately-testable change than the repair_hint fix was.

### Right fix (deferred)

If the planner/CLI path is later shown to inject misleading context, give
`MemoryRetriever.search` (and its `sqlite_substrate` mirror) the same
qualifier/tie-breaker split: admit on a structural-signal match, demote
`domain` and keyword/tag overlap to ranking only.

### Why defer

- No measured evidence the planner/CLI noise changes outcomes; the planner
  treats these records as loose hints and truncates them heavily.
- The change spans two mirrored scorers plus `search`'s signature and call
  sites — out of scope for the repair_hint fix, which was intentionally
  minimal.

### Trigger to revisit

- A measured planning-quality axis shows low-specificity recall degrading
  plans, or
- the CLI history view is repurposed as an automated signal rather than a
  human-read display.

### Anti-patterns — do NOT apply

- ❌ Copy-pasting `recall_similar`'s gate into `search` without also
  updating `sqlite_substrate._score`. Silently diverges the two backends —
  the exact failure the mirror comment exists to prevent.
- ❌ Raising a bare numeric threshold (`score > K`) in `search` instead of
  separating qualifier from tie-breaker. Reintroduces the coupling between
  weight tuning and admission that 方案甲 removed on the repair_hint path.

---

## L11. Docker isolation stops at capability drops — `--user` and mount separation are one coordinated change, not two flags

### Symptom

`DockerBackend`
(`reforge/runtime/infrastructure/execution/backends/docker_backend.py`) caps
network, memory, cpu and pids, drops all Linux capabilities and blocks setuid
escalation. Two gaps remain, and neither closes on its own:

| Gap | Current state | What it exposes |
|---|---|---|
| Process identity | container runs as **root** (no `--user`) | anything writable inside the container is writable by uid 0 |
| Workspace mount | `-v <workspace>:/work` — **read-write, whole tree** | generated code can modify or delete any file in the caller's project |

The second is the larger one: the backend isolates the host *around* the
workspace while handing over the workspace itself — the part with value in it.

Worth recording how long that read wrong: the mount was cited as *evidence of*
isolation (`full (-v workspace:/work)` in the architecture table) while the
same module's class docstring stated the root filesystem was writable. The
identical misunderstanding sat in three places — module docstring, class
docstring, architecture table — so it was not a typo but what was actually
believed at the time, and nothing in the project compares two descriptions of
one thing for agreement. A contradiction survives as long as no one reads both.

### Why they cannot land separately

- **`--user` alone breaks writes.** The mount is owned on the host by the
  invoking user; a container process running as uid 1000 has no write
  permission on it. On Linux every script that writes an output file fails
  with EACCES. On Docker Desktop (Windows/macOS) the file-sharing layer
  usually masks this — the flag looks harmless on the development machine and
  fails on Linux CI or a Linux deployment.
- **Mount separation alone leaves root.** Splitting into `/input:ro` plus a
  writable output dir removes the destruction risk but not the privilege one.
- **`--user` needs what the split provides.** For a non-root uid to write
  anything it needs a directory *we* create with ownership we control — which
  is exactly the output dir the split introduces.

### The split is a codegen-contract change, not a flag change

The current contract is "read and write both happen in `/work`". Splitting it
changes what a relative path means: `pd.read_csv("sales.csv")` resolves
differently depending on where `-w` points. Generated code knows nothing of the
new layout, so `reforge/models/prompts/templates.py` has to change with it.
Cost is dominated by that, not by the docker arguments.

### Right fix (deferred)

Add an opt-in strict mode rather than changing the default:

```python
DockerBackend(isolation="strict")   # --user + /input:ro + writable output dir
DockerBackend()                     # unchanged behaviour — current default
```

Keeping the default untouched matters because the failure mode is
platform-dependent: flipping it would break Linux users while passing every
test run on a Windows or macOS development machine.

### Why defer

- Cross-layer: docker arguments + prompt templates + path conventions inside
  generated code.
- The breakage it risks is invisible on the primary development platform
  (Windows), so it needs a Linux verification pass before it can be trusted.
- No current use case runs adversarial code; the backend's own docstring
  scopes it to code we are willing to run.

### Trigger to revisit

- The runtime is pointed at code from an untrusted source (a shared demo, a
  multi-tenant deployment, user-submitted tasks), or
- a real incident in which generated code damages files in the caller's
  workspace.

### `--read-only` is separately a non-goal

Not part of this item. The argument is recorded where it applies — the
`DockerBackend` class docstring — and is not repeated here.

### Anti-patterns — do NOT apply

- ❌ Adding `--user` on its own because it is "just one flag". It is the half
  of the change that breaks writes, and it breaks them on a platform other
  than the one it will be tested on.
- ❌ Verifying strict mode only on Docker Desktop. The file-sharing layer
  hides exactly the permission failure the mode has to get right.
- ❌ Flipping the default to strict once it works. The failure mode is
  environment-dependent; opt-in is what keeps a working default working.

---

## L12. `extract_error_type` can be poisoned by a file path containing "Error"

### Symptom

`reforge/runtime/infrastructure/error_extraction.py` scans a traceback line by
line for the substrings `Error` / `Warning` / `Exception`, returning the first
hit expanded backwards over letters and dots. Stack-frame lines are scanned
like any other. A traceback produced from a path containing one of those words
— `/home/me/ErrorDemo/run.py`, `C:\work\ErrorLogs\` — yields
`error_type="Error"` off the frame, before the real exception line is reached.
That value flows into `execution_node`
(`reforge/runtime/orchestration/graph/nodes/execution.py:15`) and from there
into failure-mode classification and the fingerprint's fallback path.

### Current exposure

- **Docker backend: immune, incidentally.** The program is piped over stdin,
  so the user frame reads `File "<stdin>"` and carries no host path. This is a
  *side effect* of the stdin change (A5/B5), not a fix of this defect.
- **Subprocess backend: still exposed.** Its tracebacks carry the real
  tempfile path, and the workspace path appears in frames from user code.

### Two ways to fix it, both with limits

| Approach | Change | Limit |
|---|---|---|
| Skip frame lines | `if line.startswith('File "'): continue` — ~2 lines | Blocks only the standard frame shape. A path inside a *message* (`PermissionError: ... '/x/ErrorLogs/a.csv'`) still poisons it |
| Anchor the match | reuse `fingerprint._last_error_line`'s regex `^[A-Za-z][A-Za-z0-9_.]*(?:Error｜Exception｜Warning)\s*:` — ~5–10 lines | Correct for real tracebacks, but rejects the non-traceback text the loose scan currently accepts |

### The cost is semantic evaluation, not code

`extract_error_type` is called on `stderr`, which is not always a Python
traceback: shell-level errors, library messages printed directly, tool output.
The loose scan extracts something from those; an anchored match returns the
`default` (`"UnknownError"` at the execution-node call site). That shifts
failure-mode classification and therefore retry policy.

Sized honestly: under 10 lines of code, 2–3 tests, plus a recall-regression
pass over real stderr samples to count how many currently-typed failures would
become `UnknownError`. The evaluation *is* the work.

### Structural observation

`fingerprint._last_error_line` and `extract_error_type` do the same job with
opposite strictness — one anchored, one substring-scanning. Merging them is the
more thorough fix and is **not** recommended as an incidental change: it would
change what `error_type` means for every consumer, a blast radius larger than
the defect it closes.

### Trigger to revisit

- A wrong `error_type` is traced to a path, or
- the recall-regression sample is collected for another reason, making the
  evaluation nearly free.

### Anti-patterns — do NOT apply

- ❌ Swapping in the anchored regex without the recall pass. It trades a rare
  poisoning for a systematic loss of typing on non-traceback stderr.
- ❌ Treating the stdin change as having fixed this. It removed one source of
  poisoned paths on one backend; the defect itself is untouched.

---

## L13. The subprocess backend hands the runtime's entire environment — credentials included — to generated code

### Symptom

`SubprocessBackend`
(`reforge/runtime/infrastructure/execution/backends/subprocess_backend.py`)
builds the child environment as:

```python
child_env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
```

Every variable the runtime holds — `DASHSCOPE_API_KEY`, `VISION_LLM_API_KEY`,
`TAVILY_API_KEY`, `OPENAI_API_KEY`, and anything else in the shell that started
it — is visible to LLM-generated code. Nothing filters it. This is the default
backend.

### The risk model is not "hostile code"

Generated code does not have to be adversarial to leak a key. A single
`print(os.environ)` while debugging, a `traceback` that renders locals, or a
library that echoes its configuration is enough. The realistic trigger is
ordinary, not malicious.

Where it goes from there — traced, not assumed:

| Sink | Carries the value? |
|---|---|
| stdout → `RuntimeState.stdout` → CLI display | yes, in memory and on screen |
| tracing span (`observability/tracing/collector.py:51`) | **no** — records `stdout={len} chars`, the length only |
| `ExecutionMemory.record(...)` | **no** — the signature has no `stdout` parameter |
| **stderr → `traceback`** | **yes, and this is the serious one** |

The last row is the path that matters. `execution_node` assigns
`traceback = result.stderr` on non-zero exit; `ExecutionMemory.record()` takes
`traceback=` and persists it to `data/execution_memory.jsonl`. From there
`recall_similar` can surface it as a `repair_hint`, which is injected verbatim
into the next retry's codegen prompt — and that prompt goes to an external LLM
provider. A credential printed to stderr therefore reaches disk, survives the
session, and can be transmitted off-host on a later run.

### The finding this whole item rests on

**Generated code has no need of any credential.** Verified, not assumed:

- `reforge/models/prompts/templates.py` contains no instruction that generated
  code read an API key or environment variable (no matches).
- The consumers of those keys are the *runtime's own* skill-registration
  checks — `reforge/runtime/skills/builtin/__init__.py:68,73,83` read
  `TAVILY_API_KEY` / `VISION_LLM_API_KEY` to decide which skills to register.
  That happens inside the runtime process, not in the sandboxed child.
- Skills are dispatched by the runtime over a JSON protocol; they are not a
  library the generated program imports.

So the exposure buys nothing. It is not a price paid for a capability — which
is what makes this a limitation rather than a design trade-off.

**If that ever stops being true — if generated code is given a task that calls
an API directly — this entry must be re-evaluated before the fix is applied.**
The whole argument below depends on the child needing no secrets.

### Right fix (deferred): an allowlist, default-deny

Pass only what execution requires — `PATH`, `PYTHONIOENCODING`, `SystemRoot`
(Windows), `TMPDIR`/`TEMP`, `HOME`/`USERPROFILE`, `LANG`/`LC_*` — and drop
everything else.

### Why not a denylist or prefix filter

Filtering `*_API_KEY` / `*_TOKEN` / `*_SECRET` is the tempting version and it
is wrong: `OPENAI_ORG`, `AWS_PROFILE`, `GOOGLE_APPLICATION_CREDENTIALS` and
`DATABASE_URL` are all sensitive and match none of those patterns. A denylist
fails silently on every variable nobody thought of, and the set of names it
must anticipate grows with every new integration. Default-deny inverts that:
the failure mode becomes a missing variable that breaks loudly, not a leaked
one that breaks nothing.

### Why defer

Same shape as L12 — short code, long verification:

- **Cross-platform.** Dropping `SystemRoot` on Windows breaks parts of the
  standard library and several C-extension packages outright. The allowlist has
  to be validated per-platform, and the primary development platform here is
  the one most likely to break.
- **Unknown consumers.** Generated code may legitimately depend on variables
  that are not credentials — `MPLBACKEND` for headless matplotlib, proxy
  settings (`HTTP_PROXY`/`HTTPS_PROXY`), `PYTHONPATH`. Each needs a decision,
  and the evidence for those decisions is real runs, not reasoning.

### Trigger to revisit

- The runtime is run with credentials that are not the developer's own
  (shared deployment, CI with production keys, multi-tenant use), or
- `DockerBackend` becomes the default, which would make this moot for the
  default path and reduce the item to a subprocess-only caveat.

### Anti-patterns — do NOT apply

- ❌ Adding a denylist because it is quicker. It leaves exactly the variables
  nobody enumerated, which are the ones that leak.
- ❌ Scrubbing credentials from stderr instead of from the environment. That
  treats one sink of many and leaves the child holding the secrets.
- ❌ Assuming `DockerBackend` makes this irrelevant. It does not pass host
  environment through, but it is opt-in; the default path is this one.

---

## L14. The MCP client is validated only against an in-repo fixture server — several protocol gaps cannot be falsified until a real server is bound

### Symptom

`discover_and_register()` (`reforge/runtime/mcp/discovery.py`) is called
from exactly one place in the repository: `reforge/tests/test_mcp_integration.py`.
The only server it has ever spoken to is `reforge/tests/_mcp_test_server.py`, a
179-line stdlib fixture. Nothing in `reforge/cli/`, `reforge/benchmark/`,
`scripts/`, or `default_skill_registry()` binds an MCP server, and no config
file carries an `mcpServers` key.

That is not itself the limitation — the transport is genuinely exercised
end-to-end over real pipes and a real subprocess. The limitation is what that
narrow surface *hides*: a fixture that is well-behaved by construction cannot
falsify the client's handling of servers that are not.

### Deferred debt — real gaps, currently unreachable

These are defects, not trade-offs. Each is latent only because the fixture
server never triggers it.

| Gap | Where | What triggers it |
|---|---|---|
| `env=` is passed straight to `Popen` without merging `os.environ` — Popen *replaces* the environment rather than extending it | `session.py` `connect()` | Any server needing `PATH`. The test suite already works around this by hand (`test_mcp_integration.py` builds `{**os.environ, ...}`) — a workaround in a test is the tell |
| No process group / job object, so grandchildren survive `terminate()` | `session.py` `connect()` | `npx`-, `uvx`-, or shell-launched servers, i.e. most published ones |
| JSON-RPC `error.code` / `error.data` are flattened into an f-string; callers cannot branch on the code | `client.py` `request()` | Any caller wanting to distinguish "method not found" from "invalid params" |

### Scope-bound gaps — not "we don't need this"

The following are unimplemented, and the honest framing is *not* that they are
unnecessary. It is that **within the current binding surface — one fixture
server we control — there is no way to falsify a decision about them.** They
become real requirements the moment a third-party server is bound, and each
should be re-opened at that point rather than defended.

| Unimplemented | Becomes a real requirement when |
|---|---|
| `tools/list` pagination (`nextCursor` is ignored; only page one is read) | A server advertises more tools than fit one page — the failure is silent tool loss, not an error |
| Notification dispatch (`notifications/*` are read and discarded, no handler registry) | A server emits `tools/list_changed`; the `list_tools` cache is currently never invalidated |
| Capability negotiation (client sends `capabilities: {}`; the server's returned `capabilities` is discarded) | A server gates methods on declared capabilities, or the runtime wants `roots`/`sampling` |
| `protocolVersion` validation (pinned to `"2024-11-05"`; the server's reply is not checked) | A server speaks only a later revision — currently the mismatch is silent |
| Server-initiated requests (a frame with an unrecognised `id` is discarded and never answered) | A server issues `sampling/createMessage` or `roots/list` and blocks waiting for a reply |

### Why this framing matters

The distinction is the point of this entry. "We don't need pagination" is a
claim about MCP servers in general and it is false. "We cannot yet tell whether
we need pagination, because we have only ever talked to a server with five
tools" is a claim about *this repository's evidence*, and it is true. The
second framing also names its own expiry condition; the first does not.

### Not in this list: three that were fixed

The same boundary has now been drawn three times: **if the fixture server can
trigger it, it is a defect rather than a deferred decision**, and it belongs on
neither table above.

`timeout_s` was accepted by `MCPClient.request()` and never used — the read
loop blocked forever. That one was not scope-bound (it fired against the
fixture server as readily as against anything else) and has been fixed: reads
now run on a dedicated thread and honour a deadline. A parameter that promises
behaviour it does not deliver is a defect, not a deferred decision.

`kill()` without a following `wait()` (formerly deferred-debt row 2) is fixed:
`shutdown()` now reaps with `wait(timeout=1.0)` after SIGKILL. Calling it
"latent" was itself the error — it was latent not because the fixture is
well-behaved but because the fixture had no *way* to misbehave. The fix adds
`REFORGE_TEST_MCP_IGNORE_SIGTERM=1` to `_mcp_test_server.py` so the path can be
falsified. That test skips on Windows (`kill()` is `terminate()` there and
zombies do not exist) and executes only on Linux CI — see L15 on the local/CI
skip sets being complementary.

Malformed stdout lines dropped without a counter (formerly deferred-debt row 5)
is fixed: `MCPClient` counts them, retains the last 3 as samples, and appends
that as a suffix to timeout and EOF failure messages. The value is not the
counter but *where it surfaces* — a server interleaving logs into stdout was
previously indistinguishable, in the error message, from one that is simply
slow. `REFORGE_TEST_MCP_STDOUT_NOISE=1` makes it falsifiable.

### Trigger to revisit

A real MCP server is bound (the `discover_and_register` command argument points
at anything other than `_mcp_test_server`). At that moment the "deferred debt"
rows above stop being latent, and every "scope-bound" row must be re-evaluated
against that specific server's behaviour.

### Anti-patterns — do NOT apply

- ❌ Implementing the scope-bound items speculatively before a real server is
  bound. Each needs a concrete server to validate against; building them now
  produces untested code justified by imagined requirements.
- ❌ Citing "we only use one server" as a *reason* these are fine. It is the
  reason they are unfalsifiable, which is the opposite of fine.
- ❌ Merging `os.environ` into `env=` inside `MCPSession.connect()` without
  deciding the credential-passing question first — see L13. The subprocess
  backend's environment leak has the same shape, and MCP servers are spawned
  processes too.

---

## L15. The local and CI skip sets differ and are complementary — neither run has ever executed the whole suite

### Symptom

Both runs reported the same summary line. They did not run the same tests.

| | passed | skipped | skipped *there and not here* |
|---|---|---|---|
| local (this machine) | 1853 | 7 | 3 × docker integration |
| CI (ubuntu-latest) | 1853 | 7 | BIRD, Pillow, playwright |

**Collection conditions for the local row — data, not context.** It was measured
with **Docker Desktop not running**. That single fact is the entire reason the
three docker integration tests show up as its exclusive skips; nothing about the
machine, the OS or the checkout produced them. The condition was not recorded at
the time and is written in retrospectively (2026-08-07), which is itself an
instance of what this entry is about: *"local" is not a place, it is a reading of
one machine in one state.* A measurement whose conditions go unwritten silently
becomes a claim about the wrong thing the moment those conditions change — and
below, they changed.

Four skips are common to both and deliberate (2 × LLM smoke, the docker
sentinel, tavily — each needs a credential or an explicit opt-in). The
remaining three on each side were **complementary**: docker was available only
in CI, while the BIRD dataset, Pillow and playwright were installed only
locally. Three against three, so `passed` and `skipped` came out identical on
both sides — 1853 and 7 — while the underlying sets did not overlap.

Of 1860 collected tests, **no single run has ever executed more than 1853**,
and the two 1853s are different 1853s.

### Why the numbers concealed it

The summary line is a count, and counts are invariant under substitution. A
test that stops running and a test that starts running cancel out perfectly.
Nothing in `passed`/`skipped` can distinguish "the same suite ran twice" from
"two different subsets ran once each" — only the SKIPPED list can, and that
list only became visible on the run page in 1a363a0. The discrepancy was
found the first time a human read that list.

### What has already been narrowed

Adding Pillow and playwright to the `[test]` extra, and making `dev` reference
`test` instead of repeating it, removes two of the three CI-only skips. What
remains is asymmetric in a way that matters:

| Gap | Direction | Nature | Closable? |
|---|---|---|---|
| docker (3 tests) | present in CI, absent locally | environment **state** — Docker Desktop simply was not running; starting it closes the gap with no code change | yes, and it has been — but state flips back, see below |
| BIRD (1 test) | present locally, absent in CI | environment **content** — a 2.1 GB dataset that is `.gitignore`d (`data/`) and cannot be shipped to a runner cheaply | in principle, via a fixture subset |
| POSIX zombie-reap (1 test) | present in CI, absent locally | environment **platform** — `test_kill_escalation_reaps_the_child` asserts that `shutdown()` reaps after SIGKILL. On Windows `kill()` *is* `terminate()`, TerminateProcess cannot be ignored, and zombies do not exist | **no** — the asserted path does not exist on the other platform |

They point in different directions and cannot be closed the same way. Treating
"make the skip sets match" as one task is the mistake this table exists to
prevent. The third row makes that sharper than the first two did: docker is a
service you can start and BIRD is a file you could ship, but no amount of
environment work makes a zombie exist on Windows. It is the first asymmetry here
that is **permanent by construction**, and a permanent gap cannot be managed by
closing it — only by being seen.

### Re-measured 2026-08-07: the same illusion, on this entry's own numbers

Local was re-run with Docker Desktop **running**, on a tree that also adds one
POSIX-only test. The local skip set moved by four:

- **−3** docker integration — the daemon was up (`docker version` reported
  `linux/29.4.0`), so those tests executed instead of skipping
- **+1** `test_kill_escalation_reaps_the_child`, added by the MCP defect fixes

Local is now **5 skips, measured**, whole suite, exit 0:

```
1859 passed, 5 skipped in 95.06s (0:01:35)
```

SKIPPED: 2 × LLM smoke, the docker sentinel, tavily, POSIX zombie-reap. The
arithmetic reconciles exactly against the baseline row, which is worth stating
because it is the only reason the new numbers can be trusted as the *same*
measurement rather than a differently-shaped one: collected goes 1860 → 1864
(+4 MCP tests), and passed goes 1853 → 1859 (+3 docker integration now
executing, +4 new tests, −1 of them skipped here).

A note on obtaining that line, since it cost three runs: `addopts` in
`pyproject.toml` already contains `-q`, so passing `-q` again on the command
line yields `-qq`, and `-qq` **suppresses the final count line entirely**. Two
runs reported only `exit 0` and a SKIPPED list, with no totals. Run the suite as
`pytest reforge/tests -rs` and let `addopts` supply the `-q`.

CI's side is **derived, not observed** — `gh` is not installed on this machine
and no CI run has been read since. Derivation: the baseline 7 loses Pillow and
playwright (cb04992 moved them into the `[test]` extra), keeps BIRD, and gains
nothing, because the new POSIX test *runs* on ubuntu. CI: **5, derived**.

| | skipped | skipped *there and not here* |
|---|---|---|
| local (Windows, docker running) | 5 — measured | POSIX zombie-reap |
| CI (ubuntu-latest) | 5 — derived | BIRD |

**Equal counts, disjoint exclusive sets — for the second time.** The first time
it was three against three and both sides read 7. This time it is one against
one and both sides read 5. The mechanism is unchanged and is already stated in
"Why the numbers concealed it": counts are invariant under substitution.

What is new is *where* it recurred. Not in some unrelated suite — in **this
entry's own table**, during the interval between writing the warning and
re-reading it. A documented failure mode that reproduces inside the document
describing it falsifies, by example, the assumption that a human reading the
SKIPPED list is sufficient control.

### Adjudication: is the POSIX-only test the third asymmetry?

**Yes. The trigger has fired.** The rule binds whoever set it, so the reasoning
is recorded rather than asserted.

**On the wording.** The trigger reads "A third skip-set asymmetry appears", with
"(a fourth optional dependency, a new credential-gated test)" appended. The new
test is neither. But the parenthetical enumerates *ways one might appear*, not
conditions of appearance; the head clause governs, and this is a skip-set
asymmetry under the entry's own definition — a test that runs on one side and
not the other for environmental reasons.

**The counter-argument, stated fairly.** With docker running, the
*instantaneously active* asymmetries are BIRD and POSIX: two, not three. One
could argue the threshold has not been reached.

**Why it fails.** Two reasons. First, docker's closure is a state observation
from a single day; the trigger's own third bullet asks for Docker Desktop to
become "reliably available", and one reading of `docker version` is not
reliability. Booking it closed is precisely the inference the anti-patterns
forbid — one measurement treated as a property. Second, the trigger counts
asymmetries *appearing*, not live ones outstanding. Read as a live count it
would be effectively unfirable, since any asymmetry can be masked on the day
someone happens to check.

**The decisive evidence is not definitional.** It is that the failure mode
reproduced above on this entry's own numbers, and that the new asymmetry is the
first that **cannot be closed at all**. Both original two were arguable partly
because each had a route to closure; "we will fix it and the question goes away"
is not available for a platform gap.

**Author's discount, declared.** This asymmetry was introduced by the same
change now invoking the rule. That warrants a stricter reading, not a lenient
one — the cheapest way to keep a self-imposed rule from ever firing is to judge
one's own case generously. Noted so that leniency, if present, is visible.

**Consequence.** The CI-side snapshot moves from "open question, worth doing" to
**due**. Deliberately not implemented here: the entry already states it must be
adopted as its own change, and folding it into a commit about MCP defects would
break that instruction on the same page that issues it.

### Why BIRD is not a CI candidate

Two separate reasons, and they carry different weight:

1. **Size — measured.** `data/bird` is 2.1 GB locally (`dev.json` itself is
   only 724 K; the bulk is `dev_databases/*/*.sqlite`, which the test needs
   because its `has_db` callback stats those files). Downloading or caching
   that per run is not proportionate to one assertion.
2. **Distribution terms — NOT verified.** There is no LICENSE, README or terms
   file under `data/bird`, and no check of BIRD's redistribution conditions has
   been made. This is recorded as an **open question, not a known obstacle** —
   it may well be permissive. It must be resolved before any workflow copies
   the dataset, but reason (1) is sufficient on its own to keep it out of the
   ordinary CI path.

The test it guards, `test_frozen_list_matches_rule_on_real_bird_data`, asserts
that the frozen `PHASE1_CASE_IDS` really is what the selection rule produces
from the real dataset. The rule's own determinism and exclusion logic are
covered by a sibling test on synthetic entries, which does run in CI; what is
missing is only the alignment against real data — a drift sentinel for the
benchmark's reproducibility, not a runtime regression test.

### Relationship to the docker sentinel

`test_docker_is_available_where_required` exists because a fully-skipped
selection still exits 0, so a runner that lost its daemon would keep reporting
green while testing nothing. That sentinel defends one direction: a skip that
should have been a pass.

This entry is the same failure mode seen from the other side. **The skip set is
a function of the environment, and nothing asserts it.** The sentinel covers
one dependency, in one environment, because someone anticipated that specific
loss. Every other optional dependency — Pillow, playwright, BIRD, tavily,
network access — silently reshapes the skip set with no signal at all.

### Open question: can skip-set drift be made an explicit signal?

Asked and reasoned afresh, not inherited from the earlier rejection of
"assert exactly N skips". That form was rejected because a bare count must be
edited on every intentional change and carries no semantics. A *set* is a
different proposition: its diff names what moved.

**Assessment: worth doing, in one specific form — a CI-side snapshot.**

- **Mechanism.** Run pytest with `--junitxml`, extract the node ids of
  `<skipped>` cases, and diff against a checked-in expected list. Node ids are
  used deliberately in place of the `SKIPPED path:line:` text, whose line
  numbers shift whenever anything above the test is edited — a false alarm
  source with no semantic content. Node ids move only when a test is renamed,
  added or removed, which is exactly when a human should confirm the change.
- **What it would have done here.** The Pillow/playwright fix would have turned
  CI red with "these two no longer skip — update the list". That is the desired
  outcome: the confirmation currently depends on a human predicting 1855 and
  checking it by eye.
- **Scope limit, and it is real.** This can only be applied to CI. Local
  environments are not enumerable — every contributor's machine has a different
  combination of docker, credentials and optional packages, so there is no
  single expected set to assert against. It therefore covers **one side of the
  very asymmetry this entry is about**. That is still the more valuable side:
  a developer watches local output constantly and would notice a change, while
  CI is usually consulted only for its colour.
- **Why not a sentinel per dependency instead.** The sentinel form is strictly
  stronger — it asserts a *capability* ("docker must actually work here"), not
  a *phenomenon* ("this test skipped"). But it needs one env flag and one test
  per dependency, plus a decision about which environments declare which
  dependency mandatory. That is a larger, more opinionated change; the snapshot
  is the cheap instrument that reports drift without adjudicating it.

**Not implemented here.** It is a mitigation with its own maintenance surface,
not a fix for this limitation, and it should be adopted as a deliberate change
rather than folded into an unrelated commit.

### Trigger to revisit

- ~~A third skip-set asymmetry appears (a fourth optional dependency, a new
  credential-gated test). Two was arguable; three means the snapshot above
  should stop being an open question.~~ **FIRED 2026-08-07** — see the
  adjudication below. The snapshot is no longer an open question; it is due, and
  due as its own change (this entry's own instruction: not folded into an
  unrelated commit).
- BIRD's distribution terms are established, or the dataset gains a small
  fixture subset sufficient for the frozen-list assertion.
- Docker Desktop becomes reliably available locally, which would leave BIRD as
  the sole asymmetry and make the whole item much cheaper to close.

### Anti-patterns — do NOT apply

- ❌ Asserting a skip *count*. It is invariant under substitution — the exact
  blindness that produced this entry, since both sides reported 7, and then
  reproduced it at 5.
- ❌ Treating "make local and CI skip the same things" as one task. The
  remaining gaps point in different directions and one of them cannot be closed
  at all (see the table above).
- ❌ Reading the local row as a property of "local". It is one machine in one
  state; the docker skips vanished the day the daemon was running. Any local
  measurement quoted without its collection conditions is a claim about the
  wrong thing.
- ❌ Deleting the skipped tests to make the sets match. They cover real
  branches; the problem is that nobody is told when they stop running.
- ❌ Reading equal summary lines as equal coverage. That inference is what
  failed here, and it failed while both numbers were correct.

---
