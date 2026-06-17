# Subsystem Ownership Boundaries

This document defines what each subsystem **produces**, **consumes**, and
**must never do**.  Violating these rules creates hidden coupling that makes
replay, testing, and future refactoring exponentially harder.

Read this before adding code to any subsystem.

---

## Ownership Table

| Subsystem | Produces | Consumes | MUST NOT |
|---|---|---|---|
| **governor** | `PolicyDecision` | `TaskIntent`, `CapabilityPolicy` | Execute code; generate plans; write `RuntimeState` directly |
| **evaluation** | `EvaluationResult` | Execution output (stdout/stderr/exit_code) | Make retry decisions; modify any state |
| **reflection** | `PlannerContext` | `EvaluationResult`, `TrajectoryStore` | Execute code; make policy decisions |
| **research** | `ResearchResult`, `HypothesisRecord` | `RuntimeRunner`, `ResearchStore`, `MessageBus` | Depend on governor; expand heuristics |
| **tracing** | `TraceEvent`, `SpanContext` | Nothing (passive observer) | Alter execution behavior; make decisions |
| **events** | `ExecutionEvent` | Nothing (emitted by all subsystems) | Depend on any runtime subsystem (stdlib only) |
| **tasks/workers** | `TaskResult`, `WorkerState` | `Task`, `WorkerPool` routing | Contain business logic; access governor or evaluation |
| **agents/bus** | `RuntimeMessage`, `VoterResult` | `VerifierAgent`, `MessageBus`, `ActorContext` | Modify `RuntimeState` directly; own execution lifecycle |
| **skills** | `SkillResult` | `SkillContext`, `SkillRegistry` | Modify `RuntimeState` directly; make retry/policy decisions; bypass governor |
| **RuntimeState** | Snapshot of current execution | All graph nodes (read-only preferred) | **Grow further** — it is FROZEN |

---

## Detailed Rules by Subsystem

### governor

- **Single authority** for retry/stop/accept decisions.
- Consumes signals from evaluation, classification, and capability policy.
- Returns `RuntimeDecision` — does NOT apply the decision itself.
- Must not call evaluation directly; evaluation is injected or pre-run.

### evaluation

- **Signal provider only**, not decision maker.
- Produces `EvaluationResult(passed, score, checks, failure_type)`.
- The runtime (governor) decides what to do with the signal.
- Must not trigger retries, modify state, or access memory.

### reflection

- **Context enricher** — makes the next plan better by injecting history.
- Reads `TrajectoryStore` and `EvaluationResult` to build `PlannerContext`.
- Does not execute code, does not classify intent.

### research

- **Investigation runtime** — orchestrates multi-round hypothesis testing.
- Communicates with verifier agents via `MessageBus`.
- Must not accumulate new heuristics — depth over breadth.
- `ResearchSession` is the boundary: callers go through session, not internals.

### tracing / observability

- **Passive observer** — only reads and records.
- `TraceCollector` and `ExecutionEventLog` accept events; they never push.
- Must not affect the code path being observed.
- `SpanContext` propagation is the only "active" aspect — it must remain opt-in.

### events (ExecutionEvent)

- **Foundation layer** — zero runtime dependencies (stdlib only).
- Every subsystem MAY emit events; no subsystem MUST consume them (yet).
- Direction: as RuntimeState freezes, events become the primary record.
- `FailureCategory` + `semantic_meaning` are the vocabulary for runtime learning.
- **Exception**: `emitters.py` lives in the events/ package but imports `RuntimeState`
  to type-annotate `NodeFn`.  It is a graph bridge layer, not part of the event model.
  The stdlib-only constraint applies to `models.py`, `log.py`, `replay.py`,
  `projection.py`, `persistent_log.py`, `observer.py`, and `categorizer.py`.

### tasks / workers

- **Pure execution substrate** — no business logic.
- `TaskScheduler` and `WorkerOrchestrator` know nothing about agents or research.
- `WorkerPool` routes by type string only; no semantic knowledge.
- Results are `TaskResult` dataclasses; post-processing is the caller's job.

### agents / bus

- **Coordination layer** — routes messages, aggregates votes, wraps agents.
- `MessageBus` knows routing rules, not semantic content.
- `VerifierVoter` aggregates results using strict majority — no LLM calls.
- `AgentRegistry` maps `(role, variant)` to implementations — no policy logic.

### skills

- **Capability wrapper layer** — each Skill encapsulates one typed capability (sandbox, file read, web search, MCP call).
- Two invocation paradigms supported through the same Protocol:
  * code-as-action: LLM-generated Python imports skills as a library
  * tool-as-action: LLM emits OpenAI function-call; runtime dispatches to `SkillRegistry.get(name).invoke(params, ctx)`
- `SkillContext` is the ONLY object passed in: no `RuntimeState`, no governor handle, no event log writer. Side effects flow OUT via `SkillResult`; runtime layer wraps the call with events.
- `SkillRegistry` is pure lookup + OpenAI schema export. No policy logic.
- A skill MAY raise, MAY do I/O, MAY take time (respecting `context.timeout_s`). It MUST NOT decide whether to retry, modify any state, or call governor.

---

## RuntimeState — FROZEN

`RuntimeState` must not grow further.

**Current fields are final.** Any new execution state MUST go into an
`ExecutionEvent` emitted to `ExecutionEventLog`.

### Why

`RuntimeState` started as a small workflow blackboard.  It now has 16 top-level
fields and 4 nested sub-states.  Continued growth causes:

- Ownership blur: unclear which node "owns" which field
- Hidden coupling: node A reads state written by node B via shared object
- Replay difficulty: reconstructing what happened requires reading the whole object
- Testing friction: tests must set up irrelevant fields to reach the state under test

### Direction: Event-Sourced Runtime

`RuntimeState` should become an **event projection** — a derived view
reconstructed from an ordered stream of `ExecutionEvent` facts.

Intermediate steps (do not rush):
1. All new state → `ExecutionEvent` (current step)
2. Graph nodes emit events alongside state mutations
3. Gradually replace state reads with event queries
4. Eventually `RuntimeState` is a thin projection helper, not primary truth

---

## Anti-Patterns to Avoid

| Anti-Pattern | Why It Hurts |
|---|---|
| Subsystem A calls Subsystem B's internal functions | Creates hidden coupling; breaks independent replaceability |
| Adding fields to `RuntimeState` | God object grows; ownership becomes unclear |
| Evaluation making retry decisions | Mixes signal production with policy authority |
| Research accumulating keyword heuristics | Rule soup; complexity grows, intelligence doesn't |
| Tracing code modifying execution behavior | Observer effect; defeats observability purpose |
| Business logic in `WorkerPool` / `TaskScheduler` | Substrate should remain policy-agnostic |
