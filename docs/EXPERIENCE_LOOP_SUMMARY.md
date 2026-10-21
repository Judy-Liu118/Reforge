# Learning from failure without gold — project summary (2026-10)

What was built and measured in the "learn from failure" track of Reforge.

**License and scope note.** This document contains methods, aggregate numbers and conclusions only. It does
not include AppWorld task content, instructions, answers or traces: the AppWorld bundle license asks that
they not be published in plain text, so they stay under the git-ignored `runs/`. Numbers marked n=1 are
demonstration traces, not statistical claims.

## Status (2026-10-21)

**Wired into the main runtime behind a feature flag, off by default.** Replaying the recorded trajectories
confirmed that the component version reproduces the script version.

- `build_graph()` attaches two hooks:
  - card guidance in the code-generation prompt;
  - probes plus gated minting after each execution.
- Both hooks are no-ops unless `REFORGE_EXPERIENCE_CARDS=1`. With the flag off, the codegen prompt is byte-identical and nothing is logged; an integration test checks this.
- The AppWorld scripts mint, gate, store and recall through `reforge/experience`.
- A zero-token replay (`scripts/replay_appworld_component.py`, 19 checks, all pass) shows the component version matches the script version:
  - same verifier evidence and same cards;
  - byte-identical system prompts on every recorded card arm;
  - probe output equal to the legacy reader on all 29 recorded request logs;
  - the hero result re-tabulates to **4/4 fixed with the card, 0/2 correct without it**.

### L2 critic tier (2026-10-21)

**The L2 cross-model LLM critic is wired into the main runtime as an optional second tier**
(`REFORGE_EXPERIENCE_CRITIC=1`, off by default).

- **When it runs**: only on signals the deterministic verifiers cannot confirm (empty output, NaN/inf, values coerced to missing), at most one call per round.
- **Isolation**: the critic model must differ from the generator. The frozen ladder prompt is used, with its sha stamped on each judgment.
- **Confirmation**: only verdict "wrong" with confidence ≥ 0.8 confirms; anything else counts as abstention.
- **Provenance**: confirmations carry `confirmation="judgment"`, distinct from re-run/AST confirmations (`"reproducible"`). They enter quarantine and need an offline canary to be promoted.
- **Logging**: every judgment (verdict, confidence, tokens, model) is logged as a `VERDICT` event.

Small test (generator qwen3.8-max, critic qwen3.8-27b; 4 triggers, about 24.7k tokens in total):

| measure | result |
|---|---|
| true coerce failures confirmed | 2/2 |
| false positives (first judgment) | 0/2 |
| mean critic tokens per trigger | about 1.8k |
| post-hoc re-judgment of a correct round | "wrong" at confidence 1.0, i.e. a false positive |

Status: the L2 LLM critic is an optional layer wired into the main runtime, off by default, triggered
only on demand, and promoted only through the canary.

In the small test (n=4) it confirmed coerce-dropped rows that the deterministic verifiers cannot confirm
(confidence 0.95–1.0). However, it produced one full-confidence false positive on a correct round, and
repeat consistency on the same input has not been measured. For both reasons the tier stays off by default
and is not enabled for now.

Next steps:
- Measure repeat consistency: re-judge the same inputs k times.
- Then require agreement across k samples, or a deterministic check of the critic's stated reason, before a
  confirmation counts.

## 1. What was built

| Piece | What it does | Where |
|---|---|---|
| Verifier ladder | **L0**: the execution itself (exit code / traceback). **L1**: static probes on data and output. **L1b**: metamorphic re-run (repair the input, re-run the code, see whether the output changes). **L2**: LLM critic. Cheap tiers run first; the critic only where they are blind | `scripts/run_verifier_ladder.py` (branch `exp/verifier-ladder`), `scripts/online_verifier.py` |
| Online gold-free loop | Task stream → verifier verdict → card mint / recall → retry. Gold lives only in `shadow.py`, behind an opaque `GoldRef`; an AST test keeps it out of every control module | `reforge/online/` |
| Experience cards | One atomic constraint per card: `{trigger, guardrail}`, rendered into the prompt as reasoning guidance (not code, not spliced into outputs) | `reforge/experience/` |
| Cheap probes for silent errors | Pagination: the agent finished, a list read's last page was full, and a read-only re-call shows the next page is non-empty. Login identity: a login call raised (L0), and a replay of identity forms shows which one works | `reforge/experience/probes.py` |
| Layered recall | Layer 1: app / environment convention cards by scope, no embedding. Layer 2: task-level cards via a plug-in retriever, admitted only through the verifier gate; **unsolved**, default returns nothing | `reforge/experience/recall.py` |
| Safety rails | Verifier-gated admit → quarantine → paired canary → promote; eviction with a `used_by` snapshot; duplicate lessons rejected; feature flag off by default | `reforge/experience/store.py`, `loop.py` |
| L2 critic tier | Optional cross-model LLM critic (`reforge/experience/critic.py`), triggered only on deterministic-unconfirmable signals; confirmations tagged as judgment; quarantine + canary like every card | `reforge/experience/critic.py`, `runtime_hooks.py` |
| Runtime integration | Two hooks in the main workflow (codegen guidance; after-execution probe → verify → mint into quarantine). Probes for the sandbox domain reuse existing runtime signals: swallowed exception (integrity guard → metamorphic un-swallowed re-run) and missing return (`None` result → AST check). Empty / `nan` outputs are probe-only and never minted | `reforge/experience/runtime_hooks.py`, `runtime_probes.py` |
| Event logs | Every agent step (code + output + tokens) goes to `steps.jsonl`. Every card event (admitted / rejected / recalled / canary / promoted / evicted) goes to the append-only loop log; card state is a projection of it | `reforge/online/loop_log.py`, `runs/` |

## 2. What was shown

### Hero trace — pagination silent error, same scenario (AppWorld, qwen3.8-27b, temperature 0)
1. A real episode answered wrongly after reading only the first page of a paginated search. The probe flagged it. The verifier re-read the next page in a fresh world and found 5 more items, confirming the failure. Gold took no part.
2. A distiller (deepseek) produced the card from the redacted evidence alone. The gate required the default page size and `page_index`, and the card passed on the first try:
   *"When a paginated call returns exactly page_limit (default 5) items → do not aggregate yet; keep requesting page_index+1 until a call returns fewer than page_limit items."*
3. The card was recalled on two other tasks of the same scenario (different entity, different min/max direction):

| arm | runs | correct | read past page 0 |
|---|---|---|---|
| no card | 2 | 0 | 0 |
| near-miss card ("10 per page, one call is enough") | 1 | 0 | 0 |
| card, no thinking | 2 | **2** | 2 |
| card, thinking | 2 | **2** | 2 |

Overall the card fixed **4/4 runs**. In every one of them, the agent's own comments repeat the card's checkable condition before it requests the next page.

**Earlier attempt.** The abstract version of the same card ("do not treat the first page as complete; read every page") left behaviour unchanged (1/1 identical to no card). The concrete anchor is what made it work.

### Cross-scenario login card — behavioural transfer, n=1
A login card was minted from two real login failures in other apps, verified by replaying three identity forms. On a different scenario (a state-changing task):
- With the card, the first login used the right parameter, with zero login errors.
- Without the card, the agent used the wrong parameter, got an error, read the docs and retried.

Both arms then ran out of the per-episode token cap before finishing the task, so **the outcome did not flip**. The remaining blockers were conventions that had no card yet.

## 3. Findings and limitations

1. **Pure semantic retrieval used the wrong key.** We embedded the task instruction and compared it with the card trigger (bge-m3, threshold = 95th percentile of an unrelated card's similarity on unrelated instructions).
   - The useful cards' triggers describe runtime events, which instructions never mention. Over 90 train instructions, all of which need login and 73% of which need pagination, the pagination card cleared the threshold on **4/90** and the login card on **1/90**.
   - A near-miss pagination card written in task language cleared it on **46/90**.
   - Semantic retrieval is biased toward cards that *sound like a task description*, and those are not necessarily correct. This is one argument for verifier-gated admission.
   - An earlier CSV-loop measurement agrees: request-text similarity reached top-1 same-root-cause precision of only 1/21.
2. **General cards have a step cost on tasks that don't need them (when-to-recall).** The env-wide pagination card, injected into a task where one page was enough, triggered four extra verification searches and delayed the actual work by 2 steps (n=1).
3. **Multi-convention tasks need several cards working together.** On the baseline agent, 9/10 tasks hit "pass the access token explicitly", 9/10 wasted steps on unprinted results, and 4/10 used the wrong login parameter. A zero-token replay showed that fixing all conventions together flips 6/6 candidate tasks; fixing one convention is not enough.
4. **Card compliance depends on the model and on the phrasing** (a Faithful-Self-Evolvers effect). A 27b agent ignored an abstract card and followed a concrete, checkable one. Earlier BIRD experiments with SQL guardrail lessons barely moved behaviour: +0.07, CI crosses zero.
5. **Other measured bounds from this track:**
   - Verifier ladder:
     - On known contamination types, the static probes catch most errors (recall 0.84).
     - On held-out, unseen types, recall is 4/28 for the probes vs 28/28 for the critic.
     - On real tables, the critic has a 6.5% false-positive rate and 61% recall.
   - Learning-loop ablation: the diagnostic content of the feedback drives the gain (+0.33 [+0.17, +0.50]). Judge noise costs +0.07 and crosses zero.
   - BIRD fact memory: NO-GO. Recalled near-facts mislead (−0.10); constraint rewriting hurt (−0.17).

## 4. Future work
- **Runtime-domain probes, first live measurement (2026-10-21, small n).** Setup: qwen3.8-max, 11 rounds through `RuntimeRunner` with the flag on, 31.7k tokens.
  - The full loop fired live once: a real swallowed `KeyError` (answer 0 instead of 6) → probe → un-swallowed re-run confirmed → card quarantined → canary passed → promoted → recalled on a later task.
  - Swallow probe precision is 1/2 (the miss was a handler that never ran) and recall 1/1. The verifier rejected the false positive.
  - **The dominant silent failure was outside both probes.** `to_numeric(errors="coerce")` silently dropped rows in 2 of the 3 silent failures. So probe coverage was 1/3.
  - On the recurrence task the promoted card was injected but did not change the answer, because the error there was the coerce kind. Prevention is therefore not shown.
  - Next: a coerce / NaN-drop probe, with a verifier that counts the rows dropped. Details are in the local `runs/runtime_live/report.md`.
- **Task-level precise retrieval.** Match on runtime events (step code / output) rather than instruction text, or on a distilled "applies-to" field. Audit precision offline on recorded traces before any agent run.
- **Card compliance.** Measure it per model, and compare phrasing (abstract vs anchored) at larger n.
- **On-demand recall.** Inject a convention card when its trigger fires (e.g. a list call returned exactly `page_limit` items), not up front. This addresses the step cost in finding 2.
- **More convention probes.** Access-token passing and finishing non-question tasks recurred in the baseline and have real failures available to mint from.
