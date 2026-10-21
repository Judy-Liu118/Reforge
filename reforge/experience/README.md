# reforge.experience — verifier-gated constraint cards

Learn from an agent's own failures without gold: a cheap probe flags a
suspected silent error, a gold-free verifier confirms it, a distiller turns the
evidence into one atomic **constraint card** `{trigger, guardrail}`, the card
waits in quarantine until a paired canary shows it does no harm, and from then
on it is handed to every task its **scope** covers.

**Off by default.** Nothing changes for an agent until
`REFORGE_EXPERIENCE_CARDS=1`, or until `ExperienceConfig(enabled=True)`, is set.

**Status.** The package is wired into the main runtime workflow behind that flag (see below). Replaying
the recorded AppWorld trajectories (zero tokens) confirmed that the component version reproduces the
script version, including the 4/4 hero result. See `scripts/replay_appworld_component.py`.

## Main runtime integration (`reforge/runtime/orchestration/graph`)

`build_graph()` attaches two hooks from `reforge/experience/runtime_hooks.py`. Both return immediately
unless `REFORGE_EXPERIENCE_CARDS=1`; with the flag off, the code-generation prompt is byte-identical
and no experience log is written. An integration test checks both.

| hook | where | what it does |
|---|---|---|
| `codegen_guidance(state, session_id)` | code-generation node, before execution | Recalls active cards by scope (`env="reforge"`) and appends `render(cards)` to the system prompt. |
| `with_after_execution(node)` → `after_execution(state, session_id)` | wraps the evaluation node, after each execution | Runs the domain probes and confirms them with gold-free verifiers. Confirmed failures are minted into **quarantine**. The hook never changes state and never raises into the graph. |

Probes written for Reforge's own execution domain, built on signals the runtime already computes:

| failure kind | probe (existing signal) | verifier (gold-free, no LLM) |
|---|---|---|
| `swallowed_exception` | `RetryIntegrityGuard` flags a no-op handler, and exit code is 0 | Metamorphic re-run: the flagged handlers' bodies become `raise` and the script runs again in the sandbox. A traceback confirms a real swallowed error. |
| `missing_return` | last stdout line is `None` (the evaluator's `SUSPICIOUS_NUMERIC`) | AST check: a function that is printed or used as the result has no `return <value>` |
| empty stdout, `nan` / `inf`, `errors="coerce"` | heuristic evaluator signals plus an AST check for coerce | none deterministic; with `REFORGE_EXPERIENCE_CRITIC=1`, the L2 critic judges them (see below) |

### Optional second tier: L2 cross-model critic (`critic.py`, `REFORGE_EXPERIENCE_CRITIC=1`, off by default)
- **Trigger**: only the probe-only signals above, at most one call per round. Requires `REFORGE_EXPERIENCE_CARDS=1`.
- **Cross-model**: `REFORGE_EXPERIENCE_CRITIC_MODEL` must differ from `LLM_MODEL`, otherwise the tier disables itself. Optional settings: `..._BASE_URL`, `..._API_KEY`, `..._THINKING`, and the spend cap `..._MAX_TOKENS` (default 50000).
- **Abstention**: only verdict "wrong" with confidence ≥ 0.8 confirms. A low-confidence verdict, an unparseable reply or an error counts as abstain, and the run continues.
- **Two confirmation sources**: deterministic evidence has `confirmation="reproducible"`; critic evidence has `confirmation="judgment"` and `verifier="llm_critic"`. Both enter quarantine and need a canary to be promoted.
- **Logging**: every judgment is a `VERDICT` event with verdict, confidence, `parse_ok`, model, tokens and prompt sha.
- **Measured caveat (small test, n=4)**:
  - It confirmed coerce-dropped rows that the deterministic verifiers cannot confirm (confidence 0.95–1.0).
  - It produced one full-confidence false positive on a correct round.
  - Repeat consistency on the same input has not been measured yet.

  The tier therefore stays off by default and is not enabled for now.

Distillation in the runtime domain is a fixed template per failure kind, with no LLM call. Evidence is
redacted to shapes: handler tier, exception class name, exit codes. The same lesson is never admitted
twice; a duplicate is rejected with `duplicate_of:<id>`, including a duplicate of an evicted card.
Runtime cards stay quarantined until an offline canary promotes them.

## The loop

```
failure ──► probe (cheap, gold-free) ──► verifier (gold-free re-check) ──► VerifiedFailure
                                                                              │
                                                       distill (host LLM / template)
                                                                              │
                                                       gate (deterministic lint) ──► CARD_REJECTED
                                                                              │
                                                              admit ► QUARANTINED
                                                                              │
                                                  paired canary report (passed?) ──► CANARY_RESULT
                                                                              │
                                                              promote ► ACTIVE
                                                                              │
next task ──► recall by scope ──► render into prompt ──► agent applies it ──► CARD_RECALLED (used_by)
                                                                              │
                                                   regression seen ──► evict ► EVICTED (used_by snapshot)
```

Every write is an event on `reforge.online.loop_log.LoopEventLog`, an
append-only fsync'd JSONL file. Card state is replayed from that log, so
provenance ("which runs got this card") and rollback are plain reads.

## Attaching it to an agent

```python
from reforge.experience import ExperienceConfig, ExperienceLoop, TaskContext, CanaryReport
from reforge.experience import probes

loop = ExperienceLoop(ExperienceConfig(
    log_path="runs/experience/loop.jsonl", env="appworld",
    known_apps=("spotify", "phone", "venmo", "simple_note"),
    banned_terms=frozenset(ALL_APP_AND_API_NAMES),            # keep env-wide cards abstract
    required_anchors={"pagination": frozenset({"5", "page_index", "page_limit"}),
                      "login_identity": frozenset({"username"})},
))

# 1) before execution
task = TaskContext(task_id, "appworld", instruction, run_id)
cards = loop.recall(task)                       # [] while the flag is off
system_prompt = BASE_PROMPT + "\n\n" + loop.render(cards)

# 2) during execution: only if a task-level retriever is plugged in (see limitations)
new = loop.recall_on_step(task, step_record, cards)

# 3) after the episode: probes + verifier, then mint (no gold anywhere)
reads = probes.paginated_reads(request_log, endpoints)
failure = probes.confirm_incomplete_reads(reads, refetch, agent_finished=done, source_task=task_id)
if failure:
    result = loop.mint(failure, distill)        # -> QUARANTINED card, or rejection reasons

# 4) offline: canary, then promote (or evict later)
loop.promote(card_id, CanaryReport(card_id, n=8, control_success=3, treated_success=5))
loop.evict(card_id, "regressed on canary 2026-11-02")
```

The host supplies these:
- `distill(failure) -> {"trigger", "guardrail"}`: an LLM call or a template. It sees only the redacted verifier evidence.
- `refetch(read, page_index, page_limit) -> int`: a read-only call into the same world.
- The paired canary run. Scoring it may use gold, because it runs offline and outside the control path.

## Configuration (`ExperienceConfig`)

| field | default | meaning |
|---|---|---|
| `enabled` | env `REFORGE_EXPERIENCE_CARDS == "1"` | feature flag. When off, recall returns `[]` and logs nothing |
| `log_path` | — | the event log |
| `env`, `known_apps` | — | scope matching. A task's apps are the app names mentioned in its instruction |
| `max_injected` | 4 | cap on cards per task |
| `banned_terms` | ∅ | the gate rejects cards that mention these |
| `banned_by_kind` | {} | extra banned terms per failure kind (e.g. app names for pagination cards only) |
| `required_anchors` | {} | per failure kind, terms that must appear in the card |
| `task_retriever` | `unsolved_task_retriever` (returns `[]`) | layer-2 plug-in |
| `distill_attempts` | 2 | the gate gets this many distiller tries |
| env `REFORGE_EXPERIENCE_LOG` | `~/.reforge/experience/loop.jsonl` | the runtime hooks' event log |

## Safety rails
- **Verifier-gated admit**: a card needs a `VerifiedFailure` with non-empty evidence. Gold is never an input.
- **Quarantine → canary → promote**: a quarantined card is never recalled. `promote` requires a paired canary report with no regressions and treated ≥ control, and the report itself is logged.
- **Eviction**: an evicted card's state is terminal. The eviction event carries a snapshot of `used_by`.
- **Provenance**: every recall logs task, run, stage, layer and card ids. `store.used_by(card_id)` reads them back.
- **Gold isolation**: `test_experience_package_never_touches_gold` checks this package statically.

## Limitations (measured; details in docs/EXPERIENCE_LOOP_SUMMARY.md)
1. **Task-level retrieval is not solved.** On AppWorld, embedding similarity between the instruction and a card's trigger was the wrong key: useful convention cards cleared a null-calibrated threshold on 4/90 and 1/90 train instructions, while a near-miss card phrased in task language cleared it on 46/90. Layer 2 is therefore an interface whose default returns nothing.
2. **Scope recall is not free.** An env-wide pagination card injected into a task that didn't need it caused four extra verification searches, which is an open when-to-recall problem (n=1).
3. **Multi-convention tasks need several cards at once.** In the one cross-scenario run, a login card was followed (first login used `username=`, no error), but the task still failed on conventions with no card yet (passing the access token, finishing a non-question task). Measured n=1.
4. **Card compliance depends on the model.** An abstract card ("read every page") did not change a 27b agent's behaviour; a card with the concrete anchor (default page size 5, "exactly page_limit items → page_index+1") did.
5. **Probes cover four failure kinds**: pagination and login identity (AppWorld), swallowed exception and missing return (runtime). Anything else needs a new probe plus verifier.
6. **The runtime-domain probes have not been measured on live traffic.** They have integration tests on a real sandbox, but no precision/recall numbers on production runs yet. Runtime cards also need an offline canary before they can be promoted.

## Experiment harness and replay
The AppWorld experiment scripts now mint, gate, store and recall through this package, via
`scripts/appworld_experience.py`. Reading AppWorld's request log, the in-world re-calls and the LLM
distillers stay in the scripts. `scripts/appworld_gold.py` is still the only module that calls AppWorld's
evaluator.

`scripts/replay_appworld_component.py` replays the recorded runs without any LLM call. It needs only the
local AppWorld server, for the verifiers' read-only re-calls. It checks 19 things:
- recorded failure → component probe + verifier → same evidence → same card → quarantined → not recalled
- promoted on the recorded paired canary
- recall gives a system prompt byte-identical to the one the script version sent, on all four hero card arms and the cross-scenario scope arm
- the component probe groups equal the legacy reader's on all 29 recorded request logs
- recorded outcomes re-tabulate to **card arms fixed 4/4, no card 0/2**
- the login card is reproduced the same way
- an unverified distractor cannot enter the store

Traces and reports stay under the git-ignored `runs/`, because the AppWorld bundle license asks that task
content not be published.
