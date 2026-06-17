# Reforge Memory Subsystem

Reforge keeps four distinct kinds of persisted experience. They look similar
on disk (JSON / JSONL append logs) but serve different roles in the runtime.
Mixing them up is the most common source of confusion when extending the
memory subsystem — keep this map handy.

## The four record types

| Type | Storage | Owner | Purpose |
|------|---------|-------|---------|
| `MemoryRecord` | `data/memory/{recovery,failures,success_patterns}.json` | `MemoryStore` + `CompositeMemorySubstrate` | Cross-session **failure / recovery / success patterns** consumed by the planner and reflection nodes via the `MemorySubstrate` Protocol. |
| `ExecutionRecord` | `data/execution_memory.jsonl` | `ExecutionMemory` | Per-attempt **execution learning** (failure_mode, problem_signature) used by `ClassifyStage` to recognise repeat failures and inform retry policy. |
| `TrajectoryRecord` | `data/trajectories.jsonl` + `data/multistep_trajectories.jsonl` | `TrajectoryStore` | Full per-session **execution arc replay** (every attempt, every eval score). Used by `PlannerMemoryContext` for similar-session recall and by the CLI `--replay` flag. |
| `ResearchResult` | `data/research.jsonl` | `ResearchStore` + `ResearchMemory` | **Research session artefacts** (hypotheses → verification → conclusion). Used by `ResearchPlanner` to avoid re-testing already-resolved patterns. |

## Read paths

- **Planning** (`graph/nodes/planner.py`)
  → `PlannerMemoryContext` → `MemorySubstrate.recall_for_planning()` (returns `MemoryRecord` only).
  When a `TrajectoryStore` is wired in, the same context also queries `find_similar()` for past arcs.

- **Reflection** (`graph/nodes/reflection.py`)
  → `MemorySubstrate.recall()` (returns `MemoryRecord` only) — finds prior recovery experiences matching the current error.

- **Retry classification** (`governor/classify_stage.py`)
  → `ExecutionMemory.recall_similar()` (returns `ExecutionRecord`) — feeds failure-mode signals into the governor pipeline.

- **Research planning** (`runtime/research/planner.py`)
  → `ResearchMemory.recall_patterns()` (queries `ResearchStore` over `ResearchResult`) — injects cross-session research patterns into hypothesis generation.

## Write paths

- `MemoryStore.save(record)` — called from outside graph nodes (currently from CLI / tests). Nodes never directly write `MemoryRecord`.
- `TrajectoryStore.save(record)` — called from `RuntimeRunner.stream()` when the `final_response` node fires.
- `ExecutionMemory.write(record)` — currently invoked at trajectory-save time as well.
- `ResearchStore.save(result)` — called from `ResearchSession.run()` and `cli/research.py`.

## Substrate Protocol

`MemorySubstrate` (defined in `reforge/memory/substrate.py`) is the single
interface graph nodes use to read memory. The default implementation
(`CompositeMemorySubstrate`) wraps `MemoryStore` + `MemoryRetriever`, but
tests and future deployments can swap in alternative backends (e.g. SQLite,
vector DB) without touching node code.

```python
from reforge.runtime.engine.runner import RuntimeRunner
from my_app.vector_substrate import VectorMemorySubstrate

runner = RuntimeRunner(memory_substrate=VectorMemorySubstrate(url="..."))
```

Both the planner and reflection nodes receive the substrate via constructor
injection in `build_graph(memory_substrate=...)`.

## Path migration

Before P-R, `MemoryRecord` JSON files lived at the project root (`memory/`).
P-R consolidated all persistence under `data/`. `MemoryStore.__init__` runs
a one-shot fallback that moves legacy files to `data/memory/` and removes
the old directory; safe to invoke on every startup.
