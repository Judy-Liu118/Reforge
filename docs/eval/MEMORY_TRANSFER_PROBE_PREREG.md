# Memory-transfer directed probe — pre-registration

> **Status: PRE-REGISTRATION.** Locked before any real-data run. The commit
> hash of this file at lock time is the reference every number this probe
> reports must cite. No post-data edit may loosen the corpus rule, the metric
> set, or the significance rule; the significance discipline is carried over
> verbatim from `PHASE0_METRICS.md` §3.

> **This is a directed probe against an already-predicted interval, not
> independent evidence.** Its result — positive or null — CANNOT overturn or
> replace the three BIRD governor-vs-naive nulls (`PHASE1_BIRD_ABLATION*.md`).
> A positive result would confirm an effect *only inside the predicted
> interval* and does not generalise outside it. This banner must be reproduced
> in any report of the result.

## 1. Hypothesis

Cross-session memory (the governor's `repair_hint` recall via
`ExecutionMemory`) yields a measurable transfer gain **only** in the interval
where **the fix cannot be derived from the error text itself**. Where the
traceback already carries its own fix, a memory-less retry that simply re-reads
the traceback suffices, so the gain collapses.

Directional predictions (the effect increases as):

- **task difficulty ↑** — longer reasoning chains, the failing line sits
  further from the root cause;
- **model prior over the domain ↓** — the correct fix is not in the model's
  general knowledge, so it cannot be guessed without the recalled hint;
- **retry budget ↓** — with fewer attempts, cold cannot afford the extra
  introspection round that warm skips by recalling.

The primary prediction is falsifiable: at the extreme end (high difficulty,
low prior, tight budget) the warm−cold `transfer_success_rate` delta's 95% CI
excludes zero and is ≥ its value at the loose-budget end. If the extreme-end CI
includes zero, the hypothesis is **not supported in the measurable range** — a
stronger disconfirmation than the existing medium-band null.

## 2. Literature basis

- **Olausson et al. (ICLR 2024)**: the *relative* self-repair gain over a
  same-budget baseline grows with difficulty — GPT-3.5 gains up to ~1.34× at
  APPS-competition vs ≤baseline at introductory (§4.1 / Fig. 14). Two other
  quantities must NOT be conflated with it: single-repair *success rate* FALLS
  with difficulty (Table 2, appendix, "Repair success rates in various
  settings", introductory → competition: GPT-4 28.8% → 8.6%, GPT-3.5 13.7% → 1.5%), and replacing GPT-4's own feedback
  with a human's raises overall repair success 33.3% → 52.6% (1.58×, Table 1) —
  the feedback-quality bottleneck. Directions differ (relative gain rises,
  absolute repair success falls); both say the same thing for this probe: hard +
  weak-feedback is where a *correct* recalled fix has the most room and the model
  can least supply it itself.
- **Reflexion (Shinn et al., arXiv:2303.11366), Table 3** (GPT-4, 50 hardest
  HumanEval-Rust): removing self-reflection while keeping the test signal leaves
  the score at the base model's (0.60 vs 0.60); full Reflexion reaches 0.68 — a
  signal with no layer to turn it into a repair direction yields no gain.

See [`RELATED_WORK.md`](RELATED_WORK.md). These place the probe: the existing
P1–P5 memory ablation is medium-difficulty / strong-prior / loose-budget — the
low-effect end — and is null at 5 seeds (README eval section). This probe moves
the corpus to the high-effect end the literature predicts.

## 3. Corpus: selection, rationale, and the L8 alignment rule

**What is selected.** Three fingerprint axes, each a `PairedCase` (seed A /
transfer A′) built to sit at the extreme end of all three knobs:

- **Low prior**: a private, non-guessable schema — column/table names follow an
  internal encoding (e.g. `c_0001_rev` for revenue) resolvable only by reading a
  `legend` mapping in the same dataset. The correct fix ("look up the legend,
  map the semantic name to the encoded name") is in neither the traceback
  (`KeyError 'revenue'` names nothing about the legend) nor the model's general
  knowledge (the encoding is private).
- **High difficulty**: multi-step tasks (load → resolve-via-legend → aggregate),
  not single-column reads.
- **Tight budget**: `max_retry ∈ {1, 3}`, chosen a priori (per `PHASE0_METRICS`
  §1, variants drawn from {1,3,5}, never from results). `1` is the extreme end;
  `3` is the existing-default control for the budget axis.

**Why it is in the predicted interval.** The fix is un-derivable from the error
text (traceback points at the missing semantic name, not the legend) and absent
from the model's prior (private encoding) — exactly the interval the hypothesis
names, and the opposite corner from P1–P5.

**L8 alignment rule (binding corpus constraint).** Per `KNOWN_LIMITATIONS.md`
L8, `recall_similar` matches on failure *shape*, so a hint keyed to the wrong
root cause can be injected. To keep a null/negative interpretable:

- seed A and transfer A′ MUST share the **same schema and the same root cause**
  (same private encoding), so the recalled repair strategy genuinely applies to
  A′. This is stricter than P1–P5, which pair across *different* datasets (P1:
  `orders.csv` → `customers.csv`) — the exact shape-not-identity pattern L8
  confirmed live.
- The transfer must require the *strategy* ("consult the legend"), not a literal
  value from A. A hint that only works if A′'s answer equals A's would be a
  leak, not transfer.
- Any pair whose A/A′ root causes are merely *similar* (not identical) is
  excluded before running.

Concrete case prompts/data are constructed AFTER this file is committed, under
the rules above; construction never consults KPI outcomes.

## 4. Metrics, pairing, significance (locked, aligned with PHASE0_METRICS)

Reuses the shipped memory harness KPIs (`experience_multiseed.py`), all
**warm − cold** per-seed paired deltas:

- **`transfer_success_rate`** (primary) — per seed, (warm A′ pass rate − cold
  A′ pass rate) over pairs.
- **`first_try_delta`** — per seed, warm A′ first-try rate − cold A′ first-try
  rate.
- **`attempts_reduction`** — per seed, cold avg attempts − warm avg attempts.

`passed` / `first_try` / `attempts` follow `PHASE0_METRICS.md` §3 definitions.

**Pairing.** `delta_seed[i] = KPI(warm, seed=i) − KPI(cold, seed=i)`, averaged
over pairs; aggregate over seeds with `summarise()` → mean, std, 95% CI
half-width (Student-t, df = N_seeds − 1), `excludes_zero`.

**N_seeds = 5.** Matches the already-published memory ablation (README) so the
probe is comparable to the null it refines, and exceeds the `PHASE0_METRICS`
secondary-axis floor of 3 (a documented doc-internal difference; the stricter
value is taken).

**Significance (locked before any data).** A KPI is reportable as an effect
only if its per-seed paired 95% CI does not cross zero. Any CI including zero is
reported as "no significant effect (CI includes 0)" and may not appear as a
headline or directional claim. Verbatim from `PHASE0_METRICS.md` §3.

**Instrument gate (mechanism only, not direction).** Before the headline run, a
calibration confirms: cold A′ first-try failure rate > 0 (the trap fires), warm
runs actually write and recall a `repair_hint`, and all KPIs compute without
NaN. Result direction is NOT a gate — gating on it would be circular.

**Diagnostic observation (pilot; non-gating; not part of the hypothesis).**
Record, in the cold arm, the fraction of recoveries reached by *autonomous
introspection* — a run that read the legend / listed the schema and then
self-repaired with no external hint. Report this fraction in the pilot. A high
value means cold can find the fix on its own, weakening the probe's mechanism
premise; this must be known BEFORE the full 5-seed run, not after. It is an
observation only: it does not gate direction and does not alter the hypothesis
or the §1 predictions.

### Analysis focus — budget = 1 is the primary arena

Stated here before any data as an analysis lens; it does NOT alter the
hypothesis or the §1 predictions. A self-contained sandbox task is fully
introspectable, so the information a recalled hint carries is almost never
*unknowable* to cold — only more *expensive to discover*. The gain, if any, is
therefore expected in `attempts_reduction` / `first_try_delta` (discovery cost),
not in `transfer_success_rate` (cold can usually discover the fix given enough
attempts). Under `max_retry = 1`, cold has no second attempt to spend on
discovery, so discovery cost converts into a success-rate difference. Hence a
result where **budget = 1 shows an effect and budget = 3 is null is itself a
clean confirmation of the prediction**, not a mixed outcome.

## 5. Constraints (honesty locks)

- **L8**: same structural fingerprint → same root cause, enforced per §3.
  Violation can produce a negative effect that is uninterpretable.
- **No p-hacking on null**: if the result is null, it is reported as null. The
  corpus, budget set, seeds, and thresholds are NOT adjusted-and-rerun to
  surface a positive.
- **Directed-probe banner**: every report of this result reproduces the top
  banner — it does not overturn or replace the BIRD nulls, and does not
  generalise outside the predicted interval.

## 6. What this probe does NOT license

- Any claim that cross-session memory helps in general, or on the medium-band /
  strong-prior workloads where P1–P5 already measured null.
- Any re-interpretation of the BIRD governor-vs-naive result.
- Promotion of a case-level or single-KPI signal to a headline when the primary
  per-seed CI includes zero.

## 7. Future direction — multi-step, path-dependent environments (UNMEASURED)

> Recorded as a design only. NOT measured. NOT part of the locked hypothesis,
> §1 predictions, corpus rule (§3), metric set (§4), or significance rule above,
> none of which this section changes. No effect-direction claim is made — the
> correct status is "unmeasured", not "expected positive". Motivated by the
> post-hoc failure-type reading in `CAPABILITY_VS_ENVIRONMENT_POSTHOC.md`.

**Task form.** A multi-step, path-dependent environment: it opens with an error,
and the path to success needs several edits along the way, where the model's
default preferred path disagrees with the one the environment actually permits
(a real schema, an API quirk, a multi-step dependency).

**Why it would sit in the predicted interval (§1).** The failure is
environment-mismatch, not capability — the model *can* execute each step, it
just does not know which path the environment allows — and the fix is not
derivable from the error text (the traceback names the symptom, not the
environment's actual shape). This is the opposite corner from single-function
code generation, whose failures are capability-type (see the post-hoc note).

**Construction rules (to be locked before building any case).**
- Real structure — a genuine schema / API quirk / multi-step dependency; the
  case is NOT reverse-engineered around one specific hint.
- Cold-recoverable — the cold arm must be able to discover the fix on its own
  eventually; the mechanism claim is "memory is cheaper", not "impossible
  without memory" (per §4.3, budget=1 converts discovery cost to success rate).
- L8 — same structural fingerprint → same root cause (as §3).
- Labelled synthetic environment.

**Anticipated risks (where this probe most likely fails).**
- *Introspectable-sandbox collapse* (same failure mode as private-encoding): if
  every step is discoverable from the environment, cold with enough budget finds
  the path too, and any effect degrades to discovery-cost only — visible at
  budget=1, null at budget=3, never a success-rate gain.
- *Capability contamination*: if any step's difficulty is writing hard code
  (rather than choosing the right path), a capability-type failure mixes in and
  muddies the "pure environment-mismatch" claim. Each step's obstacle must be
  path / order / environment-fact, not code ability.
- *Prior leakage*: a "real API quirk" the strong model has already seen raises
  the prior above the low-prior corner and shrinks the interval.
- *Reverse-design leak*: multi-step makes it tempting to build the environment
  around the hint, producing a "memorise one fact and it passes" leak rather
  than strategy transfer (violates §3's strategy-not-value rule).
