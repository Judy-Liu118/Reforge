# Changelog

All notable changes to Reforge. Format roughly follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions track the `pyproject.toml` `[project] version`.

## [Unreleased]

### Fixed
- **Evaluator false-negative pressure on contract-format output (L6 gating
  fix)** — `HeuristicEvaluator` penalized correct bare-scalar answers
  (`"5"`, `"-"`) via its length/digit plausibility checks even when the
  request itself pinned the output shape ("Print nothing else"). 100% of
  Phase 1's 169 evaluator false negatives attributed to this. The evaluator
  now detects an explicit output contract (generic phrases: "print nothing
  else", "output only …", 只输出 …) and suspends the length-based checks;
  emptiness, tracebacks, exit codes, and all anti-cheating checks are
  unchanged. Validated held-out (300 pool questions the Phase 1 picks never
  touched, seed 20260711): FN 42.7% → 0.0%, zero rejection-integrity
  regressions. Protocol + results: `docs/eval/EVALUATOR_CALIBRATION.md`;
  reproduce with `scripts/calibrate_evaluator_heldout.py`. Clears the
  governor-vs-naive axis for a fresh run — old Phase 1 records must not be
  re-scored (the evaluator drives runtime retry behavior).
- **Phase 0 driver: cold-start memory per (mode, seed) leg** — with the
  memory loop now live, all 54 calibration runs would have shared one
  `execution_memory.jsonl` (and the global reflection substrate); each leg
  now points `REFORGE_PROJECT_DIR` / `REFORGE_HOME` at a fresh tmp dir.
  Calibration re-run 2026-07-10 on the repaired loop: verdict **GO**, all
  four mechanism gates passed (`docs/eval/PHASE0_CALIBRATION.md`).
- **Sandbox subprocess backend runs generated code under `sys.executable`**
  instead of whatever `python` resolves to on PATH, so the sandbox sees the
  same dependency set `capability_check` assumed.
- **MCP test suite no longer depends on an editable install** — the spawned
  server subprocess gets the repo root prepended to `PYTHONPATH`.
- **memory → repair_hint → retry-prompt loop wired end-to-end** (previously
  four independent breaks left the headline recall claim dead in production):
  - `reflection_node` now snapshots each failed attempt
    (`SemanticState.last_failure`: error_type, suggested fix, structural
    fingerprint) so the failing context survives the recovery attempt
  - `RuntimeRunner` persists `(problem_signature → repair that worked)` to
    `ExecutionMemory` when a session ends RECOVERED — the store the governor
    recalls from finally has a production write path
    (`memory/writer.py::execution_record_from_final_state`)
  - `ClassifyStage` passes the current failure's fingerprint to
    `recall_similar`, activating the structural scoring weights
    (error_class / root_cause / domain) that previously never fired
  - `retry_decision_node` lands the governor's `repair_hint` on
    `semantic_state.repair_hint` (cleared when recall is empty), and
    `RetryContextData` / `build_retry_prompt` render it into the retry
    codegen prompt as a "Repair hint" section
  - End-to-end contract in `tests/test_repair_hint_e2e.py`: write side,
    read side, hint-clearing, and prompt rendering
- **Planner output is now consumed**: the plan lands on
  `semantic_state.plan` and is injected into the codegen prompt (previously
  it was written to `generated_code` and unconditionally overwritten —
  a dead LLM call per session). CLI formatter and trace collector read the
  new location
- **Intent classified once per session**: `IntentStage` reuses
  `semantic_state.task_intent` persisted by `retry_decision_node`, so an
  N-attempt session pays one intent LLM call instead of N (and intent can
  no longer flip mid-run between attempts)
- **CI**: removed phantom `REFORGE_DISABLE_LLM` env (referenced nowhere in
  code) and the `|| true` on the ruff step — lint failures now fail the
  build; the tree is ruff-clean (145 violations fixed)

### Removed
- Dead code islands with no production entry point: `runtime/tasks/`,
  `runtime/workers/`, `runtime/parallel/` (superseded by
  `decomposition/async_runner`'s ThreadPoolExecutor), and
  `runtime/policy/policy_engine.py` (unconsumed wrapper around
  `RetryPolicy`). The event↔state consistency validator moved from
  `runtime/bridge/consistency.py` to `reforge/tests/_consistency.py` —
  it is test infrastructure and now lives next to the contract tests
  that call it

### Added
- **L3 repeated-signature deliberate STOP (history-based unrecoverability,
  narrowed)** — the reflection node now appends each failed attempt's
  structural fingerprint to `semantic_state.failure_signature_history`;
  when 2 consecutive attempts share one identical fingerprint (same error
  class AND same target module/key/file/name, parsed deterministically from
  the traceback — not from LLM reflection text), `ClassifyStage` flips the
  classification to `failure_mode="repeated_signature"` and the governor
  issues a deliberate STOP (`repeated_failure_signature`) with budget
  remaining. The outcome resolver reports it as its own event instead of
  mislabeling it `RETRIES_EXHAUSTED`. Expected-failure intents
  (`RECOVERABLE_DEMO`) are exempt; the naive bypass arm is untouched.
  Landed **after** the Phase 1 runs — R1/R2 numbers measure the runtime
  without it, and any cost-savings claim requires a fresh run
  (`docs/KNOWN_LIMITATIONS.md` L3 status update). Validated by unit +
  integration tests only (`reforge/tests/test_repeated_signature_stop.py`).
- **Phase 1 BIRD ablation run 2 — post-calibration, the load-bearing
  result** (`docs/eval/PHASE1_BIRD_ABLATION_R2.md`, raw records in
  `docs/eval/phase1_records_r2.jsonl`; same locked corpus/protocol as run 1).
  Sensitivity appendix: evaluator FN 0.0% both arms, verdict **symmetric**
  — headlines stand unqualified. The success_rate null is real: 61.0% both
  arms (paired Δ 95% CI [-4.4, +4.4]pp); recovery_rate +6.5pp crosses zero
  (3 genuine recoveries / 100 governor runs, zero FN-driven retries). Cost
  overhead shrank from 3.1× to 1.4× tokens-per-solved after the evaluator
  fix. `write_report` now stamps the actual records path into the report
  header instead of a hardcoded default.
- **Phase 1 BIRD ablation: run and reported** (`docs/eval/PHASE1_BIRD_ABLATION.md`,
  raw records in `docs/eval/phase1_records.jsonl`): 20 pre-registered cases ×
  {governor, naive} × 5 seeds = 200 runs, graded by the SQL comparator.
  Primary result is an honest null — success_rate 65.0% in both arms
  (paired Δ 95% CI [-4.4%, +4.4%]) with the governor arm paying 3.1×
  tokens-per-solved and 3.2× wall-clock. The locked sensitivity appendix
  (PHASE0_METRICS v4 §4) returns **ASYMMETRIC**: the internal evaluator's
  false-negative rate on comparator-correct attempts is 80.8% (governor) vs
  52.3% (naive), so retries are dominated by re-solving already-correct
  answers (34/100 governor runs; 3 lost a correct answer; 5 genuine
  recoveries). Evaluator calibration is the gating fix before this axis is
  re-run. Harness: `reforge/benchmark/phase1/` (leg-granular resume,
  attempt-level comparator grading via `RuntimeRunner.stream()`,
  `--report-only` recompute).
- **Visual self-heal — vision codegen routing**: when the user request
  references a `target.png` in the workspace AND matches visual-reproduction
  intent (复刻 / reproduce / front-end / UI), `code_generation_node` routes
  to `LLMClient.for_vision_codegen()` and attaches the image to the codegen
  request as an OpenAI-style `image_url` content block. The model sees the
  actual pixels, not a lossy `describe_image` text transcription. Routing
  decision lives in `runtime/orchestration/graph/vision_routing.py` and is
  testable in isolation. Empirically lands a 1-shot 0.85 reproduction on
  the Claude UI mockup with `qwen-vl-max`
- **`CODEGEN_VISION_*` config fields** (base_url / api_key / model) with
  fall-through to `LLM_*` when empty, mirroring the pre-existing
  `VISION_JUDGE_*` pattern. `.env.example` updated with templates for all
  four LLM roles
- **`VISION_CODEGEN_SYSTEM` prompt** — separate system prompt for the
  vision codegen path (model sees the image directly, no `describe_image`
  call instructions); demonstrates worked HTML interpolation pattern with
  literal string copy-paste from the visible target
- **Subprocess backend preserves buffered stdout on TimeoutExpired**:
  previously `subprocess.run(timeout=...)`'s `stdout` and `stderr` were
  thrown away when the parent killed the child, so the CLI's
  `[stdout tail]` diagnostic block was empty on every timeout. Now both
  buffers are decoded (handling bytes/str ambiguity from the SDK) and
  surfaced. Tests cover the buffered-and-killed case plus the
  no-output case
- **`format_stdout_tail` CLI formatter** + wiring in `run_task` /
  `run_multistep_task` so a failed attempt prints its last 20 lines of
  stdout, including the `[reforge.step] <op>: <Ns>` timing prints. Lets
  the user see *which* step consumed the budget when a self-heal attempt
  times out, instead of just `[Error] Execution timed out after 300s`
- **`[reforge.step] <op>: start` lines** in `screenshot()`,
  `describe_image()`, `compare_images()` — emitted with `flush=True`
  before the long network call. Without this, a subprocess killed
  mid-operation left no trace of which helper was active, since the
  completion print only fired in the `finally` block
- **Anchored judge rubric** for `compare_images()` — `_build_question`
  now includes a worked example with explicit numeric deductions (text
  typo -0.40, missing region -0.20, missing icon -0.05 each, wrong
  proportion -0.15, wrong color theme -0.15, etc.) so the judge model
  anchors on numbers instead of default-helpful "looks similar"
  scoring. Empirically moves `qwen-vl-max` from 0.85-generous to
  ~0.65-0.85 honest on the same reproduction
- **Self-heal trigger threshold tuned to 0.85** in `CODE_GENERATION_SYSTEM`
  and `VISION_CODEGEN_SYSTEM` example flows. Empirically `qwen-vl-max`'s
  achievable ceiling on UI reproduction; 0.75 is too lenient (lets
  visibly-broken output pass), 0.92 causes over-correction divergence
  (model inlines giant SVG paths trying to fix icons, blows the token
  budget, emits truncated HTML)
- **`web_screenshot.full_page` default flipped to `False`**: full-page
  capture of Wikipedia / Notion produced 6000+ px tall images that
  multiplied downstream vision API latency 2-3x. Viewport-only is the
  correct semantics for "replicate this page" tasks; callers can still
  override per-call
- **Dirty-data SQL benchmark** (`data/sql_bench/dirty_cases.json`,
  `scripts/prepare_sql_dirty.py`): 5-case extension probing real-world
  data quality patterns the toy benchmark glosses over — case-inconsistent
  tier strings, NULL emails, mixed `completed` / `Completed` / `COMPLETE`
  status, negative-amount refunds, orphan foreign keys. Each case carries
  an `evidence` field naming the dirty pattern; reuses the existing
  `SqlBenchSession` and CLI (`python -m reforge.runtime.sql
  --cases data/sql_bench/dirty_cases.json`)
- **`MemorySubstrate.recall_repair_pattern(signature, limit)`**: cross-task
  repair-transfer recall — given a typed failure fingerprint, returns
  past RECOVERY records whose `problem_signature` structurally overlaps.
  Unlike `recall(query)` (free-form text + keyword overlap) or
  `find_by_error(error_type)` (single-field substring), this ranks by
  weighted overlap of fingerprint fields (`error_class`, `missing_key`,
  `missing_module`, `domain`, ...). Implemented on both
  `CompositeMemorySubstrate` and `SqliteMemorySubstrate` against the
  shared `_score_signature` so backends rank identically
- **Dashboard `/sessions/<id>` panels**: in addition to the raw events
  table, the session page now renders a *Retry timeline* (each
  `RECOVERY_ATTEMPTED` paired with its parent `EXECUTION_FAILED` via
  `parent_event_id`, plus the resulting outcome) and a *Policy decision
  trace* (every `POLICY_DECIDED` event with decision + reason). Pure
  client-side projections off the existing `/api/events` payload — no new
  route, no new event kind, no new `RuntimeState` field
- **HPO / AutoML benchmark** (`reforge.runtime.hpo`): `HpoSession` drives
  N trials per `HpoCase`; each trial = one runtime run that asks the LLM
  for a sklearn pipeline and prints `CV_SCORE=<float>`. Plateau
  detection short-circuits unproductive trial budgets. Ships with a
  4-case toy benchmark (iris / wine / breast_cancer / diabetes, mixed
  classification + regression) wired against the same `DummyClassifier`
  / `DummyRegressor` baselines.
- **Text-to-SQL benchmark** (`reforge.runtime.sql`): `SqlBenchSession`
  drives NL→SQL questions through the runtime; comparator implements
  BIRD/Spider-style execution accuracy (order-insensitive multiset, NULL
  / whitespace / numeric normalisation). Ships with a 15-case toy
  benchmark (4-table school registry, easy → hard) and an opt-in BIRD
  dev-set loader (`scripts/prepare_bird.py` + `bird_loader.py`).
- **EDA application** (`reforge.runtime.eda`): given a CSV, runs 8
  discrete stages (overview / dtypes / missing / numeric_stats /
  categorical_freq / correlation / outliers / quality_warnings) on top of
  the runtime and emits a structured Markdown report. Validated on 3
  UCI/OpenML datasets (iris / titanic / wine_quality) — first non-synthetic
  workload on top of Reforge
- Pluggable `SandboxBackend` Protocol with `SubprocessBackend` (default) and
  `DockerBackend` (opt-in via `REFORGE_SANDBOX_BACKEND=docker`)
- `ExecutionContext` + `trace_id` / `parent_event_id` on `ExecutionEvent`;
  dashboard can now pivot across sibling/child sessions of one user request
- `robustness` benchmark category with 4 adversarial cases:
  `robust_timeout_recovery`, `robust_double_column_miss`,
  `robust_malformed_constraint`, `robust_prompt_injection`
- `AgentCapability` runtime-level isolation (allow-list + memory scope),
  enforced at `SkillRegistry` boundary
- Case-level parallelism in `SqlBenchSession` / `HpoBenchSession` via
  `max_workers` (`--workers N` in their CLIs); each worker gets a fresh
  in-memory substrate when no explicit one is passed. Measured 3.0×
  speedup on the 15-case SQL toy benchmark at `--workers 4`
- `pyproject.toml` (PEP 621) + `.github/workflows/test.yml` matrix CI
- Demo recording playbook at `docs/demo/record.md`
- `LICENSE` (MIT)

### Changed
- `SandboxExecutor` is now a thin facade — the original subprocess code path
  lives in `SubprocessBackend`; behaviour is identical when no env var is set
- `dashboard/pages.py` (413 lines) split into `pages/` sub-package with one
  HTML template per file (CLAUDE.md <400 lines rule)
- `pip install -e ".[test]"` is now the documented install path
  (`requirements.txt` was the previous source of truth)

### Fixed
- README badge link points to `<your-org>/reforge` placeholder until repo is
  pushed (previous link returned 404)

---

## [0.1.0] — 2026-06-14 (pre-tag working state)

Reflects the state at the end of P-series consolidation (P0 → P25).

### Core runtime
- **P0–P3**: Skill abstraction (Protocol, registry, context, built-in skills:
  `python_sandbox`, `read`, `grep`, `glob`, `edit`, `web_search`)
- **P4**: Web dashboard (live SSE stream, outcome chart, session timeline,
  memory browser, skill catalogue)
- **P5**: Benchmark suite (10 cases across 4 categories; `BenchmarkRunner`
  with mock-able factory; cross-session learning curve mode)
- **P6**: Event-sourced runtime (`ExecutionEventLog` as append-only canonical
  record; `RuntimeState` frozen; new state must go through events)
- **P17–P18**: Multi-agent (`PlannerAgent` / `VerifierAgent` /
  `SynthesizerAgent` Protocols; `MessageBus`, `AgentRegistry`,
  multi-verifier consensus via `VerifierVoter`)
- **P21**: Worker pool + scheduler
- **P23–P24**: Parallel runtime + parallel research orchestrator
- **P25**: Session replay (`SessionReplay`, `SessionSummary`, `render_summary`)
- **P26–P27**: State projection + projection consistency checks

### Governance
- Governor pipeline: Intent → Capability → Classify → Policy
- 3-layer safety: `SemanticSafetyGuard` (regex) + AST guard +
  integrity guard (anti-spoof reflection)
- Typed `failure_mode` enum + `problem_signature` (root_cause / error_type /
  domain) — not natural-language failure descriptions

### Memory
- `MemorySubstrate` Protocol with `write` / `recall` / `recall_for_planning`
- 3-layer substrate: `ExecutionMemory` (JSONL) + `MemoryStore` (typed JSON)
  + `TrajectoryStore` (cross-session semantic arc)
- SQLite backend (`SqliteMemorySubstrate`, WAL mode) as drop-in alternative
- Pattern-based recall: keyword scoring + `problem_signature` structural
  match (not vector-only)

### MCP integration
- Hand-rolled sync stdio JSON-RPC 2.0 client (no SDK dependency)
- `discover_and_register()` registers every remote tool as a Skill
- Same governor / memory / events govern MCP-sourced and local skills

### Research mode
- Auto-routing of "why X / 为什么" questions to `ResearchSession`
- Multi-round hypothesis → verify → aggregate
- Cross-session pattern recall; Markdown export via `--export-research <id>`

---

## How to cut a release

1. Bump `version` in `pyproject.toml`
2. Move `Unreleased` items into a new dated section here
3. `git tag v0.x.y` after the commit lands on main
