# reforge.experience — verifier-gated constraint cards

Learn from an agent's own failures without gold: a cheap probe flags a
suspected silent error, a gold-free verifier confirms it, a distiller turns the
evidence into one atomic **constraint card** `{trigger, guardrail}`, the card
waits in quarantine until a paired canary shows it does no harm, and from then
on it is handed to every task its **scope** covers.

**Off by default.** Nothing changes for an agent until
`REFORGE_EXPERIENCE_CARDS=1`, or until `ExperienceConfig(enabled=True)`, is set.

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
| `required_anchors` | {} | per failure kind, terms that must appear in the card |
| `task_retriever` | `unsolved_task_retriever` (returns `[]`) | layer-2 plug-in |
| `distill_attempts` | 2 | the gate gets this many distiller tries |

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
5. **Probes cover two failure kinds**, pagination and login identity. Anything else needs a new probe plus verifier.

## Experiment harness (not part of the component)
The AppWorld measurements behind the numbers were run with `scripts/appworld_*.py`, `scripts/run_appworld_hero.py`
and `scripts/run_appworld_multicard.py`. These predate this package and keep their own card files so that runs
can be reproduced. `scripts/appworld_gold.py` is the only module that calls AppWorld's evaluator. Traces and
reports live under the git-ignored `runs/`, because the AppWorld bundle license asks that task content not
be published.
