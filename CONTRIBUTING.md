# Contributing to Reforge

Quick orientation for anyone reading the code or sending a patch.

## Setup

```bash
git clone <repo> && cd Reforge
python -m venv .venv && .venv\Scripts\activate    # Windows
pip install -e ".[dev]"                            # editable + ruff + pytest
pre-commit install                                  # one-time hook install
cp .env.example .env                                # fill in your LLM key
```

Run the suite:

```bash
python -m pytest reforge/tests --tb=short
```

You should see ~1828 passing tests (4 skipped) in under 2 minutes.

## Hard rules (see also CLAUDE.md)

These are enforced by review, not always by tests. Please skim CLAUDE.md
before your first PR.

| Rule | Why |
|---|---|
| `RuntimeState` is **frozen** — no new fields | New state goes into `ExecutionEvent` and is projected from the log. Adding a field re-introduces the god-state pattern P26 was designed to kill. |
| No emojis in source files | Cross-platform encoding issues (e.g. Windows GBK), and we strip them in PR review anyway. |
| Files stay under 400 lines | Soft target; split into a sub-package once approaching it. |
| No tight coupling between memory / tools / orchestration / evaluation | Each must be independently replaceable. |
| `MemorySubstrate` Protocol methods: `write` / `recall` / `recall_for_planning` | If your test mock uses different names (`store`, `get`), it will pass and then fail at runtime. |
| Hardcoded prompts: forbidden inside business logic | Pull from `prompts/` modules so prompt iteration is a string change, not a code change. |
| Each new lifecycle action: emit a hook/event | Tool execution, retry, reflection, memory R/W. |

## Naming conventions

| Kind | Convention | Example |
|---|---|---|
| Event factories | snake_case verbs | `execution_failed(...)` |
| Protocol class | Noun + `able` or noun | `CapabilityAware`, `MemorySubstrate` |
| Dataclass (state) | PascalCase noun | `AgentCapability`, `ExecutionContext` |
| Test file | `test_<feature>.py` or `test_p<NN>_<feature>.py` for phased work | `test_sandbox_backends.py` |
| Test class | `Test<Aspect>` (1 aspect per class) | `TestDockerBackendCommandShape` |

## What goes where

| You want to... | Put it in... |
|---|---|
| Add a new built-in skill | `reforge/runtime/skills/builtin/<name>.py` + register in `default_skill_registry()` |
| Add a new MCP server | `reforge/runtime/skills/mcp/discovery.py` configures auto-registration |
| Add a new event kind | `reforge/runtime/events/models.py` factory + `EventKind` literal + projection update |
| Add an agent role | `reforge/runtime/agents/<role>.py` with `AgentCapability` carried explicitly |
| Add a sandbox backend | `reforge/runtime/infrastructure/execution/backends/<name>_backend.py` conforming to `SandboxBackend` Protocol |
| Add a benchmark case | `reforge/benchmark/cases.py` — keep curated, demonstrate behaviour |

## Testing standards

- **Every new file ships with unit tests** in the same PR.
- **Real I/O over mocks** when feasible — see `reforge/tests/integration/test_sandbox_chain.py` for the pattern.
- **Failure paths** count as much as success paths — half of Reforge's value is in recovery, so tests should exercise it.
- **Mark slow / environment-specific tests** with markers (`@pytest.mark.docker`, etc.) so CI can opt in/out.

## Commit / PR style

- One concern per PR. Two if they're tightly coupled.
- Commit subject in imperative, &lt;= 72 chars: `add DockerBackend`, not `Added DockerBackend.`
- Body should answer *why*, not *what* — the diff shows *what*.
- Reference the relevant CLAUDE.md rule when reverting an over-engineering attempt.
