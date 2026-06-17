# Changelog

All notable changes to Reforge. Format roughly follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions track the `pyproject.toml` `[project] version`.

## [Unreleased]

### Added
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
