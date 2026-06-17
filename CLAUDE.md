# Development Rules

## General Principles

* prioritize modularity
* prioritize readability
* avoid overengineering
* avoid giant agent classes
* keep files under 400 lines if possible
* prefer composition over inheritance

---

# Runtime Philosophy

The runtime is event-driven and reflective.

Do NOT tightly couple:

* memory
* tools
* orchestration
* evaluation

Each subsystem must remain independently replaceable.

---

# Architecture Constraints

## NEVER:

* hardcode prompts inside business logic
* directly call tools from UI
* store global mutable states
* mix execution and evaluation

## ALWAYS:

* use interfaces/protocols
* add structured logging
* return typed outputs
* separate graph nodes clearly

---

# Hook System

Every important lifecycle action should emit hooks/events.

Examples:

* tool execution
* retry
* reflection
* memory retrieval
* memory write

Hooks must be extensible.

---

# Error Handling

The runtime must fail gracefully.

All execution systems should:

* capture stderr
* preserve traceback
* expose retry metadata

Avoid silent failures.

---

# Memory Rules

Short-term memory:

* lightweight
* fast
* contextual

Long-term memory:

* semantic
* retrievable
* persistent

Avoid storing raw conversation blindly.

---

# Skills

Skills are higher-level capabilities built on tools.

A skill may:

* orchestrate multiple tools
* contain retry logic
* contain evaluation logic

Skills should remain domain-independent.

---

# Code Style

* Use pydantic models for structured states
* Use async wherever beneficial
* Use dependency injection
* Prefer explicit typing
* Prefer dataclasses/pydantic over raw dicts

---

# Testing

Each module should include:

* unit tests
* failure cases
* retry scenarios

Self-healing loops must be tested carefully.

---

# RuntimeState — FROZEN

`RuntimeState` must NOT grow further.

New execution state MUST go into `ExecutionEvent` emitted to `ExecutionEventLog`.

Do NOT add fields to `RuntimeState`. This is a hard constraint, not a guideline.

If you think you need a new field in `RuntimeState`, you need an event instead.

---

# Event-Sourced Runtime Direction

The runtime is moving toward event-sourced architecture:

* RuntimeState = event projection (not primary source of truth)
* New state → `execution_started()` / `execution_failed()` / etc.
* `ExecutionEventLog` is the append-only canonical record

Prefer:

```python
log.append(execution_failed(session_id, task, category="syntax", recoverable=True, error=msg))
```

Over:

```python
state.some_new_field = ...
```

---

# Subsystem Ownership

Read `OWNERSHIP.md` before touching any subsystem.

Key rules:
* evaluation = signal provider only, NOT decision maker
* governor = single authority for retry/stop/accept
* tracing = passive observer, never alters behavior
* research = must NOT accumulate new heuristics
* workers/tasks = no business logic, pure execution substrate

---

# Goal

The project should resemble:

* a production AI runtime

NOT:

* a toy chatbot
* a prompt demo
* a monolithic script
