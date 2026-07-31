# pass@t re-analysis — is self-repair worth its compute on BIRD?

> **POST-HOC. Re-analysis of EXISTING records, not a pre-registered result.**
> Reads the three already-run BIRD ablation rounds (`phase1_records.jsonl`,
> `phase1_records_r2.jsonl`, `phase1_records_r3.jsonl`) on a compute-normalized
> axis. It does **not** modify or overturn any conclusion in
> `PHASE1_BIRD_ABLATION{,_R2,_R3}.md`; it *reinforces* the existing null on a
> new axis. It is not part of the memory-transfer pre-registration and licenses
> no predictive claim.

## 1. The question (Olausson et al., pass@t)

Olausson et al. (ICLR 2024) argue the correct way to judge self-repair is not
"does repair raise pass rate?" but "**at the same compute budget, would drawing
more independent samples have done better?**" — pass@t, with total sampled
tokens on the x-axis. The BIRD ablation already found the governor's retries buy
**no** gold-accuracy gain over naive; pass@t asks the sharper question: given
that repair *costs* tokens, does it sit below the plain-sampling curve?

## 2. Data and method

- **Corpus.** Three rounds, each `2 modes × 20 cases × 5 seeds = 100` runs per
  mode (`governor` = retry-with-feedback; `naive` = single-shot,
  `exit_code==0` accept). Gold = `passed` (BIRD SQL comparator; equals the final
  `comparator_correct`).
- **Token budget.** `tokens_prompt + tokens_completion` per run, **full
  coverage** (`tokens_unknown = 0` on all 600 runs). This is exactly the
  "total sampled tokens" pass@t needs. **naive** is genuinely single-shot
  (avg attempts 1.00–1.01), so its per-run total = cost of one independent
  sample; **governor** avg attempts 1.31–2.70, so its per-run total = the cost
  of one repaired answer.
- **pass@k (oracle).** For each case, the 5 seeds are 5 i.i.d. naive samples
  with `c` correct; `pass@k = 1 − C(5−c, k)/C(5, k)` (Chen et al. unbiased
  estimator), averaged over the 20 cases. **This assumes a perfect verifier
  selects the correct sample out of k** — see §5, this is an upper bound our
  runtime cannot reach.
- **What is NOT available.** Per-*attempt* token counts are not recorded — only
  per-run totals (`attempt_observations` carries no token field). Run-level
  pass@t does not need them; a within-governor "first-sample vs repair-round"
  token split is therefore out of scope and is **not** estimated.

## 3. Results — pass@k curve vs the governor's operating point

Each row is naive oracle pass@k at budget `k × (naive tokens/sample)`; the
governor is a single point at its own gold pass and its own token cost.

**Round 1** (naive 4923 tok/sample; governor 15512 tok/run = **3.15×**):

| budget (naive-samples) | tokens | naive pass@k (oracle) |
|---|---|---|
| 1 | 4 923 | 0.650 |
| 2 | 9 845 | 0.695 |
| 3 | 14 768 | 0.700 |
| 5 | 24 614 | 0.700 |
| **governor** | **15 512** | **0.650** |

**Round 2** (naive 5065 tok/sample; governor 8376 tok/run = **1.65×**):

| budget (naive-samples) | tokens | naive pass@k (oracle) |
|---|---|---|
| 1 | 5 065 | 0.610 |
| 2 | 10 129 | 0.655 |
| 3 | 15 194 | 0.675 |
| 5 | 25 324 | 0.700 |
| **governor** | **8 376** | **0.610** |

**Round 3** (naive 5110 tok/sample; governor 8353 tok/run = **1.63×**):

| budget (naive-samples) | tokens | naive pass@k (oracle) |
|---|---|---|
| 1 | 5 110 | 0.620 |
| 2 | 10 220 | 0.695 |
| 3 | 15 329 | 0.745 |
| 5 | 25 549 | 0.800 |
| **governor** | **8 353** | **0.610** |

**Reading each round.** The governor's gold pass equals naive **pass@1** in all
three rounds (0.650 / 0.610 / 0.610 vs 0.650 / 0.610 / 0.620) while costing
1.6–3.2× the tokens. At the governor's own budget, plain sampling reaches:

- R1: budget 15 512 tok ≈ 3.15 naive-samples → naive pass@3 = **0.700 > 0.650**.
- R2: budget 8 376 tok ≈ 1.65 naive-samples → between pass@1 0.610 and pass@2
  0.655, i.e. **≥ 0.610** = governor.
- R3: budget 8 353 tok ≈ 1.63 naive-samples → between pass@1 0.620 and pass@2
  0.695, i.e. **> 0.610** = governor.

In every round the governor sits **on or below** the naive sampling curve: the
tokens it spends on repair would, spent on independent sampling, match or beat
it. Self-repair is not compute-efficient here.

## 4. Seed-paired significance (house style, aligned with PHASE1)

Per-seed paired deltas over the 5 seeds, Student-t 95% CI (df = 4):

| round | gold pass Δ (gov − naive) | 95% CI | token ratio gov/naive | 95% CI |
|---|---|---|---|---|
| R1 | +0.000 | [−0.044, +0.044] — **includes 0** | 3.15× | [2.99, 3.32] — **excludes 1** |
| R2 | +0.000 | [−0.044, +0.044] — **includes 0** | 1.66× | [1.53, 1.78] — **excludes 1** |
| R3 | −0.010 | [−0.091, +0.071] — **includes 0** | 1.64× | [1.48, 1.80] — **excludes 1** |

The accuracy delta **reproduces the existing null** (CI includes 0 in all three
rounds — no change to the PHASE1 conclusion). The token-cost ratio's CI
**excludes 1** in all three: the governor is significantly more expensive for
that same-accuracy result. That pairing is the pass@t verdict in the house's own
statistic: **equal accuracy, significantly higher compute.**

## 5. The load-bearing finding — the bottleneck is the verifier, not the search strategy

The pass@k column exposes a premise deeper than the cost comparison: **it
assumes a perfect verifier that can pick the correct sample out of k.** Our
runtime has no such thing. So the honest, deployable statement is not "sampling
beats repair" — it is stronger and more general:

> **Without a reliable verifier, neither retry nor resampling converts compute
> into accuracy.** Repair spends tokens re-generating; sampling spends tokens
> drawing more candidates; but with no gold check to *select* the right one,
> extra compute buys nothing at runtime. The bottleneck is the verdict, not the
> search strategy.

This is one bottleneck seen from three sides, all pointing at the same
missing-gold-verifier:

1. **pass@t (here)** — governor spends 1.6–3.2× the tokens for no gold gain over
   naive pass@1 (§3–§4); the naive pass@k advantage is only reachable *with* an
   oracle selector we do not have.
2. **`EVALUATOR_FP_MEASUREMENT.md`** — the no-gold evaluator has an 18% false
   positive rate on MBPP; it cannot tell a silent-wrong answer from a correct
   one, so it cannot be the selector pass@t assumes.
3. **BIRD retries (these records)** — the governor's retries fire but the net
   gold-accuracy delta is null in all three rounds (§4), and they are triggered
   almost entirely by *quiet evaluator rejections*: counting each retry by the
   exit code of the attempt that triggered it, clean-exit (`exit 0`) rejections
   account for 168/170 (R1), 30/32 (R2), 30/31 (R3) — the loud (`exit ≠ 0`)
   failures the detector actually needs are near-absent (2 / 2 / 1 attempts per
   round) and never occur twice in a row (0 runs with two consecutive loud
   failures). The evaluator triggers the extra attempts on outputs it cannot
   verify, and they move no net accuracy — the same rejection-without-a-gold
   loop, and the reason the repeated-signature detector never fires
   (`KNOWN_LIMITATIONS.md` L3).

Add a gold verifier and both levers (repair, resampling) become usable; without
one, tuning either is rearranging cost. This is why the memory/governor ablation
and the FP measurement land in the same place from different directions.

## 6. What this does and does not license

- **Oracle-selector caveat (load-bearing).** naive pass@k assumes a perfect
  verifier picks the correct sample out of k. Our runtime has **no gold
  selector** — that is the 18% evaluator false-positive wall
  (`EVALUATOR_FP_MEASUREMENT.md`). So the naive pass@k column is an **upper
  bound not achievable at runtime**: naive would accept its *first* `exit 0`
  sample, correct or not. The deployable claim is the narrower, robust one: the
  governor spends 1.6–3.2× the tokens for **no gold gain over naive pass@1**, and
  *neither* arm can convert extra compute into accuracy without a gold verifier.
  pass@t and the FP finding point at the same bottleneck — **the verifier, not
  the search strategy.** Repair adds tokens; sampling adds tokens; without a
  gold check to select, neither cashes them in.
- **pass@k uses seeds as i.i.d. samples** (n = 5 per case). The k > 1 curve is
  therefore a cross-seed estimate, **not** a seed-paired statistic; only the
  §4 operating-point delta and token ratio are seed-paired. The plateau at
  ~0.70 (R1/R2) is the ~30% of cases no seed ever solves (a hard-case ceiling at
  n = 5, not a property of repair vs sampling).
- **Post-hoc.** Not pre-registered; budgets/tiers were read off the data, not
  fixed in advance. It changes no existing report's conclusion.

## 7. Reproduce

```
python - <<'PY'
import json
from math import comb
def passk(n,c,k): return 1.0 if n-c<k else 1.0-comb(n-c,k)/comb(n,k)
for fn in ["phase1_records","phase1_records_r2","phase1_records_r3"]:
    recs=[json.loads(l) for l in open(f"docs/eval/{fn}.jsonl",encoding="utf-8")]
    by={}
    for r in recs:
        tot=r["tokens_prompt"]+r["tokens_completion"]
        by.setdefault(r["case_id"],{}).setdefault(r["mode"],[]).append((bool(r["passed"]),tot))
    cases=sorted(by)
    naive_ps=sum(t for c in cases for p,t in by[c]["naive"])/(len(cases)*5)
    gov_ps  =sum(t for c in cases for p,t in by[c]["governor"])/(len(cases)*5)
    print(fn, "naive/sample",round(naive_ps), "gov/run",round(gov_ps), "ratio",round(gov_ps/naive_ps,2))
    for k in range(1,6):
        pk=sum(passk(5,sum(1 for p,_ in by[c]["naive"] if p),k) for c in cases)/len(cases)
        print("  k",k,"pass@k",round(pk,3),"budget_tok",round(k*naive_ps))
PY
```
