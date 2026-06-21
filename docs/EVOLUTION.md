# Reforge — Architecture & Evolution Archive

> This file consolidates `ARCHITECTURE.md`, `TASKS.md`, `RUNTIME_ARCHITECTURE_REVIEW.md`,
> `docs/ARCHITECTURE_VISION.md`, `docs/RESEARCH_RUNTIME_ROADMAP.md`,
> and `docs/RUNTIME_REVIEW.md` into a single historical archive. The live,
> normative docs are now: `README.md`, `CLAUDE.md`, `OWNERSHIP.md`.

---

## Part 1 — Architecture Vision

Reforge is an **execution runtime substrate**, not a chatbot. Core abstraction:

```
LLM + Execution + Reflection + Policy + Runtime Memory
```

Treats execution as a first-class reasoning primitive. Long-term direction:

```
Execution Runtime → Reflective Runtime → Research Runtime → Autonomous Investigation Substrate
```

### Core Runtime Layers

| Layer | Concept | Owns |
|---|---|---|
| Execution | `exec_state` | sandbox, stdout/stderr, timeout, traces |
| Semantic | `semantic_state` | intent, reflection, evaluation, recovery reasoning |
| Control | `control_state` | retry policy, capability gating, governor decisions |
| Outcome | `outcome_state` | task outcome, recovery status, completion semantics |

### LangGraph's Role (constrained)

LangGraph manages: orchestration, node transitions, conditional routing.
LangGraph does NOT own: memory, reflection, retry strategy, evaluation,
tool abstraction, observability, runtime policies. Each remains independently
implemented.

### Non-Goals

Not a chatbot UX, not a generic assistant, not a function-calling demo,
not a benchmark harness. Focus on runtime intelligence and execution
substrate quality.

---

## Part 2 — Phase History (P1 → P36+)

### Phase 1 — Self-Healing Runtime Foundation ✅

* P1 Retry Loop — execution-aware recovery, reflection-based regeneration
* P2 Intent-Aware Failure Semantics — task fidelity > raw execution success
* P3 Outcome Resolver — SUCCESS / RECOVERED / EXPECTED_FAILURE / DENIED / FAILED
* P4 Capability Governance — 3-layer security (request gate, AST guard, integrity guard)
* P5 Runtime State Separation — nested sub-states (exec / control / semantic / outcome) with dual-write compat
* P6 Runtime Consolidation — Governor owns decisions, workflow only routes; +5 sandbox integration tests
* P7 Governor Pipeline Composability — RuntimeStage Protocol, 4 independent stages
* P7 Execution Memory — JSONL per-run failure_mode + repair_strategy, recall_similar() injection

Outcome: 65 tests passing, governor unified decision flow.

### Phase 2 — Reflective Runtime ✅

* P8 MemorySubstrate Protocol + TrajectoryStore + retrieval-aware planning (88 tests)
* P9 Multi-step Task Decomposition — TaskDecomposer + SubtaskRunner + MultiStepTrajectory (111 tests)
* P10 Async Execution + Parallel Subtasks — AsyncSubtaskRunner + topological scheduling (129 tests)
* P11 Runtime Evaluation Improvements — retry_drift, output_contains_data, EvaluationFeedback (147 tests)
* P12 Per-Attempt Evaluation Tracking + History Query (164 tests)

### Phase 3 — Research Runtime ✅

* P13 Research Runtime — ResearchSession / ResearchPlanner / EvidenceAggregator (209 tests)
* P14 Research CLI Integration + Persistent ResearchStore (237 tests)
* P15 Adaptive Research — HypothesisRanker + ResearchMemory + adaptive exit (267 tests)
* P16 Research Quality + Export — ResearchReporter + `--export-research` + research_output_quality (303 tests)

### P-R — Runtime Consolidation ✅

Cleared four pieces of accumulated debt:

1. `graph/workflow.py` 455 → 67 lines; 8 nodes split to `graph/nodes/*.py`, each ≤ 100 lines
2. Prompts externalised to `models/prompts/directives.py`; `runtime/requirements.py` owns constraint extraction
3. Removed 8 backward-compat flat RuntimeState fields (retry_count / task_intent / task_outcome / outcome_reason / final_answer / execution_status / task_status / decision_reason); ExecutionStatus / TaskStatus enums removed
4. `MemorySubstrate` Protocol activated end-to-end via `build_graph(memory_substrate=...)` and `RuntimeRunner.__init__`
5. Memory persistence unified under `data/`; auto-migration from legacy paths
6. 3 contract test files freeze invariants

319 tests passing (303 + 16 contract).

### Phase 4 — Multi-Agent Runtime ✅ (P17–P18+)

* P17 Multi-Agent Stub + Research Orchestration — Protocol-based PlannerAgent / VerifierAgent / SynthesizerAgent + ResearchOrchestrator (parallel verification + worker isolation) (346 tests)
* P18 Multi-Agent graduation — AgentRegistry + MessageBus + multi-verifier consensus + per-agent trace spans

### Phase 5 — Event-Sourced Runtime (P19 → P36)

* P19–P25 Tracing + Task Graph + Workers + Events + Persistent Event Log + Session Replay
* P26–P33 State Projection + Consistency + Migration of retry_count / eval / policy_decision to event log; always-active EventLog; node mutation removal; full consistency integration
* P34 PersistentEventLog (JSONL persistence, drop-in subclass, thread-safe under load)
* P35 Events CLI (`--events-list`, `--events-show`, `--events-summary`)
* P36 Event Subscriber/Hook System — `subscribe()` / `unsubscribe()` / `SubscriptionHandle`; subscribers called outside lock, errors isolated

**1317 tests passing** at time of this archive (Jun 2026).

---

## Part 3 — Historical Review Notes

Excerpted from `RUNTIME_REVIEW.md`. Several items have since been addressed
or scoped differently; kept here for institutional memory.

### Recurring Theme

> "Runtime abstraction's growth has outpaced runtime architecture convergence."

Not a feature-completeness problem — a substrate-stabilisation problem.

### Past Recommendations (status as of P36)

| Item | Status |
|---|---|
| Complete nested RuntimeState migration | ✅ done in P-R / P26 → P33 |
| Decomposition runtime-ification (SubtaskRuntimeState) | ✅ done in P10 |
| CapabilityPolicy honest naming (still regex/keyword underneath) | ✅ done — main class is `SemanticSafetyGuard`; module docstring states "Keyword/regex-based heuristics only. No LLM, no real sandbox isolation." |
| Failure fingerprint structured signature | ✅ done — `memory/fingerprint.py` |
| Runtime package layering (domain / orchestration / infrastructure) | ✅ done |
| CLI main.py decomposition | ✅ done in `cli/commands/` |
| Runtime-level integration tests | ✅ done — `tests/integration/test_runtime_chains.py` etc. |
| Stop adding large subsystems; converge runtime architecture | ⚠️ ongoing discipline |
| Project positioning: "Self-healing Execution Runtime" not "general agent" | ✅ adopted (see README) |

### Open Architectural Direction

Remaining migration (tracked via `ExecutionEvent` additions; `RuntimeState` is frozen):

> RuntimeState → event projection.
> Steps: all new state goes to ExecutionEvent → graph nodes emit alongside
> mutations → replace state reads with event queries → RuntimeState becomes
> thin projection helper.

See `OWNERSHIP.md` for current ownership rules and CLAUDE.md "RuntimeState — FROZEN".
