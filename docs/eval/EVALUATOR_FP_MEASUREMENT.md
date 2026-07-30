# Evaluator false-positive measurement — HumanEval/MBPP gold oracles

> Executed 2026-07-30 (deepseek-v4-pro; weak-model calibration with
> qwen-turbo). Uses held-out unit tests as *gold* to quantify the runtime
> evaluator's false positives, falsifies "rescue it with an LLM judge", and
> pins the mechanism behind the governor-vs-naive null. Reproduce with
> `scripts/fp_sweep.py`, `scripts/llm_judge_probe.py`, `scripts/retry_diff.py`
> (harness in `reforge/benchmark/targeted/`). Commits `b0efa27`, `b032c18`.

## 1. Motivation

The BIRD ablation is null (`PHASE1_BIRD_ABLATION_R2/R3.md`): the governor's
retries buy no success-rate gain. The suspected cause is that the runtime
`HeuristicEvaluator` has no gold and waves through *silent* wrong answers
(code runs clean, output looks fine, the value is wrong), so it never fires a
retry on them. That was an argument, not a number. This experiment gets the
number by borrowing a gold oracle from standard code benchmarks.

## 2. Method

A dataset-agnostic `EvalTask` harness grades each attempt **twice, in
parallel**:

- **gold** — the benchmark's held-out tests. HumanEval runs `check(entry)`;
  MBPP runs its `test_list` asserts. The generated code is *imported* as a
  module (not run as `__main__`), so the demo-print block the prompt asks for
  never affects grading. This is the field of record for correctness.
- **evaluator** — the runtime `HeuristicEvaluator`'s own verdict
  (`SUCCESS`/`RECOVERED` = passed), the thing under test.

Crossing the two gives a confusion matrix per attempt. A **false positive** is
`eval_passed ∧ ¬gold` — a wrong answer the no-gold evaluator accepted. All runs
are the governor arm, one seed. MBPP hides the function name only in the tests;
the prompt gives the name but never the asserts, so a solution can look right
on the description yet fail a held-out case.

## 3. FP is ~18%, matching Reflexion

MBPP, 100 tasks, deepseek-v4-pro:

| | value |
|---|---|
| pass@1 (gold) | 82% |
| confusion (attempts) | TP 82 · **FP 18** · FN 1 · TN 1 |
| **FP rate** = P(gold-wrong \| eval-passed) | **18%** — every one of the 18 wrong answers accepted at full score |

Reflexion (Shinn et al., Table 2) reports the same quantity — "unit tests pass,
solution fails" — at **16.3%** on MBPP-Python and 1.4% on HumanEval-Python.
Our 18% is an independent reproduction at the same magnitude, with a *different*
evaluator (heuristic rules vs their self-generated tests) and model.

Every wrong answer was `exit 0` — it ran cleanly with well-formed output.
The evaluator's proxy signals (has output / no traceback / looks like data)
were all satisfied, so it could not tell the answer was wrong.

> HumanEval was too easy to press FP (pass@1 100%, FP 0). Its interesting
> artifact was on the **false-negative** side: function implementations print
> nothing, tripping `output_not_empty`; a bare boolean `True/False` also trips
> `research_output_quality` (`has_numbers=False`, not a length issue).
> Prompting for `input -> output` prints drops FN 60% → 0% — a workaround that
> sidesteps the rule, not a fix to the evaluator.

## 4. FP rate is model-independent

| model | pass@1 | FP rate |
|---|---|---|
| deepseek-v4-pro (strong) | 82% | 18.0% |
| qwen-turbo (weak) | 79% | 18.1% |

A strong and a weak model, different wrong answers, land on nearly the same FP
rate. The rate is a property of the **evaluator + task mix**, not model
strength: any model that emits silent wrong answers has them waved through at
the same proportion.

## 5. Two bottlenecks — why retries still don't help

Retries only fire on *observable* failure (`exit ≠ 0`). Running a weak model
(qwen-turbo, 100 tasks) split the 21 wrong answers cleanly:

| failure class | count | evaluator | bottleneck |
|---|---|---|---|
| silent wrong (`exit 0`) | 17 | no gold → can't tell → **no retry** → FP | evaluator (no gold) |
| crash (`exit ≠ 0`) | 4 | caught → **retry fires**, feedback includes traceback | model capability |

The 4 crashes triggered the governor retry, but the weak model could not
repair them — confirmed by replaying the code (`retry_diff.py`):

- **Mbpp/97** — root cause is line 1 `import from collections import defaultdict`
  (invalid syntax). On retry the model left that line and added a *correct*
  import inside the function body: it patched the wrong place, still
  `SyntaxError`.
- **Mbpp/126** — the function is named `sum` (shadowing the builtin) and calls
  `sum(divisors)` on itself → `TypeError`. On retry it regenerated **identical**
  code: it walked in place.

Note the crash feedback *carries a traceback* — information is not missing.
The failure to recover is model capability, not lack of gold. So the two
failure classes are blocked by two different walls: silent errors by the
no-gold evaluator, crashes by weak-model capability. A strong model almost
never crashes, so its failures fall almost entirely in the silent bucket.
**That is the full mechanism behind the governor null.**

## 6. Option B — an LLM judge does not rescue it

Can a smarter judge catch the silent-error FPs the rules miss? A probe replays
the 18 known-FP tasks and adds a third verdict: an LLM asked "is this correct?"
given only the request, the code, and its stdout — a *no-gold* judge (the
honest stand-in for wiring an LLM into the evaluator).

| judge | recall on FPs | wrongly killed (correct answers) | token cost |
|---|---|---|---|
| v1 (terse) | 8% (1/13) | 0 | baseline |
| v2 (chain-of-thought, task-agnostic wording) | 12% (2/16) | **100% (2/2)** | **+21%** |

Deeper prompting did not raise discrimination — it only moved the
recall/precision point (recall and false-kills rose together) and cost more
(judge completion ~40 → ~1030 tokens). The misses are structural:

- **Shared blind spot** — judge and coder are the same kind of model and read
  an ambiguous spec the same way. For "convert a tuple to a string" both take
  `str(tuple)`; the gold wants character-join. The judge endorses the wrong
  reading.
- **Missing information** — the correct convention (e.g. argument order) lives
  only in the held-out test, not in the judge's input. No prompt supplies it.

A no-gold judge catches "code contradicts itself / violates common sense"
(e.g. camelCase not lowercasing the first word) but not "code is self-consistent
yet disagrees with a convention that only the test encodes."

## 7. Relation to Reflexion

Reading `noahshinn/reflexion` (`programming_runs/`), the split is on two axes:

| axis | Reflexion (programming) | this evaluator |
|---|---|---|
| verdict | execute self-generated unit tests (grounded, but tests can be flaky) | heuristic rules on output shape |
| feedback | LLM reflection on a real assert failure (`assert f(1,2)==4 # got -1`) — points at the fix | rule template (e.g. "add quantitative output") — points at appearance |
| FP source | flaky self-tests pass a wrong solution | rules never check correctness at all |

Both land at 16–18% FP for the same reason (internal verdict ≠ gold), but the
runtime evaluator is a step weaker on *both* axes: verdict from output shape
rather than execution, feedback toward appearance rather than root cause. The
`research_output_quality` feedback ("needs quantitative output") is the clean
example — on Mbpp/91 the model complied by adding a `quantitative:` decoration
without touching its logic, and the evaluator's score went 0.67 → 1.00 (the
0.67 was reproduced exactly by replaying the evaluator on the real stdout).

## 8. Takeaways

1. The evaluator's false positive is **structural**: with no gold it can only
   use proxy signals (output present / has numbers / no traceback), and proxies
   diverge from correctness in **both** directions — silent wrong answers pass
   (FP), correct-but-unusual answers fail (FN on bare booleans / no-output
   functions).
2. **A smarter no-gold judge does not fix it** — deeper reasoning only trades
   recall against false-kills, at higher cost. The dividing line is an
   *executable* gold, not a cleverer arbiter or a better prompt.
3. Layering, if this is ever wired in: keep the deterministic/safety rules
   (`clean_exit`, traceback checks, `ast_capability_violation`, task contracts);
   the *appearance-proxy-for-correctness* rules (`research_output_quality`,
   `output_contains_data`) are the misfire source and are the retirement
   candidates.

This does not overturn the field-of-record for pass/fail (the SQL comparator /
held-out tests, KNOWN_LIMITATIONS L6). It quantifies why the *runtime* signal
that drives retries cannot, on its own, close the loop.

## 9. Reproduce

- Data: `scripts/fetch_humaneval.py`, `scripts/fetch_mbpp.py` →
  `benchmark_data/{HumanEval.jsonl, sanitized-mbpp.json}`.
- FP sweep: `python scripts/fp_sweep.py` (set `LLM_MODEL`; records are keyed by
  model). Confusion + gold-wrong list + FP cases.
- Judge probe: `python scripts/llm_judge_probe.py` (`JUDGE_MODE` v1/v2, token
  accounting).
- Retry code diff: `python scripts/retry_diff.py` (replays specific tasks,
  prints per-attempt code diff).
- Harness + oracles: `reforge/benchmark/targeted/{task,driver,humaneval_task,
  mbpp_task}.py`; tests in `reforge/tests/test_{humaneval,mbpp}_task.py`.
