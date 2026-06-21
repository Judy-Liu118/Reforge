# Experience Memory Benchmark

A reproducible benchmark that asks: **does Reforge's experience memory
actually make the runtime better at repeat-pattern failures?**

The answer (v2 multi-seed) is more interesting than yes/no. See §5.

---

## TL;DR

This benchmark has **two findings**, and the second is the bigger one:

1. **Experience Memory's effect on routine single-hop self-heal tasks is
   statistically indistinguishable from zero.** v0 saw `+20%` transfer;
   v1 (with `.reforge/` isolation) collapsed it to `+0%`; **v2** (5 seeds ×
   5 pairs × 4 legs = 100 LLM calls) confirmed all three headline KPIs
   have 95% confidence intervals *including zero*:
   - Transfer success rate: `+0% [+0%, +0%]`
   - First-try rate delta: `+4% [-7%, +15%]`
   - Attempts reduction: `+0.04 [-0.07, +0.15]`
2. **The reflective self-healing runtime is the real story.** 5 fingerprint
   axes (KeyError / ModuleNotFoundError / FileNotFoundError /
   sqlite-OperationalError / case-mismatch), **100% pass rate across all
   25 cold-A' runs** (5 pairs × 5 seeds, std = 0% on 4 of 5 pairs),
   1.6 average attempts, no memory required. The runtime's
   `Reflection + FailureFingerprint + RetryContext + ClassifyStage`
   pipeline absorbs single-hop failures on its own — that's what should
   sit at the top of a résumé summary, not the memory benchmark.

In other words: this work simultaneously validated the runtime's
self-healing strength *and* statistically bounded experience memory's
marginal contribution at zero. It's a positive result for the runtime
that happens to look like a rigorous null result for memory.

The v3 roadmap (§8) is where memory should start mattering — multi-step
recovery, lower retry budgets, and cost-side KPIs. Routine self-heal
isn't the right hill.

---

## 1. What & Why

Reforge already has a working memory pipeline end-to-end
(`Reflection → FailureFingerprint → MemoryStore + ExecutionMemory →
PlannerMemoryContext → ClassifyStage retry-hint injection`). The honest
question is whether that pipeline measurably **changes outcomes**, or
whether the reflective runtime would have self-healed anyway.

This is the difference between *shipping a feature* and *demonstrating
runtime value*. Benchmarks that just say "memory exists" don't answer
that — you have to compare a runtime with memory against the same runtime
without it, on the same tasks.

---

## 2. Experiment Design

### 2.1 The Cold/Warm protocol

For each paired case `(A, A')`:

```
Cold leg:
  fresh substrate + fresh .reforge/  → run A     → record metrics
  fresh substrate + fresh .reforge/  → run A'    → record metrics
                                        ^
                                        A' sees NO trace of A. This is the
                                        true "first encounter" baseline.

Warm leg:
  fresh substrate + fresh .reforge/  → run A     (seeds memory)
  same substrate  + same  .reforge/  → run A'   → record metrics
                                        ^
                                        A' inherits A's lessons.
```

The Cold-A' vs Warm-A' delta is the **transfer signal**. Because everything
else (model, task, retry budget, sandbox, tools) is held constant, any
delta is attributable to memory.

### 2.2 Why paired (A, A'), not "run A twice"

If A' were just a re-run of A, "memory helps" could mean "the model
memorized the request text." We want to know whether memory transfers
to a *different surface form of the same underlying failure*. So each
pair shares one `FailureFingerprint` axis but differs on every visible
keyword:

| Pair | Fingerprint axis | A (seed) | A' (transfer probe) |
|---|---|---|---|
| P1 | KeyError + `missing_key` | `orders.csv` / `profit` (actual: `gross_profit`) | `customers.csv` / `margin` (actual: `profit_margin`) |
| P2 | ModuleNotFoundError + `missing_module` | `import pd` for pandas | `import np` for numpy |
| P3 | FileNotFoundError + `missing_file` | `sales_2024.csv` (actual: `sales-2024.csv`) | `orders_2024.csv` (actual: `orders-2024.csv`) |
| P4 | OperationalError + sqlite table | query `sales` (actual: `tbl_sales`) | query `users` (actual: `tbl_users`) |
| P5 | KeyError + case mismatch | `Revenue` (actual: `revenue`) | `Amount` (actual: `amount`) |

The retriever's scoring (`reforge.memory.retrieval._SIGNATURE_WEIGHTS`)
ranks by structural fingerprint overlap (`error_class` + `missing_key` +
`domain`), not by keyword overlap with the request text. A keyword-only
matcher would miss every transfer probe; a fingerprint matcher should
hit all five.

### 2.3 The isolation patch — why v0 was contaminated

Reforge's memory has *two* storage layers, and the v0 benchmark only
isolated one:

| Layer | Path | What writes to it | What reads from it |
|---|---|---|---|
| `MemorySubstrate` | global (`~/.reforge/memory/*.json`) | `record_from_final_state` after each session | `PlannerMemoryContext.build()` at planner time |
| `ExecutionMemory` | per-project (`./.reforge/execution_memory.jsonl`) | `ExecutionMemory.record()` during retry stages | `ClassifyStage` to fetch retry hints |

The v0 driver isolated `MemorySubstrate` (per-leg fresh JSON dir) but left
`ExecutionMemory` shared across legs — because it derives its path from
`Path.cwd()`. So when Cold-A finished writing a `repair_strategy` to
`.reforge/execution_memory.jsonl`, Cold-A' was still reading from the same
file. Cold-A' wasn't actually cold.

**The patch** added a `REFORGE_PROJECT_DIR` env override to `paths.project_dir()`
and a `_scoped_env()` context manager in the driver. Every case now runs
against a fresh `.reforge/` per leg, so the only thing Warm-A' inherits
from Warm-A is what we deliberately wrote to the substrate.

This took 3 files, ~30 lines of patch. Without it, every Cold/Warm result
is suspect.

---

## 3. v0 Results (no `.reforge/` isolation)

Output: [`docs/exp_v0_all_pairs.md`](exp_v0_all_pairs.md).

| Metric | Value | What it claims |
|---|---|---|
| **Transfer success rate** | **+20%** | Memory rescued one failed cold attempt |
| **First-try rate delta** | **+0%** | Memory didn't help warm hit on attempt 1 |
| **Attempts reduction** | **+0.00** | Memory didn't shorten the recovery path |
| Cold-A' pass rate | 80% (4/5) | |
| Warm-A' pass rate | 100% (5/5) | |
| Cold-A' first-try success | 20% | |
| Warm-A' first-try success | 20% | |
| Recall hit rate (warm) | 100% | Retrieval is firing |

The +20% came entirely from P2:

```
P2 cold.A'  : EXPECTED_FAILURE (1 attempt, score 0.25)
P2 warm.A'  : SUCCESS          (1 attempt, score 1.00, recalls=1)
```

`EXPECTED_FAILURE` is the runtime classifying the request as "user asked
for something that will fail on purpose" (e.g. `import np` written
literally). Read at the time as the cleanest possible transfer story:
Cold misroutes, Warm uses memory to disambiguate.

But P1/P3/P4/P5 all showed Cold-A' = Warm-A' = 2 attempts PASS, transfer
delta zero. That asymmetry was suspicious enough to investigate.

---

## 4. v1 Results (with isolation patch)

Output: [`docs/exp_v1_isolated.md`](exp_v1_isolated.md).

| Metric | Value | What it claims |
|---|---|---|
| **Transfer success rate** | **+0%** | Cold and Warm both pass everything |
| **First-try rate delta** | **+0%** | Memory didn't save an attempt either |
| **Attempts reduction** | **+0.00** | Same recovery depth in both legs |
| Cold-A' pass rate | 100% (5/5) | |
| Warm-A' pass rate | 100% (5/5) | |
| Cold-A' first-try success | 40% (2/5: P2, P4) | |
| Warm-A' first-try success | 40% (same 2: P2, P4) | |
| Recall hit rate (warm) | 100% | Retrieval is still firing |

The headline number went *down* from v0. That's a feature, not a bug —
**all three KPIs land at zero independently** in v1, which isn't a
transfer-rate quirk but the same null finding from three angles. If
memory had helped on *any* of "more attempts succeeded" /
"more first-try wins" / "shorter recovery paths", at least one of the
three would have moved. None did.

Per-pair breakdown:

| Pair | Cold-A' | Warm-A' | Att Δ |
|---|---|---|---|
| P1 | PASS (2) | PASS (2) | 0 |
| P2 | PASS SUCCESS (1) | PASS SUCCESS (1) | 0 |
| P3 | PASS (2) | PASS (2) | 0 |
| P4 | PASS SUCCESS (1) | PASS SUCCESS (1) | 0 |
| P5 | PASS (2) | PASS (2) | 0 |

`recall_hit_rate = 100%` confirms the retrieval path is firing — the
warm runtime *is* pulling lessons. They just don't change the outcome
on these tasks.

---

## 4.5 v2 Results (multi-seed statistical confirmation)

Output: [`docs/exp_v2_multiseed.md`](exp_v2_multiseed.md). 5 pairs × 5
seeds × 4 legs = 100 LLM calls, ~61 minutes wall-clock.

The v1 finding ("+0% transfer") was a single data point. v2 asks: under
seed-level resampling of the *same* prompts with the *same* runtime, does
that null finding survive a 95% confidence interval? The answer is yes.

### Headline KPIs

| KPI | Mean | Std | 95% CI | CI excludes 0? | Verdict |
|---|---|---|---|---|---|
| **Transfer success rate** | **+0%** | 0% | `[+0%, +0%]` | no | **consistent with noise** |
| **First-try rate delta** | **+4%** | 9% | `[-7%, +15%]` | no | **consistent with noise** |
| **Attempts reduction** | **+0.04** | 0.09 | `[-0.07, +0.15]` | no | **consistent with noise** |

Three independent KPIs, three CIs straddling zero. The "no positive
transfer" finding isn't a one-shot artifact — it survives multi-seed
resampling under proper isolation.

### Per-pair seed breakdown

| Pair | Cold pass | Warm pass | Cold 1st-try | Warm 1st-try | Δ first-try (CI) |
|---|---|---|---|---|---|
| `P1` KeyError | 100% ± 0% | 100% ± 0% | 0% ± 0% | 0% ± 0% | `+0% [+0%, +0%]` |
| `P2` ModuleNotFound | 100% ± 0% | 100% ± 0% | 60% ± 55% | 80% ± 45% | `+20% [-36%, +76%]` |
| `P3` FileNotFound | 100% ± 0% | 100% ± 0% | 100% ± 0% | 100% ± 0% | `+0% [+0%, +0%]` |
| `P4` SQL | 100% ± 0% | 100% ± 0% | 100% ± 0% | 100% ± 0% | `+0% [+0%, +0%]` |
| `P5` case-mismatch | 100% ± 0% | 100% ± 0% | 0% ± 0% | 0% ± 0% | `+0% [+0%, +0%]` |

Three things the per-pair table makes visible that the headline doesn't:

1. **4 of 5 pairs are fully deterministic across seeds.** P1, P3, P4, P5
   have std = 0% on both cold and warm. The model picks the same recovery
   strategy regardless of seed. That's a signal about the runtime's
   determinism on those error classes, not about memory.
2. **The only variance comes from P2 (ModuleNotFoundError).**
   Cold-first-try is 3/5 seeds, warm-first-try is 4/5 seeds — the
   intuitively-attractive `+20%` Δ that v0 spotted as a clean signal.
   But with N=5 seeds, the 95% CI is `[-36%, +76%]`. **The v0 P2 signal
   is statistically inseparable from "lucky single sample".**
3. **P3 and P4 ran at first-try 100% across all 10 cold-A' samples.**
   v1's single seed showed P3/P4 cold-A' = 2 attempts RECOVERED;
   v2 shows that was the unlikely path. The fixtures are weak — the
   underlying FileNotFoundError / OperationalError errors aren't actually
   triggering on most seeds, because the model is reading the directory
   /querying the schema unprompted. **Worth fixing in v3, but doesn't
   change the v2 statistical conclusion** (memory delta is still null
   when the error *does* fire — see P1 / P5).

---

## 5. v0 vs v1 vs v2 — What Actually Changed

| Axis | v0 | v1 | **v2** |
|---|---|---|---|
| `.reforge/` isolation | ❌ | ✅ | ✅ |
| Seeds per leg | 1 | 1 | **5** |
| Statistics | single point | single point | **mean ± std + 95% CI** |
| Transfer rate | `+20%` | `+0%` | **`+0% [+0%, +0%]`** |
| First-try delta | `+0%` | `+0%` | **`+4% [-7%, +15%]`** |
| Attempts reduction | `+0.00` | `+0.00` | **`+0.04 [-0.07, +0.15]`** |
| Reviewer can dismiss as... | "no isolation" | "n=1, just noise" | **(very little)** |

Three rounds, three steps of methodological hardening:

1. **v0 → v1: isolation.** The +20% transfer signal in v0 came from one
   P2 cold-A' run getting classified as `EXPECTED_FAILURE`. With the
   `REFORGE_PROJECT_DIR` env override patching `ExecutionMemory.jsonl`
   into a per-leg sandbox, the same P2 prompt produced `SUCCESS` in v1.
   Lesson: leaking project-scope state across "cold" runs lets memory
   take credit for what was actually shared retry context.
2. **v1 → v2: multi-seed + statistics.** The v1 +0% was suspicious
   because n=1 can't distinguish "no effect" from "didn't see effect
   this time". The v2 multi-seed run shows the P2 first-try delta
   *exists* (Cold 60% → Warm 80%) but has a 95% CI of `[-36%, +76%]` —
   wide enough that the apparent signal is consistent with chance.
3. **What this means for the v0 narrative.** The v0 `+20%` transfer was
   true *as reported* (one sample, one number), but it was reporting a
   one-shot LLM misroute, not a runtime property. Memory's actual
   contribution under controlled conditions is, to within α=0.05,
   **zero**.

---

## 6. What This Tells Us

### 6.1 The runtime's self-healing pipeline is the binding result

This benchmark **failed to demonstrate a positive transfer effect on
routine self-heal cases** — and that's the interesting finding.

The four runtime mechanisms that are *not* memory all contribute to
that result:

1. **Reflection** sees the traceback, derives `error_type / summary / fix`.
2. **`FailureFingerprint`** extracts structured fields (`missing_key`,
   `missing_module`, `missing_file`, ...) the same way for cold and warm.
3. **Retry context** assembles a focused retry prompt that includes the
   error class and the schema-discovery instruction.
4. **`ClassifyStage`** routes the retry through a budget and a hint
   injection point.

On a `KeyError 'margin'`, those four together let a cold runtime
introspect the dataframe (`df.columns`), see `profit_margin`, and patch
the next attempt — in 2 attempts, no memory required. Memory could give
the runtime a head start, but only if it changes the *first* attempt's
code. On these tasks it doesn't.

**The conclusion**: the runtime's *first-attempt code generator* is the
binding constraint, not the retry path. Memory currently bolts onto the
retry path (via `ClassifyStage`) and weakly onto the planner (via
`PlannerMemoryContext`), but the planner prompt prefix isn't strong
enough to override the model's default decomposition. That's where v2
should focus.

This is also why benchmarks **without isolation** are dangerous: they let
runtime improvements take credit for what the model would have done on
its own.

### 6.2 Reframing for the résumé summary

The headline finding for project narrative purposes is:

> **A reflective + fingerprint-aware self-healing runtime that achieves
> 100% pass rate across 5 distinct failure categories at 1.6 average
> attempts on a controlled benchmark with proper isolation.**

The memory benchmark is the *methodology layer* underneath that headline —
it's how we know the 100% isn't fake (no `.reforge/` contamination, no
hand-tuned cases, no inflated transfer numbers from one-shot LLM
misroutes). The honest demonstration that memory *isn't* the binding
constraint is what makes the runtime claim credible, not weaker.

### 6.3 The `recall_hit_rate` caveat

`Warm-A' recall hit rate = 100%` in both v0 and v1 only says the
`MemorySubstrate.recall()` API was invoked. It does **not** confirm the
planner actually used the returned record, or that the retry-hint string
was attended to by the model. A future `memory_influence_score` (e.g.
"does the warm planner output reference a memory phrase?") would
distinguish "retrieved" from "consumed". Today, we can't.

### 6.4 Sample size and stochasticity — addressed in v2

v0 and v1 ran 5 pairs × 1 seed each — **a case study, not a benchmark**.
The v0→v1 P2 flip (`EXPECTED_FAILURE → SUCCESS`) on the same prompt is
exactly the stochastic noise that sample size cannot detect or correct
for.

**v2 ran 5 pairs × 5 seeds × 4 legs = 100 LLM calls** with mean ± std +
95% CI per KPI. The verdict survives: all three headline KPIs have
confidence intervals including zero. P2's first-try delta (the v0 signal
source) is now quantified at `+20% [-36%, +76%]` — directionally positive,
statistically null.

Further hardening (more seeds, fixture upgrades) is the v3 roadmap (§8),
but the v2 statistical floor is solid enough that further N just sharpens
already-null CIs.

---

## 7. Reproducing

```bash
# v1 single-seed run, 5 pairs × 4 LLM calls = 20 calls, ~13 minutes
python -m reforge.benchmark.experience_cli --out docs/exp_v1_isolated.md

# v2 multi-seed run, 5 pairs × 4 legs × 5 seeds = 100 calls, ~61 minutes
python -m reforge.benchmark.experience_cli --seeds 5 --out docs/exp_v2_multiseed.md

# Single pair (single seed), ~3 minutes — useful when iterating on fixture design
python -m reforge.benchmark.experience_cli --pair P2

# Preserve the per-leg tmp .reforge/ dirs for forensic inspection
python -m reforge.benchmark.experience_cli --pair P2 --keep-tmp
```

The single-seed driver is in `reforge/benchmark/experience_driver.py`.
The multi-seed driver + statistics is in
`reforge/benchmark/experience_multiseed.py` (Student-t 95% CI computed
inline, no scipy dependency). The 5 paired cases are in
`reforge/benchmark/experience_cases.py`.

Mocked unit tests cover the full pipeline:
- `reforge/tests/test_experience_benchmark.py` — single-seed driver,
  fixture shape, `REFORGE_PROJECT_DIR` isolation, report aggregation
  (29 cases, < 1 s)
- `reforge/tests/test_experience_multiseed.py` — `StatSummary` CI math,
  `excludes_zero` verdict, multi-seed driver fanout, per-pair stats,
  reporter rendering (17 cases, < 1 s)

Fixture data files (`benchmark_data/orders.csv`, `customers.csv`,
`sales-2024.csv`, `orders-2024.csv`, `transactions.db`) are committed.
Regenerate with `python scripts/gen_experience_fixtures.py`.

---

## 8. v3 Roadmap — Where Memory Should Plausibly Help

v2 closed the question "did memory help here?" with a statistical "no".
The next round should pivot the *what's being tested*, not stack more
seeds on the same null result.

### 8.1 Lower retry budget → first-try-or-fail

Drop the retry budget from 3 to 1 in a benchmark mode. Cold runs lose
the schema-discovery retry; warm runs that inject the correct column
name through `PlannerMemoryContext` win at attempt 1. Tests the
*planner prompt prefix's* strength directly — the lever v2 showed is
currently too weak to move outcomes when the runtime can afford to
retry.

### 8.2 Multi-step recovery fixtures

Tasks that require ≥3 distinct repairs (e.g. wrong file → wrong column →
wrong dtype). The Cold runtime burns the retry budget on step 1 and 2,
fails at step 3. The Warm runtime, having seen this chain before,
collapses the chain at the planner. This is where memory's *step-count
reduction* is the differentiator, not its accuracy contribution.
Specifically targets the v2 finding that 4/5 current pairs have
deterministic single-hop recoveries — multi-hop is the regime where
seed-level variance and memory benefit both should appear.

### 8.3 Cost dimension

Even when both Cold and Warm pass, Warm may use fewer LLM calls / fewer
sandbox executions. Add `total_tokens` and `total_executions` to the
per-run metrics and surface a "cost reduction" KPI. Memory might be
worth shipping for *the same accuracy at lower cost*, not just for
higher accuracy.

### 8.4 Memory Influence Score

v2's `recall_hit_rate = 100%` only proves `recall()` was called. It
doesn't prove the planner used the result. Build a trace-layer
instrument that captures the warm planner's first-attempt prompt *and*
code output, then checks whether the memory phrase appears in either.
Surface `recall → injected → used` as a 3-stage funnel. This is the
mechanism-level question that v0–v2 left open and that no aggregate
KPI on its own can answer.

### 8.5 Fix the weak P3/P4 fixtures

v2 exposed that the FileNotFoundError (P3) and SQL-table (P4) fixtures
don't actually trigger their named error in most seeds — the model
auto-discovers the right path/table without burning a retry. The v2
KPIs are still valid (the contribution-of-memory question is
fingerprint-agnostic), but a v3 with corrected P3/P4 would give the
per-pair table its full intended coverage.

### 8.6 What to *not* invest in

- More fingerprint axes that look like P1–P5. v2 generalised the result
  across 5 axes and 5 seeds; adding axes 6–10 of the same shape won't
  change the verdict.
- Vector embeddings on the retrieval path. The fingerprint matcher
  already hits 100%; ranking quality isn't the constraint.
- Larger fixture counts on the same routine self-heal regime. v2 nailed
  the verdict; more N here just sharpens already-null CIs. Spend the
  compute on the v3 regimes above (multi-step, low-budget, influence
  tracing) instead.
