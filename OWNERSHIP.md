# Subsystem Ownership Boundaries

English | [简体中文](OWNERSHIP.zh-CN.md)

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

### Projection vs Mirror vs Dual-Write

The "no flat dual-write field" contract (`test_state_no_flat_fields.py`) guards
RuntimeState's *top level*. Fields *inside* the sub-states that also carry a
value present in the event log were, until this rule, unclassified. Once a
field's value also lives in the event log it falls into one of three
categories — only the first is a legitimate derived field.

**A derived field is a legitimate _projection_ iff:**

0. **(Premise) The source of truth exists.** Projection-ness is *not* an
   intrinsic property of the field — it is conferred by how the runtime is
   assembled. Under `RuntimeRunner` the source (the event log) exists; when it
   does not, conditions 1–3 do not apply. **Whether the source exists is
   decided by the assembly path, not by the field definition.**
1. **Single-directional source.** The event is written first, the projection
   updated after — which only holds given condition 0.
2. **Reconstructability.** The field can be rebuilt in full from the source;
   the reverse does not hold.
3. **Determinate arbitration.** On disagreement the answer is fixed — the event
   log wins. *This determinacy is currently provided by the emitter recomputing
   the value in full on every write, and verified by tests — NOT enforced by a
   runtime arbiter. `check_state_consistency` only reports mismatches; it never
   corrects them.*

| Category | Definition | Members | Risk / arbitration |
|---|---|---|---|
| **Projection** (legitimate) | Fully reconstructable from its source event; meets all three conditions | `control_state.retry_count` — registered in `tests/test_projected_fields.py::PROJECTED_FIELDS` | Discardable cache; on disagreement the event log wins |
| **Mirrored** (constrained) | Written from the *same local variable* as its event, in the same function; **cannot** be rebuilt from the log | `control_state.retry_decision_action` (`emitters.py:327`); `semantic_state.reflection_summary` (`emitters.py:273-282`); `semantic_state.evaluation_result` score/passed (`emitters.py:196-233`) | Consistency rests on "writing it twice"; **if the write site is wrong, reconstruction cannot detect it** — this is what separates mirrored from projection |
| **Dual-write** (forbidden) | Two co-equal authorities each write independently; no single-directional source | none (blocked by `test_state_no_flat_fields.py`'s deny-list) | Source of truth is not unique; unrecoverable |

**Why all three conditions, together — not any one alone.** Drop
reconstructability and the field degrades to *mirrored*: still consistent on the
happy path, but a wrong write is undetectable because there is nothing to
rebuild from. Drop single-directionality and it degrades to *dual-write*: two
authorities with nothing to arbitrate between them. Only all three together make
the field a discardable cache the event log can always overrule — which is
exactly what distinguishes a projection from a dual write.

**Assembly boundary (where the premise is guaranteed).** Under `RuntimeRunner`
assembly, `retry_count` is a projection of the event log; under the legacy path
`build_graph(event_log=None)` it degrades to a node-local counter and conditions
1–3 do not apply. The guarantee lives in `RuntimeRunner.__init__`
(`runner.py:46` — always-active log), **not** in the graph layer. `build_graph`
and the emitters intentionally support `event_log=None` (covered by
`test_always_active_event_log.py` and the legacy tests in
`test_emitter_policy_decision.py`), so this is **not** hardened with a runtime
assertion — an assertion would break a supported mode. The invariant is
documented here and cross-referenced from `runner.py:46` instead.

**Process requirement — when adding a derived sub-state field.** First decide
which of the three categories it is:

- **Projection**: it MUST be registered in `PROJECTED_FIELDS` and pass the
  reconstructability test before it can merge.
- **Mirrored**: its event-same-source relationship MUST be noted at the write
  site, with a stated reason why it cannot be reconstructed.
- **Dual-write**: forbidden.

**Scope honesty (a gate, not a disclaimer).** The contract test only locks down
*registered* fields; it cannot automatically detect a newly added, unregistered
projection-shaped field — that needs semantic judgement. That gate is carried by
the process requirement above and by code review, NOT by the test. Do not read a
green contract test as "there are no unregistered projections."

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
