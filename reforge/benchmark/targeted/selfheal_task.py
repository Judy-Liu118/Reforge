"""A self-constructed EvalTask whose failures fall inside the governor's design域.

Every case fails (when it fails) in a way the runtime can *observe* — an
ImportError, an AssertionError, a raised exception — the opposite of BIRD's
silent semantic errors. Buckets each exercise a distinct governor mechanism
that BIRD's corpus never activated (repair_hint recall, typed-failure retry,
must-fail interception).

Cases live in `selfheal_suite.jsonl`, NOT hard-coded here — case content is
hand-designed (targeted failures have no off-the-shelf dataset) but the suite
is data, versioned separately, and every row must declare provenance:
`tests_mechanism` (what it probes) and `measured_first_try_fail_rate` (the
pilot-measured failure rate; null until calibrated). A case whose model
solves it on the first try triggers no failure and so measures nothing — the
null field is a standing reminder that this suite is not yet calibrated and
must not back a headline until it is. Row order is significant: cases sharing
a bucket stay adjacent so a repeated-failure sequence lands in one memory leg.

The oracle (`grade`) is gold-based and per-case: exact-string for discrete
answers, within-tolerance for numeric ones (declared via `numeric_tolerance`)
so round-off variants of an inexact quotient are not falsely rejected.
"""

from __future__ import annotations

import json
from pathlib import Path

from reforge.benchmark.targeted.task import EvalCase

_SUITE_PATH = Path(__file__).with_name("selfheal_suite.jsonl")

# Keys every row MUST carry (value may be null, but the key must be present),
# so adding a case forces a declaration of what it tests and whether its
# first-try failure rate has been pilot-calibrated.
_REQUIRED_PROVENANCE = ("tests_mechanism", "measured_first_try_fail_rate")
_REQUIRED_FIELDS = ("case_id", "bucket", "payload")


class SelfHealTask:
    """Observable-failure suite loaded from selfheal_suite.jsonl. `name` + 3 seams."""

    name = "selfheal_v0"

    def __init__(self, *, suite_path: Path | None = None) -> None:
        self._suite_path = suite_path or _SUITE_PATH

    def load_cases(self) -> list[EvalCase]:
        if not self._suite_path.exists():
            raise RuntimeError(f"selfheal suite not found at {self._suite_path}")
        cases: list[EvalCase] = []
        seen: set[str] = set()
        lines = self._suite_path.read_text(encoding="utf-8").splitlines()
        for lineno, raw in enumerate(lines, 1):
            line = raw.strip()
            if not line or line.startswith("//"):
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"{self._suite_path}:{lineno}: invalid JSON — {exc}"
                ) from exc
            self._validate_row(row, lineno)
            cid = row["case_id"]
            if cid in seen:
                raise RuntimeError(
                    f"{self._suite_path}:{lineno}: duplicate case_id {cid!r}"
                )
            seen.add(cid)
            cases.append(EvalCase(
                case_id=cid,
                difficulty=row.get("difficulty", "n/a"),
                bucket=row["bucket"],
                payload=row["payload"],
            ))
        if not cases:
            raise RuntimeError(f"selfheal suite {self._suite_path} has no cases")
        return cases

    def _validate_row(self, row: object, lineno: int) -> None:
        if not isinstance(row, dict):
            raise RuntimeError(f"{self._suite_path}:{lineno}: row must be a JSON object")
        for field in _REQUIRED_FIELDS:
            if field not in row:
                raise RuntimeError(
                    f"{self._suite_path}:{lineno}: missing required field {field!r}"
                )
        for field in _REQUIRED_PROVENANCE:
            if field not in row:
                raise RuntimeError(
                    f"{self._suite_path}:{lineno}: missing provenance field {field!r} "
                    "— declare what the case tests and its pilot-measured first-try "
                    "fail rate (null is allowed, but the key must be present)"
                )
        payload = row["payload"]
        if not isinstance(payload, dict) or "prompt" not in payload:
            raise RuntimeError(
                f"{self._suite_path}:{lineno}: payload must be an object with a 'prompt'"
            )

    def build_prompt(self, case: EvalCase) -> str:
        return case.payload["prompt"]

    def grade(
        self, case: EvalCase, stdout: str, exit_code: int | None,
        generated_code: str = "",  # output-graded: source is not consulted
    ) -> bool:
        # Observable failure is a hard fail — the whole point of this suite.
        if exit_code is not None and exit_code != 0:
            return False
        got = stdout.strip()
        expected = case.payload.get("expected_stdout")
        if expected is not None:
            tol = case.payload.get("numeric_tolerance")
            if tol is not None:
                # Numeric answer: compare within tolerance, so "8"/"8.0" and the
                # round-off variants of an inexact quotient ("6.6667" vs
                # "6.66667") all pass — exact string `==` would falsely reject
                # them. A declared tolerance meeting a non-numeric stdout is a
                # failure, not a crash.
                try:
                    return abs(float(got) - float(expected)) <= tol
                except ValueError:
                    return False
            return got == expected.strip()
        subs = case.payload.get("expect_substrings", [])
        return all(s in stdout for s in subs)
