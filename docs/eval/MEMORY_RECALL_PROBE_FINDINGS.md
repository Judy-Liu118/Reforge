# Memory-recall probe — corpus search log (2026-09-23 → 2026-10-09)

> **Instrument-gate log, not a result.** Nothing here is a headline number.
> Every run below is a pilot whose purpose was to decide whether a corpus can
> test the memory-transfer hypothesis at all. No pre-registered metric was
> computed, and none of this overturns the BIRD nulls
> (`PHASE1_BIRD_ABLATION*.md`) or the Experience-Memory null
> (`docs/experience_benchmark.md`).

Goal of the exercise: find a task family where cross-session repair memory
can demonstrably change an outcome — the gap left open by the Experience
bench (all three KPIs' CIs include zero) and by the memory-transfer probe
pilot (instrument-level false-positive stop).

Model: `deepseek-v4-pro`. Retry budget: `MAX_RETRY=3`. Every run isolates
memory on three scopes (`REFORGE_PROJECT_DIR`, `REFORGE_HOME`, substrate
factory), so "cold" means cold — the Experience driver isolates only the
first two, leaving `trajectories.jsonl` shared.

---

## 1. What a viable corpus has to satisfy

Derived from the two failures below plus `MEMORY_TRANSFER_PROBE_PREREG.md` §1.

| | Condition | Violated by |
|---|---|---|
| C1 | First failure is **loud** (non-zero exit) | comma-trap v1 (never failed); the TOTAL-footer error (never crashes) |
| C2 | The fix is **not derivable from the error text** | comma-trap v2 (`could not convert string to float: '1,234'` states its own fix) |
| C3 | The fix is a **strategy**, carrying no literal value | — (L8 hazard) |
| C4 | The fix is **reachable inside the budget** | private-encoding corpus (cold arm never got there) |
| C5 | The **masking path is closed** | original probe corpus (return 0 / warn-and-continue accepted as RECOVERED) |
| C6 | First encounter is **expensive** (needs exploration) | comma-trap v1 |

C1 and C2 pull against each other: the failure must be visible to the runtime
while its repair must not already be spelled out in the traceback. That
intersection is the whole measurable range for memory.

## 2. Corpus A — comma-trap (`reforge/benchmark/comma_trap_cases.py`)

20 tasks, one defect, twenty surfaces: sum one column of one CSV whose numeric
cells carry thousands separators.

**v1 (separators only).** Smoke, 2 tasks × 2 arms = 4 runs: **4/4 passed on
attempt 1**, zero failures, zero recalls. Violates C1/C6 — a corpus where the
trap never fires measures the instrument.

**v2 (+ 3 European-format rows, 2 parenthesised negatives, 1 decoy TOTAL
footer).** Smoke, 3 tasks × 2 arms = 6 runs: trap fires (first-attempt exit 1
on 3 of 6), **0/6 correct against gold**, and all six runs were reported
SUCCESS or RECOVERED by the runtime.

Both wrong answers reconstruct exactly, which is what makes the diagnosis a
measurement rather than a story:

| Run | Answer | Decomposition |
|---|---|---|
| CT03 attempt 2 | `557627.92` | gold `256971.39` + footer `300656.53` — formats all parsed correctly, footer summed in |
| CT02 attempt 2 | `505810.91193` | US rows `254666.19` + European rows read as decimals `206.90` − parenthesised rows `133224.37` + footer `384162.19` |

### 2.1 Memory poisoning, observed end to end

A third smoke (mem arm only, 3 tasks, isolation dirs kept) caught the full
chain in one sequence:

1. **CT03 attempt 1** crashes (`decimal.InvalidOperation`). Reflection's
   suggested fix: *"使用异常跳过无效行：`try: total += Decimal(cleaned)
   except InvalidOperation: continue`"* — a masking strategy.
2. **CT03 attempt 2** exits clean with `gold + footer`. Per
   `outcome_resolver.py` (`exit_code == 0 and retry_count > 0`) the run is
   **RECOVERED**; the runtime has no gold and cannot see the 300k error.
3. Because the outcome is RECOVERED, `repair_record_from_final_state` writes a
   repair record whose `repair_strategy` is **reflection's masking advice from
   attempt 1** — not what attempt 2 actually did (attempt 2 parsed every
   format correctly, which "skip invalid rows" cannot produce).
4. **CT02 attempt 1** crashes (`ValueError`). `ClassifyStage` recalls that
   record and injects it as `repair_hint`.
5. CT02 attempt 2 is wrong again and is again recorded RECOVERED.

Two qualifications, stated because the evidence supports only this much:

* **Observed inertness, not observed harm.** Neither run followed the hint —
  had they, the European rows would have been dropped entirely (contributing
  0) rather than mis-parsed as `206.90`. What is confirmed is that a false
  recovery wrote a bad strategy into cross-session memory and served it to a
  later task; n = 1 chain.
* **Recall admission is coarse.** CT02's failure is `ValueError`; the recalled
  record is `decimal.InvalidOperation` — different `error_class`,
  `root_cause` and `domain`. It qualified on `failure_mode` alone, and
  `failure_mode == "execution_error"` for essentially every crash, so that
  qualifier is near-always-true — the degeneracy `execution_memory.py`
  already documents for `domain`, but applied to a field still used for
  admission.

**Architectural asymmetry this exposes.** Reflection has no decision
authority (`FailureClassifier` deliberately does not consume it) yet it owns
what gets written to long-term memory: `repair_strategy` is its text
verbatim. An LLM note that cannot change a single decision can still poison
cross-session state. Candidate fix: write a repair only when the retry
demonstrably changed the outcome and the stored strategy matches the code
change — not on every RECOVERED.

**Verdict on corpus A:** violates C2 and C5. The format trap states its own
fix, and the footer error — the one that actually decides correctness — never
crashes, so it is structurally invisible to reflection, fingerprinting, memory
and the retry policy alike.

## 3. Corpus B — private encoding + output contract

`reforge/benchmark/memory_probe_contract.py` derives from the pre-registered
probe corpus (left byte-identical) by appending an output contract: print the
aggregate *and* the row count, and fail with a non-zero exit when the target
cannot be located or zero rows were aggregated. Verification moves into the
task contract — the one place a no-gold evaluator can be given teeth without
modifying the system under test.

Cold-arm gate, 3 seed cases:

| | pre-registered pilot | with contract |
|---|---|---|
| Loud first failure | 3/3 | 3/3 |
| Masked "success" accepted as RECOVERED | **3/3** | **0/3** (all FAILED) |
| Read the legend unaided | 0/3 | 0/3 |
| Gold solved | 0/3 | 0/3 |

**The contract works for what it was built for** — the masking path is closed
without touching the runtime. The corpus still fails C4: the cold arm never
reaches the legend, so no correct repair exists for a warm run to inherit.
**Verdict: NO-GO (floor).**

Why the budget was never spent: both attempts produced an *identical*
structural fingerprint, so the L3 history detector stopped the run at attempt
2 of 3.

```
policy_reason      = repeated_failure_signature
signature_history  = [KeyError/pandas/missing_key/"Column ",
                      KeyError/pandas/missing_key/"Column "]
```

This is **L3's first observed trigger in a real run** (R3 recorded zero
triggers across 200 runs), and its behaviour here is correct by design — the
second attempt repeated the first failure verbatim.

## 4. Defects found, reproduced locally without an LLM

1. **An early STOP is reported as a budget exhaustion.**
   `final_response.py::_determine_task_outcome` calls `resolve_outcome(...)`
   without `policy_reason`, so the `REPEATED_SIGNATURE_STOP` branch — whose
   condition is `policy_action == "STOP" and policy_reason ==
   "repeated_failure_signature"` — is unreachable from the only call site that
   writes the recorded outcome. Observed: `policy_reason =
   repeated_failure_signature` (correct) alongside `outcome_reason =
   retries_exhausted` (wrong). Cost: a replay cannot distinguish deliberate
   early stopping from burning the whole budget, which is the single property
   L3 exists to provide.
   **Not contaminating R3:** the Phase 1 driver records
   `control_state.policy_reason`, and no R3 record carries
   `repeated_failure_signature`, so "L3 zero-trigger" stands.

2. **`extract_error_type` returns the first warning in stderr.** It scans
   line by line for a token ending in `Error`/`Warning`/`Exception`; a pandas
   `UserWarning` printed before the traceback wins over the real exception.
   Observed `error_type = UserWarning` on a run whose exception was
   `KeyError`. Decisions are unaffected (classification reads exit codes), but
   the memory record's `error_type` and `find_by_error_type` recall are.

3. **`missing_key` degenerates to a constant on pandas errors.** pandas wraps
   the message (`KeyError: "Column 'revenue' not found"`), and the parser
   extracts `missing_key = "Column "`; a bare `KeyError: "revenue"` extracts
   `"revenue"` correctly. Consequence: every pandas column-not-found failure
   shares one fingerprint, which (a) amplifies L8 — unrelated column errors
   look like perfect structural matches to `recall_similar` — and (b) weakens
   L3, whose guarantee is "same error class **and** same target": two
   *different* missing columns in one session would now stop the run early.

## 5. Where this leaves the search

Fix (2) and (3) first — both are deterministic parse defects with reproduction
cases in hand, and every fingerprint-based conclusion rests on them. Then
either nudge corpus B toward discovery without revealing the legend (invite
inspection of what columns/tables exist, so attempt 2 stops repeating attempt
1 and L3 stops capping exploration), or move to the lower-difficulty
sentinel-value family (`-999` as a missing marker plus a contract assertion),
where the convention is unguessable but the repair is cheap.

Run records live under `runs/` (gitignored, regenerable):
`ct_smoke.jsonl`, `ct_smoke2.jsonl`, `ct_probe.jsonl`, `mpc_pilot*.jsonl`.
