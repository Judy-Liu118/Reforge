# Memory-transfer directed probe — pilot result

> **Directed probe, not independent evidence.** This result does NOT overturn
> or replace the BIRD governor-vs-naive nulls, and does not generalise outside
> the predicted interval. Pre-registration:
> [`MEMORY_TRANSFER_PROBE_PREREG.md`](MEMORY_TRANSFER_PROBE_PREREG.md)
> (branch `docs/memory-probe-prereg`, commit `6ad241c`).

## What ran

Pilot only (per the prereg instrument-gate step), 2026-07-29: budgets {1, 3} ×
1 seed × 3 pairs (MP1 KeyError / MP2 OperationalError / MP3 FileNotFoundError,
private-encoding schema + legend, `reforge/benchmark/memory_probe_cases.py`),
plus a verbose single-pair confirm on MP2. **The full 5-seed axis was NOT run**
— the pilot showed the instrument did not test the hypothesis (below), so
running it would measure an artifact, not memory transfer.

## Result

| | budget=1 | budget=3 |
|---|---|---|
| cold first-try fail rate | 3/3 = 100% (trap fires ✓) | 3/3 = 100% |
| cold-A' pass / warm-A' pass | 0/3 / 0/3 | 0/3 / 0/3 |
| read-legend (all attempts, both arms) | all False | all False |
| attempts | MP1/MP3 = 2, MP2 = 1 | identical |

## Root cause (confirmed from generated code, MP2 verbose)

Not a null: an **instrument-level false-positive stop**. The trap fires, but
the model "repairs" by *gracefully masking* the error rather than discovering
the real name, and the no-gold evaluator accepts it:

- **seed** (`inventory`, gold 660): attempt 2 runs
  `SELECT name FROM sqlite_master WHERE name='inventory'`, finds it absent, and
  returns `0` / prints a warning → `outcome=RECOVERED`, answer `'0'`.
- **cold-A'** (`shipments`, gold 1500): `SELECT SUM(qty) FROM shipments` →
  `outcome=SUCCESS`, `attempts=1`, answer `'60'`.

Every SUCCESS/RECOVERED carries a wrong answer (0 / 60 / warning); the gold
keyword marks them all `pass=False`. Because the **seed** false-succeeds, no
correct repair is stored, so warm has nothing to recall; because **cold**
false-succeeds, budget is never spent discovering the legend — which is why
`read_legend` is all False and budgets {1,3} are identical.

## Positioning

Third independent task family reproducing the same boundary as the BIRD
(silent-error) and targeted/must_fail (observable-crash) results: a **no-gold
evaluator's false positive** makes "gracefully mask the error" a cheaper
success path than a real fix, which collapses any retry/memory gain. Fixing it
needs a gold-aware (hybrid) evaluator — deferred as KNOWN_LIMITATIONS L6, and
out of scope here because it changes the system under test mid-experiment.

## Caveats

- **Corpus not validated.** The seed is effectively unsolvable under the
  current evaluator (false-positive accept), so this corpus never backed any
  headline and the 5-seed axis stays unrun.
- **Shared-DB pollution suspected.** `benchmark_data/mp_warehouse.db` is shared
  across runs; the `answer='60'` on a non-existent table points at cross-run
  contamination. A real continuation must copy the data into a per-run private
  workspace.
- **No p-hacking.** The corpus, budgets, and thresholds were not
  adjusted-and-rerun to surface a positive.
