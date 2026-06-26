# Phase 0 calibration corpus — selection record

> **Status**: SIGNED OFF (v2 — post governor classify/policy audit).
> The 5 BIRD picks, T1/T2/T3, and the timeout-rebased D1″ are locked.
> Fixtures land in the same commit as the calibration harness; the
> calibration report cites the commit hash of this file.
>
> v2 changes vs v1: D1′ (FileNotFoundError) replaced by D1″ (timeout),
> after the governor audit found D1′ would NOT trigger deliberate-STOP
> (no history-pattern unrecoverability detector exists; only intent-
> classified expected-failure and TIMEOUT_EXIT_CODE reach deliberate
> STOP). Pre-commit cross-check then surfaced that D1″'s initial
> "Loop forever printing tick" prompt would be classified by IntentStage
> as STRESS_TEST and trigger the `outcome_resolver.py:50` intent
> override (`EXECUTION_TIMEOUT → (SUCCESS, "task_fidelity_achieved")`),
> hijacking `policy_reason` away from `"timeout"`. D1″'s prompt was
> therefore rebased to `Sleep for 120 seconds, then print "ok"`
> (NORMAL_EXECUTION-intent timeout) and the calibration gate's STOP
> assertion was switched to `state.classification_result.failure_mode
> == "timeout"` (intent-independent), with `policy_reason == "timeout"`
> as a defense-in-depth secondary check. The Phase-2 thesis is also
> narrowed — see `docs/eval/PHASE0_METRICS.md` v3 revision log and
> `docs/KNOWN_LIMITATIONS.md` L3.

## BIRD-simple correction (terminology fix)

`docs/eval/PHASE0_METRICS.md` and earlier discussion called the BIRD
slice "BIRD-easy". BIRD's actual difficulty labels are
`{simple, moderate, challenging}` — there is no `easy`. The 5 BIRD
picks below are all `difficulty == "simple"`. The PHASE0_METRICS doc
will get a follow-up edit to use the correct term in the same commit
that lands this corpus.

## BIRD `dev.zip` SHA256 (pinned)

```
cdd6d19faeb45a23970b98d3ef6c40a87987c95459c2cf12076897a60cf5a630
```

Downloaded from `https://bird-bench.oss-cn-beijing.aliyuncs.com/dev.zip`
via `python scripts/prepare_bird.py`. The eval chapter cites this hash
for corpus reproducibility. The script is idempotent — a fresh clone
that downloads the same dev.zip must yield this hash; if not, BIRD
has updated the corpus and the eval chapter must record both versions.

## Distribution audit

```
total:              1534 questions
by difficulty:      925 simple / 464 moderate / 145 challenging
simple - dialect:   873  (drop JULIANDAY/STRFTIME/DATETIME/IIF)
- evidence-heavy:   289  (drop "refers to ..." formula hints)
```

Final candidate pool: **289** simple cases free of SQLite-dialect
gotchas and free of strong evidence hints.

## The 5 picks

Selected to satisfy the locked criteria from `PHASE0_METRICS.md`:

- ≥2 require JOIN across ≥2 tables, with join keys on
  **differently-named** columns (`T1.foo = T2.bar`, not
  `T1.foo = T2.foo`) — i.e., the join is *recoverable* (a model that
  misses the FK on first try can fix it from traceback), not trivially
  inferrable from column name parity.
- ≥1 question is a single-table aggregate that should first-try-pass
  — the **reverse anchor**: validates the governor does NOT over-retry
  when it should ACCEPT.
- All 5 cases sit in **different** `db_id`s (so schema-cache,
  schema-injection, and the DDL-shape variety get exercised, not just
  one database's DDL).
- All `difficulty == "simple"`. All `evidence` either empty or a
  one-clause string-mapping hint (no formula like "X refers to A/B").

### Pick 1 — recoverable JOIN, no evidence (anchor of recoverability)

| Field | Value |
|---|---|
| `case_id` | `bird_7_california_schools` |
| `question_id` | 7 |
| `db_id` | `california_schools` |
| `difficulty` | simple |
| evidence | `""` (empty) |
| join shape | `satscores.cds = schools.CDSCode` (**different names**) |
| question | "What is the phone number of the school that has the highest number of test takers with an SAT score of at least 1500?" |
| gold SQL | `SELECT T2.Phone FROM satscores AS T1 INNER JOIN schools AS T2 ON T1.cds = T2.CDSCode ORDER BY T1.NumGE1500 DESC LIMIT 1` |

Why this one: empty evidence → zero scaffolding. The join key needs
schema inspection (`cds` ↔ `CDSCode`, lowercased FK vs PascalCase PK).
`ORDER BY ... DESC LIMIT 1` is a standard pattern that smaller text
LLMs sometimes get wrong on first try (forget LIMIT, wrong sort
direction). High likelihood of first-try failure → recovery via
traceback or eval-driven loop.

### Pick 2 — recoverable JOIN, no evidence, different db

| Field | Value |
|---|---|
| `case_id` | `bird_1313_student_club` |
| `question_id` | 1313 |
| `db_id` | `student_club` |
| `difficulty` | simple |
| evidence | `""` (empty) |
| join shape | `member.link_to_major = major.major_id` (**different names**) |
| question | "How many students in the Student_Club are from the College of Engineering?" |
| gold SQL | `SELECT COUNT(T1.member_id) FROM member AS T1 INNER JOIN major AS T2 ON T1.link_to_major = T2.major_id WHERE T2.college = 'College of Engineering'` |

Why this one: cross-db diversity (student_club is a different DDL
shape from california_schools — different vendor-style identifiers and
naming conventions). Empty evidence. The `link_to_major` ↔ `major_id`
FK has more conceptual distance than `cds ↔ CDSCode` — model has to
recognize "link_to_X" as the foreign-key naming pattern. JOIN +
COUNT(table-qualified col) is a standard recoverable shape.

### Pick 3 — first-try-pass anchor (single-table aggregate, no JOIN)

| Field | Value |
|---|---|
| `case_id` | `bird_354_card_games` |
| `question_id` | 354 |
| `db_id` | `card_games` |
| `difficulty` | simple |
| evidence | `"'Aaron Boyd' is artist;"` (column-name hint only, not a formula) |
| shape | single-table aggregate |
| question | "How many types of cards does the artist Aaron Boyd illustrated about card art?" |
| gold SQL | `SELECT COUNT(type) FROM cards WHERE artist = 'Aaron Boyd'` |

Why this one: pure single-table `COUNT(col) ... WHERE ...`. Clean
positive-control: any model that passes BIRD-simple at all should
first-try-pass this. **Reverse anchor**: if `mode=governor` retries
this case, the governor is over-firing on `eval_driven_recovery` and
the calibration is exposing a real bug. If `mode=naive` retries this,
something even worse (the codegen LLM is failing on a trivial query).

### Pick 4 — single-table filter, two-column projection, no evidence

| Field | Value |
|---|---|
| `case_id` | `bird_697_codebase_community` |
| `question_id` | 697 |
| `db_id` | `codebase_community` |
| `difficulty` | simple |
| evidence | `""` (empty) |
| shape | single-table filter, 2-column SELECT |
| question | "What is the reputation and view count of the user, who is known by his or her display name 'Jarrod Dixon'?" |
| gold SQL | `SELECT Reputation, Views FROM users WHERE DisplayName = 'Jarrod Dixon'` |

Why this one: fourth db (codebase_community has Stack-Overflow-style
schema — wholly different from school/club DBs). Multi-column
projection on a single table tests that the model returns the right
column ordering (Reputation first, Views second — comparator with
`order_sensitive=False` doesn't care about row order but DOES care
about column-order and identity). Likely first-try-pass.

### Pick 5 — single-table filter, single-column projection, no evidence

| Field | Value |
|---|---|
| `case_id` | `bird_838_superhero` |
| `question_id` | 838 |
| `db_id` | `superhero` |
| `difficulty` | simple |
| evidence | `""` (empty) |
| shape | single-table filter, 1-column SELECT |
| question | "Provide the full name of the superhero named Alien." |
| gold SQL | `SELECT full_name FROM superhero WHERE superhero_name = 'Alien'` |

Why this one: fifth db (superhero, comic-domain schema). Minimal
question, minimal SQL — the floor of "this should obviously pass".
If even *this* doesn't first-try-pass, the codegen LLM is unhealthy
and Phase 0 calibration is exposing a runtime bug we'd otherwise
attribute to the corpus.

## Coverage matrix

| Pick | db_id | needs JOIN? | join key shape | category | expected first-try outcome |
|---|---|---|---|---|---|
| 1 (qid 7) | california_schools | YES | diff-name (`cds ↔ CDSCode`) | recoverable JOIN | likely FAIL → recover |
| 2 (qid 1313) | student_club | YES | diff-name (`link_to_major ↔ major_id`) | recoverable JOIN | likely FAIL → recover |
| 3 (qid 354) | card_games | no | n/a | first-try-pass anchor | PASS |
| 4 (qid 697) | codebase_community | no | n/a | single-table filter | likely PASS |
| 5 (qid 838) | superhero | no | n/a | single-table filter | likely PASS |

5 distinct `db_id`s. 2 recoverable JOINs (per user-locked criterion).
1 first-try-pass anchor (per user-locked criterion). Plus 2 "should-
pass" backups so the cohort doesn't degenerate into "every BIRD case
fails" — which would make the comparator path untested for the SUCCESS
branch.

## Toys and decoy (committed designs, fixtures pending sign-off)

These are the calibration's recovery-path and STOP-path probes,
exercising mechanisms BIRD alone cannot.

### T1 — eval-driven recovery anchor (silent-wrong output)

- **prompt**: `Read sales.csv and print the total revenue, formatted as a single number.`
- **fixture**: `sales.csv`, three rows, `revenue` stored as
  `$1,234.56` / `$2,000.00` / `$3,499.99`
- **first-try expected failure mode**: `silent-wrong` — model writes
  `df['revenue'].sum()` and gets the string concatenation
  `"$1,234.56$2,000.00$3,499.99"` (no exception, comparator says wrong)
- **recovery path**: `eval_driven_recovery` — comparator rejects →
  governor RETRY → next attempt strips `$` and `,`, casts to float
- **gold stdout**: `6734.55`

### T2 — execution-error recovery (KeyError on missing column)

- **prompt**: `Read users.csv and print the name of the user with the highest lifetime_value.`
- **fixture**: `users.csv`, columns `id,name,total_spend` (no
  `lifetime_value`), 5 rows
- **first-try expected failure mode**: `execution_error` —
  `KeyError: 'lifetime_value'`
- **recovery path**: `execution_recovery` — traceback inspection →
  governor reads schema → next attempt maps `lifetime_value` →
  `total_spend`
- **gold stdout**: name of the row with max `total_spend` (concrete
  value fixed in the fixture at implementation time)

### T3 — execution-error recovery (TypeError on dtype mismatch)

- **prompt**: `Read orders.csv and print the average number of days between order_date and ship_date (one decimal).`
- **fixture**: `orders.csv`, columns `order_id,order_date,ship_date`,
  dates as strings `"2024-01-15"` etc., 5 rows
- **first-try expected failure mode**: `execution_error` — `TypeError`
  on string subtraction
- **recovery path**: `execution_recovery` — model adds
  `pd.to_datetime(...)` on second attempt
- **gold stdout**: a fixed float (e.g. `3.4`), value set in fixture
  to match the chosen 5 rows

### D1″ — deliberate-STOP path probe (timeout)

> Replaces D1′ (FileNotFoundError). Governor audit (`classifier.py:24-75`,
> `retry_policy.py:19-53`) showed D1′ would NOT trigger deliberate-STOP:
> the runtime has no history-based unrecoverability detector, so a normal
> read prompt that yields repeated `FileNotFoundError` enters
> `execution_error / retryable=True` and gets RETRY'd until
> `retry_count == max_retry` → `retry_limit_reached_with_error` STOP
> (budget-exhausted, NOT deliberate). Of the two deliberate-STOP
> sub-paths in `retry_policy.py:31-35`, only the `failure_mode ==
> "timeout"` branch is reachable from a normal-looking prompt. D1″ probes
> exactly that branch.

- **prompt**: `Sleep for 120 seconds, then print "ok".`
- **fixture**: none (no workspace prep required)
- **execution behavior**: model writes `time.sleep(120); print("ok")`;
  sandbox watchdog kills the process at `EXECUTION_TIMEOUT = 30s`
  (`reforge/config.py:19`); executor sets
  `exit_code = TIMEOUT_EXIT_CODE = -1`
  (`subprocess_backend.py:91`, `docker_backend.py:122`).
- **intent classification**: prompt deliberately chosen so IntentStage
  returns `NORMAL_EXECUTION` (the few-shot in `task_intent.py:42-43`
  matches STRESS_TEST on "infinite loop" / "fork bomb" patterns; an
  explicit bounded sleep does not match either, and falls back to
  NORMAL_EXECUTION via `task_intent.py:80`). The earlier draft prompt
  ("Loop forever printing tick") matched STRESS_TEST's few-shot and
  would have triggered the `outcome_resolver.py:50` intent override
  `EXECUTION_TIMEOUT → (SUCCESS, "task_fidelity_achieved")` —
  hijacking `policy_reason` away from `"timeout"` and reframing the
  STOP as success. That conflates two different features (deliberate
  STOP on unrecoverability vs intent-driven outcome reinterpretation);
  the sleep-prompt isolates the deliberate-STOP path cleanly.
- **classifier path**: `exit_code == TIMEOUT_EXIT_CODE` hits
  `classifier.py:36-40` (this check runs before any `task_intent`
  branch in the classifier, so it is intent-independent) →
  `failure_mode="timeout"`, `is_expected_failure=False`,
  `retryable=False`.
- **expected governor behavior**: PolicyStage hits `failure_mode ==
  "timeout"` (`retry_policy.py:34-35`) on attempt 1 → **deliberate
  STOP** with `retry_count == 0` (budget remaining). With
  NORMAL_EXECUTION intent and no `EXECUTION_TIMEOUT` override
  (`outcome_resolver.py:48-64`), `resolve_outcome` returns the default
  `(FAILED, "timeout")`, so `state.control_state.policy_reason ==
  "timeout"` and `state.classification_result.failure_mode ==
  "timeout"`.
- **expected naive behavior**: `_naive_resolution`
  (`retry_decision.py:43-67`) does not consume `failure_mode` and only
  sees `exit_code != 0` (`-1 != 0` is truthy) → RETRY → another ~30s
  watchdog kill → RETRY → `retry_count >= config.max_retry` →
  **budget-exhausted STOP** with `policy_reason == "naive: budget
  exhausted"`. With `config.max_retry = 3` (default,
  `reforge/config.py:18`), naive runs `initial + 3 retries = 4
  attempts × ~30s ≈ 120s wall-clock` plus four codegen LLM turns vs
  governor's `1 attempt × ~30s ≈ 30s` plus one codegen turn. The
  delta is 3× wall-clock + 3× codegen-turn tokens, not a cosmetic
  reason-field difference.
- **what this probes**: the **timeout** deliberate-STOP code path
  only. The **terminal_intentional** sub-path (`is_expected_failure=True
  AND retryable=False`) is NOT exercised here, because triggering it
  requires the IntentStage LLM to classify the prompt as
  `EXPECTED_ERROR` / `TRACEBACK_DEMO` — a prompt explicit enough to do
  that would leak intent into the calibration corpus. That sub-path's
  evaluation is structurally out of Phase 0 scope and is documented in
  `docs/KNOWN_LIMITATIONS.md` L3.

### Coverage note — what D1″ does and does not buy

Phase 2's earlier plan included multi-class decoys (`.invalid`,
logically unsatisfiable, missing env dep, self-contradictory) intended
to measure governor's deliberate-STOP precision and recall as a
*general* unrecoverability recognizer. The audit found this capability
does not exist in the current runtime — non-timeout decoys all collapse
to `execution_error → RETRY → budget exhaustion`. Tier B's
deliberate-STOP precision / recall / calibration metrics are
consequently marked **deferred (out of current governor scope)** in
`PHASE0_METRICS.md` v3, and the decoy-diversity constraint is dropped.
Phase 2's headline contracts to recovery + memory ablation + the narrow
timeout-deliberate-STOP efficiency point; the gap is documented as
KNOWN_LIMITATIONS L3 rather than masked.

## Calibration go / no-go — three triggered paths

The calibration is **go** iff all three recovery / decision paths are
actually triggered across the 9 runs:

| Path | Probed by | Trigger evidence |
|---|---|---|
| `execution_recovery` | T2, T3 (also possible in BIRD picks 1/2) | ≥1 governor-mode run with `attempts > 1 AND passed=True AND first-attempt traceback recorded` |
| `eval_driven_recovery` | T1 (also possible in BIRD pick 1) | ≥1 governor-mode run with `attempts > 1 AND passed=True AND first attempt had comparator/eval rejection (not traceback)` |
| `deliberate_STOP (timeout)` | D1″ | ≥1 governor-mode run on D1″ with `action == "STOP" AND retry_count < config.max_retry AND state.classification_result.failure_mode == "timeout"`. The `failure_mode == "timeout"` field is intent-independent (set in `classifier.py:36-40` before any task_intent branch), so the gate stays robust even if IntentStage drifts. As a defense-in-depth secondary check, `state.control_state.policy_reason == "timeout"` should also hold (the D1″ prompt is chosen so NORMAL_EXECUTION intent skips the STRESS_TEST `EXECUTION_TIMEOUT` override in `outcome_resolver.py:48-51`). The terminal_intentional sub-path is out of calibration scope (see D1″ above and KNOWN_LIMITATIONS L3). |

If any of the three paths shows **0 occurrences across the calibration
corpus**, calibration is **NO-GO** — the gate is fake-green and we
fix the underlying mechanism before Phase 1 runs.

(Result-direction checks like "bypass solve rate ≤ governor solve
rate" remain forbidden as gates — they are the experiment's
conclusion, not the instrument's property. See `PHASE0_METRICS.md`.)

## Sign-off (v2)

Signed off after the governor classify/policy audit (read on
2026-06-26):

- **5 BIRD picks**: accepted.
- **T1 / T2 / T3**: accepted. T1 = eval-driven recovery anchor;
  T2/T3 = execution-error recovery.
- **D1″** (timeout-rebased): accepted. Probes the
  `failure_mode == "timeout"` deliberate-STOP sub-path. D1′
  (FileNotFoundError) discarded per audit.
- **Three-path go/no-go**: `execution_recovery` (T2/T3),
  `eval_driven_recovery` (T1), `deliberate_STOP (timeout)` (D1″).
  Any path = 0 occurrences across the calibration corpus → NO-GO.
- **BIRD #1 / #2 recoverability gate (kept)**: at least one of
  `bird_7_california_schools` / `bird_1313_student_club` must produce
  a `first_try=False AND passed=True` run under governor mode in the
  calibration. If neither does, the "BIRD as recovery anchor" claim
  is unsubstantiated and Phase 1 needs a corpus revisit before any
  headline number ships.

Implementation now lands:

- `docs/eval/PHASE0_CORPUS.md` (this file) — locked record of the
  picks + designs.
- `docs/eval/PHASE0_METRICS.md` v3 — Tier B (false-stop, deliberate-
  STOP precision/recall, calibration) marked deferred; decoy-diversity
  constraint dropped; "BIRD-easy" → "BIRD-simple"; calibration STOP
  check rescoped to `policy_reason == "timeout"` on D1″.
- `docs/KNOWN_LIMITATIONS.md` L3 — STOP scope documented as
  intent-driven + timeout-driven, no history-pattern unrecoverability
  detector; pattern-based detector considered and deferred (precision
  rationale recorded).
- Toy + decoy fixtures + a tiny Phase-0 driver that wraps
  `SqlBenchSession` (for BIRD picks) and a small toy-runner (for
  T1/T2/T3/D1″) inside `token_accounting(case_id, seed_idx)`, then
  emits the calibration report.
- Calibration run with `N_seeds = 3` (Phase-0 calibration uses the
  secondary-axis budget — it's instrument verification, not the
  headline experiment).
