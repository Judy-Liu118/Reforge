# Known Limitations

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

Each list scans `state.user_request` with `re.search` against hand-
curated Chinese + English phrases. Misses are inevitable — "make it
fail on purpose" never matches `故意.*报错`; `0.85` printed by a
"don't explain, just score" task fails `MIN_OUTPUT_LENGTH = 5`; a chart-
extraction task that happens to use the word "build" can route through
`DATA_TASK_KEYWORDS` while one that says "compute" does not.

Review correspondence: items ① (directive hardcoding), ③ (regex misses),
④ (output-length floor), ⑦ (keyword breadth) — all four are surface
manifestations of the same root.

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
  in `vision_routing.py` — review item ②, already shipped). These
  buy time without making the larger problem worse.

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

Four constructors / argument signatures carry the same hardcoded
default for max retries:

| Location | Signature |
|---|---|
| `reforge/runtime/policy/retry_policy.py:25` (`RetryPolicy.decide()` parameter) | `max_retries: int = 2` |
| `reforge/runtime/policy/policy_engine.py:21` (`PolicyEngine.__init__`) | `max_retries: int = 2` |
| `reforge/runtime/orchestration/governor/policy_stage.py:12` (`PolicyStage.__init__`) | `max_retries: int = 2` |
| `reforge/runtime/orchestration/governor/engine.py:31` (`ExecutionGovernor.__init__`) | `max_retries: int = 2` |

The production runtime path goes through
`reforge/runtime/orchestration/graph/nodes/retry_decision.py:74`:

```python
governor = ExecutionGovernor(max_retries=config.max_retry)
```

which reads `config.max_retry = int(os.getenv("MAX_RETRY", "3"))`
(`reforge/config.py:18`). The bypass `_naive_resolution`
(`retry_decision.py:50`) likewise reads `config.max_retry` directly.
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
