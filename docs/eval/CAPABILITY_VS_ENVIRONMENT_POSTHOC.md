# Why data-recovery cases recover and MBPP does not — a failure-type axis

> **POST-HOC. Comparison of EXISTING records, not a pre-registered result.**
> This reads already-run data (`PHASE0_CALIBRATION.md`, 2026-07-10; the MBPP
> `fp_sweep`/`retry_diff` runs, 2026-07-30) and does not license any causal or
> predictive claim. It is not part of the memory-transfer pre-registration and
> does not alter its hypothesis or §1 predictions. Where the records are
> insufficient, that is stated inline.

## 1. The question

Both are "a first attempt that fails." Why does a data/EDA recovery case
(e.g. `KeyError` on a wrong column name) get repaired on retry, while an MBPP
single-function task does not?

## 2. Evidence (from the records, not inferred)

| dimension | data-recovery cases (Phase 0) | MBPP (tonight) |
|---|---|---|
| example | `t2_users_key_error` (KeyError), `t3_orders_dtype_recover` (dtype) | `Mbpp/97` SyntaxError, `Mbpp/126` TypeError, + 17 silent |
| exit code | non-zero (observable: `top_level_exception=KeyError`) / eval-driven | crashes `exit 1`; **silent wrong = `exit 0`** |
| retry triggered? | yes | crashes yes; **silent wrong: no** (evaluator has no gold) |
| retry outcome | **recovered**: `t3` 3/3 seeds `recovered=true`; `t2` governor seed=2 + naive seed=0 recovered (not all seeds — governor seed 0/1 `retries_exhausted`) | **not recovered**: all 4 crashes `FAILED`; 17 silent never retried |

Fix-derivability from the error text — the load-bearing difference:

- **Data-recovery**: `KeyError 'users'` / `KeyError 'profit'` names the *wrong*
  key, never the *right* one. The fix ("the real table is `tbl_users`", "the
  real column is `gross_profit`") is a **fact about this specific dataset**,
  absent from both the traceback and general knowledge — but **discoverable
  from the environment** (`sqlite_master`, `df.columns`). The case designs make
  this explicit (`experience_cases.py` P1/P4 descriptions; Phase 0 t2/t3), and
  the records show recovery actually happened.
- **MBPP crashes**: `SyntaxError: invalid syntax`, `TypeError: sum() missing
  argument` — the fix is **code knowledge itself**. `retry_diff` shows the weak
  model failing to apply it: `Mbpp/97` left the root-cause line (`import from …`)
  and patched the wrong place; `Mbpp/126` regenerated **identical** code. The
  traceback carried full information; the model could not use it.
- **MBPP silent wrong** (the majority, and 18/18 of deepseek's errors): `exit 0`,
  no signal at all — the no-gold evaluator can't tell it's wrong, so no retry
  fires.

## 3. The axis this supports — capability vs environment-mismatch

### The five failure forms actually observed

The "capability vs environment-mismatch" split is the top layer; underneath it,
the records show **five** distinct forms, each null for a *different* reason.
Only the last has any room for a memory/retry mechanism.

| failure form | signal? | can the model fix it? | outcome |
|---|---|---|---|
| silent wrong (BIRD / MBPP, `exit 0`) | none | — | no retry fires → null |
| crash + strong model (`deepseek-v4-pro`) | yes | almost never crashes — no trigger | null (no observable failure) |
| crash + weak model (`qwen-turbo`) | yes | can't | retry fires → still FAILED |
| crash + traceback carries the fix (`must_fail`) | yes, sufficient | fixes it unaided | recovers, mechanism adds nothing — *design premise, not a measured bucket rate; measured anchor = Phase 0 naive arm (see provenance)* |
| environment mismatch (column / table / dtype) | yes, but not enough to pin the fix | can fix — just doesn't know *where* | RECOVERED (not 100%) |

Row-by-row provenance (records, not inference):

- **silent wrong** — MBPP: all 18 wrong answers `exit 0` (§2; `EVALUATOR_FP_MEASUREMENT.md` §3). BIRD: the same silent-wrong argument behind the ablation null.
- **crash + strong model** — deepseek-v4-pro produced **zero** crashes across its
  18 wrong answers (all `exit 0`); the strong model simply does not emit an
  *observable* capability failure to retry.
- **crash + weak model** — qwen-turbo, 4 crashes triggered the governor retry but
  stayed FAILED (`retry_diff`: `Mbpp/97` patched the wrong line, `Mbpp/126`
  regenerated identical code; `EVALUATOR_FP_MEASUREMENT.md` §5).
- **crash + traceback carries the fix** — this is a **design premise, not a fresh
  measured recovery rate**: the `must_fail` bucket
  (`reforge/benchmark/targeted/selfheal_suite.jsonl`) is characterised as
  "traceback already carries its own fix" (`RELATED_WORK.md`), but its own
  `measured_first_try_fail_rate` is `null` (never run as a bucket). The **measured**
  anchor for "traceback-carried fix ⇒ even a memory-less retry recovers" is the
  Phase 0 **naive** arm: `t2_users_key_error` naive seed 0 `recovered=true`,
  `passed=true`, attempts=2 — naive reads only `exit_code`, carries no governor
  hint and no memory, yet self-repairs. That is exactly "mechanism adds nothing".
- **environment mismatch** — Phase 0: `t3` governor 3/3 seeds `recovered=true`;
  `t2` governor seed 2 recovered (seeds 0/1 `retries_exhausted`).

**The load-bearing line.** Only the last row leaves room for a recalled fix,
because the fix *direction* must come from information outside the model — and
only there is it both outside the error text and outside the model's own
knowledge. A `KeyError 'profit'` does not tell you the real column is
`gross_profit`; that is a fact about *this dataset*, not code knowledge. A
`SyntaxError`, by contrast, is fixed with code knowledge itself — the model
either can or cannot, and a hint adds nothing it did not already have.

**Same-model contrast (not cross-batch inference).** The strong-model zero-crash
row and the environment-mismatch recovery row are the **same model**,
deepseek-v4-pro (see §5 on the model-id provenance): the *same* model that on
MBPP single-function tasks produced 18/18 silent wrong answers with zero crashes
produced, on the Phase 0 data tasks, recoverable environment-mismatch failures
that actually recovered. Task form — not model strength — decides the failure
type; this now rests on same-model evidence, not a cross-corpus guess.

### Restated as the axis

The records support a distinction more fundamental than model strength:

- **Capability-type failure** — the model cannot write correct code. A strong
  model does not produce it as an *observable* failure (its errors are silent
  wrong answers the evaluator waves through); a weak model produces it as a
  crash but cannot repair it. **Both ends are closed, and memory has no room —
  memory cannot raise capability.**
- **Environment-mismatch failure** — the model is capable, but its default
  preferred path (`profit`, `users`, `sales_2024.csv`) disagrees with what the
  environment actually exposes (`gross_profit`, `tbl_users`, `sales-2024.csv`).
  The fix is a **fact about the environment**, un-derivable from the error text
  but discoverable by inspection. **This is exactly what memory can supply and
  reflection cannot** — a recalled fact vs a re-read of the same traceback.

**MBPP single-function generation produces almost only capability-type
failures.** A self-contained function has no external environment to mismatch:
it is either right, silently wrong (capability, evaluator-blind), or a crash
whose fix is code knowledge (capability, model-bound). This is why **tuning the
model along the strong↔weak axis kept hitting the wall** — strong models fail
silently (evaluator can't retry), weak models crash and can't self-repair;
neither failure is one a recalled environment-fact could resolve.

## 4. Sidelight — Reflexion's own effect gradient

Reflexion (Shinn et al., arXiv:2303.11366) is consistent with this axis, as an
external data point (not evidence for our system):

- Its **largest** effect is AlfWorld: "improve on decision-making AlfWorld tasks
  over strong baseline approaches by an **absolute 22%**." AlfWorld failures are
  path/order knowledge — the agent *can* execute the actions, it just picked the
  wrong order (environment-mismatch-shaped).
- Its **null** is WebShop: Reflexion is "unable to solve tasks that require a
  significant amount of **diversity and exploration**" (Appendix B.1). Where the
  bottleneck is generative capability/exploration rather than a recallable
  environment fact, the reflective/memory layer has no room — the same shape as
  MBPP here.

## 5. Honesty caveats

- **Model-id provenance (same model, but not from the record).** The Phase 0
  calibration report (`PHASE0_CALIBRATION.md`) does **not** store a model id in
  its records — the field is simply absent. The model is confirmed as
  **deepseek-v4-pro from the run configuration**, not from the record: the
  project's documented default for the text/data-task role is
  `LLM_MODEL=deepseek-v4-pro` (tracked `.env.example`; `project-model-selection`
  memory, "Text reasoning → deepseek-v4-pro for planner/eval/policy"), and the
  author confirms Phase 0 ran on it. This is the *same* model as the MBPP
  strong-model runs, so the §3 contrast is same-model / different-task — but the
  provenance is run-config-plus-author-confirmation, **not** "the record shows
  it." (The MBPP weak-model calibration additionally used qwen-turbo.)
- **Recovery code not persisted — sqlite_master stays inference.** We checked:
  the Phase 0 appendix records store outcomes (`recovered=true`) and a truncated
  `got=` stdout only — there is **no** `generated_code` field in any eval record
  (`grep generated_code docs/eval/*.md` → nothing). So we cannot read the cold/
  governor arm's repair to confirm it called `sqlite_master` / `df.columns` /
  `PRAGMA table_info`. "Recovered by inspecting the environment" is therefore
  **inferred** from the failure type (a `KeyError`/dtype mismatch resolves only
  by learning the real name, a data fact) — not observed. Stated as inference.
  (The one place a schema-probe call *was* read off generated code is the
  separate private-encoding pilot, `MEMORY_TRANSFER_PROBE_PILOT.md` §"Root
  cause" — a different, harder corpus, not these Phase 0 cases.)
- **Recovery is not universal even on the environment-mismatch side.**
  `t2_users_key_error` recovered on some seeds and hit `retries_exhausted` on
  others (governor seeds 0/1). The claim is "memory has *room* here", not
  "recovery is guaranteed".
