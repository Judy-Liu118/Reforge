# Reforge — Product Requirements

Self-healing autonomous execution runtime for data analysis and knowledge tasks.

> **Note:** This document describes the long-term product vision. For current engineering
> constraints and active scope, see `DAILY_TASKS.md`, `CLAUDE.md`, and `docs/EVOLUTION.md`.
> Some sections below are aspirational and explicitly deferred.

---

# 1. Vision

Reforge is a production-style autonomous agent runtime focused on:

* long-horizon reasoning
* autonomous tool use
* self-healing execution
* memory-driven planning
* reflective error recovery

The system should NOT be a simple chatbot wrapper.

The goal is a modular agent runtime architecture suitable for:

* data analysis copilots
* autonomous coding assistants
* workflow orchestration systems

This project prioritizes:

* engineering architecture
* runtime robustness
* observability
* extensibility
* autonomous recovery loops

NOT prompt engineering demos.

---

# 2. Core Features

## 2.1 Autonomous Task Execution ✅ Implemented

The agent:

* understands user tasks
* generates Python code
* executes in isolated sandbox
* evaluates results
* retries or self-corrects
* generates final outputs

Example flow for "Analyze this CSV and explain Q2 revenue drop":

* task planning
* Python code generation
* sandbox execution
* error correction if execution fails
* result presentation

---

## 2.2 Self-Healing Runtime ✅ Implemented

Core differentiator.

Loop:

```text
generate code
→ run in sandbox
→ capture stderr
→ reflect on traceback
→ rewrite code
→ rerun
```

until success or retry limit reached.

Governor pipeline controls the loop with deterministic policy routing.

---

## 2.3 Memory System

### Short-Term (Implemented — file-based)

Current implementation:

* `ExecutionMemory` — JSONL per-run failure + repair records
* `MemorySubstrate` Protocol — unified memory interface

### Long-Term (Deferred)

* Historical successful solutions
* Semantic knowledge chunks
* Episodic recall

Current approach uses flat JSON files. Vector DB integration is explicitly deferred
until the substrate abstraction (MemorySubstrate Protocol) is proven stable.

---

## 2.4 Skill System (Deferred)

Planned abstraction layer above tools.

Example skills:
* DataAnalysisSkill
* PythonExecutionSkill

Each skill would contain tools, prompts, retry policy, memory access, evaluation rules.

**Current state:** Not implemented. PythonExecutionSkill is effectively embedded in
the sandbox execution loop. Extracting a formal skill layer is deferred to a future phase.

---

## 2.5 Tool System

Current:

* Python sandbox (subprocess-based)
* LLM inference (OpenAI-compatible API)

Planned (deferred):

* Web search
* File parsing
* SQL query
* Vector retrieval

---

## 2.6 Multi-Agent Coordination (Deferred)

Planned: Router Agent + Worker Agent topology.

**Current state:** Single-agent loop only. Multi-agent runtime is in DAILY_TASKS "LATER" backlog.

---

## 2.7 Reflective Evaluation ✅ Implemented

The runtime continuously evaluates:

* execution correctness (heuristic checks)
* output quality (non-empty, no unexpected errors)
* constraint satisfaction (must_fail_first, expects_uncaught_exception)

Reflection triggers:
* retry
* alternative code generation
* replanning (via retrieval-aware planning)

---

# 3. Architecture Principles

## 3.1 Separation of Concerns

* runtime separated from business logic
* memory separated from execution
* orchestration separated from evaluation
* classification separated from reflection

## 3.2 Event-Driven Hooks

Lifecycle hooks via TraceCollector:

* PLAN_STARTED / COMPLETED
* CODEGEN_STARTED / COMPLETED
* EXECUTION_STARTED / COMPLETED
* REFLECTION_STARTED / COMPLETED
* EVALUATION_STARTED / COMPLETED
* RETRY_TRIGGERED
* TASK_COMPLETED

---

# 4. Technical Stack

## Active

* Python 3.11+
* LangGraph (orchestration only)
* OpenAI-compatible APIs (DeepSeek, Qwen, OpenAI)
* Pluggable sandbox backend: subprocess (default) + docker (opt-in via `REFORGE_SANDBOX_BACKEND=docker`)
* JSONL / JSON file storage

## Deferred (not in current scope)

* Redis (short-term memory) — deferred; JSONL sufficient for current scale
* Qdrant (vector retrieval) — deferred; MemorySubstrate Protocol enables future swap
* FastAPI — deferred; CLI is the current interface
* Frontend (Gradio / Next.js) — out of scope

---

# 5. Non-Goals

The project is NOT intended to become:

* a benchmark harness
* a pure chat UI
* a toy LangChain demo
* a feature-complete agent platform

Focus on runtime intelligence and execution substrate quality.

---

# 6. Phase Roadmap

## Phase 1 — Self-Healing Runtime ✅ Complete

* LangGraph flow
* sandbox execution
* retry loop with Governor
* traceback reflection
* capability policy
* outcome resolution

## Phase 2 — Reflective Runtime ✅ In Progress (P8 Complete)

* Execution memory (JSONL-based)
* Trajectory tracking (cross-session)
* Memory Substrate Protocol
* Retrieval-aware planning

## Phase 3 — Multi-Step Execution (P9 Planned)

* Task decomposition
* SubtaskRunner
* Trajectory aggregation

## Phase 4 — Research Runtime (Future)

* Iterative investigation loops
* Hypothesis generation
* Evidence aggregation

## Phase 5 — Autonomous Substrate (Long-term)

* Self-directed execution planning
* Runtime self-optimization
* Multi-agent orchestration
