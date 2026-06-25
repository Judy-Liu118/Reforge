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
