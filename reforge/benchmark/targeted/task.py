"""The three seams Phase 1 wrote directly against SQL, lifted to a protocol.

Phase 1's driver imported `load_bird_dev` (case supply), `build_sql_prompt`
(prompt), and `run_sql` + `compare_results` (oracle) at module top and
called them inline. Any other dataset — a self-constructed failure-bucket
suite, DS-1000, SWE-bench-lite — differs *only* in these three places.
`EvalTask` is that boundary; the driver and stats layers never see SQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class EvalCase:
    """One task instance, dataset-agnostic.

    `bucket` groups cases by *failure type* (e.g. "stale_api",
    "assertion", "must_fail_first"). Per-bucket recovery is the metric
    that actually isolates the governor's contribution — a whole-corpus
    success_rate is diluted to null by cases that solve on the first try
    (the Phase 1 lesson).

    `payload` carries whatever the concrete task needs — prompt text,
    expected stdout, a gold SQL + db path, a checker script. The driver
    never inspects it; only the task's own `build_prompt` / `grade` do.
    """

    case_id: str
    difficulty: str = "n/a"
    bucket: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class EvalTask(Protocol):
    """Seam contract. Implement these three; inherit the whole harness."""

    name: str

    def load_cases(self) -> list[EvalCase]:
        """Seam 1 — case supply. (Phase 1: load_bird_dev + PHASE1_CASE_IDS.)

        Order matters: cases sharing a `bucket` should be adjacent so a
        repeated-failure sequence lands inside one memory leg, letting the
        governor's repair_hint recall accrue across the sequence.
        """
        ...

    def build_prompt(self, case: EvalCase) -> str:
        """Seam 2 — prompt construction. (Phase 1: build_sql_prompt.)"""
        ...

    def grade(
        self,
        case: EvalCase,
        stdout: str,
        exit_code: int | None,
        generated_code: str = "",
    ) -> bool:
        """Seam 3 — deterministic gold oracle. (Phase 1: run_sql + compare_results.)

        Return True iff the attempt is correct for `case`. This is the
        field-of-record for pass/fail — it must be gold-based and
        deterministic, NOT the runtime's internal evaluator (which has no
        gold and is the thing under test). `exit_code` is None when grading
        the aggregated final answer, where no single process code applies.

        `generated_code` is the source the runtime produced for this attempt
        (empty for the final-answer grade if no state was captured). Oracles
        that judge *output* (stdout == expected, result-set equivalence)
        ignore it; oracles that judge the *program itself* (run a held-out
        unit-test suite against the generated function, HumanEval-style) need
        it. It is optional so the output-graded tasks stay unchanged.
        """
        ...
