"""SQL/BIRD as an EvalTask — proof the protocol wasn't shaped for one dataset.

If the three-seam abstraction is right, Phase 1's BIRD path must fall back
into it introducing no new concepts:
  - case supply  → load_bird_dev + the locked PHASE1_CASE_IDS;
  - prompt       → build_sql_prompt;
  - oracle       → *result-set equivalence* (run gold SQL, compare rows).

The oracle is where the generality claim gets tested. SelfHealTask grades by
exact stdout; SQL grades by whether the printed rows match the gold query's
rows and deliberately IGNORES exit_code. Two genuinely different oracle
shapes riding the same seam is the whole point — if `grade` could only
express "stdout == expected", the abstraction would be a SelfHeal-shaped
mould, not a real boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from reforge.benchmark.phase1.corpus import PHASE1_CASE_IDS
from reforge.benchmark.targeted.task import EvalCase
from reforge.runtime.sql.bird_loader import load_bird_dev
from reforge.runtime.sql.comparator import compare_results, run_sql
from reforge.runtime.sql.models import SqlCase
from reforge.runtime.sql.prompt import build_prompt as build_sql_prompt
from reforge.runtime.sql.prompt import parse_rows


class SqlEvalTask:
    """Adapts the locked Phase 1 BIRD corpus to the EvalTask protocol.

    Gold result sets are materialised once in load_cases — mirroring Phase 1's
    ``expected = {cid: run_sql(...)}`` — and cached for grade(), so grading a
    single attempt costs no extra DB round-trip.
    """

    name = "bird_sql"

    def __init__(
        self,
        *,
        bird_root: str | None = None,
        case_ids: Iterable[str] = PHASE1_CASE_IDS,
        loader: Callable[..., list[SqlCase]] = load_bird_dev,
    ) -> None:
        self._bird_root = bird_root
        self._case_ids = tuple(case_ids)
        self._loader = loader
        self._expected: dict[str, list[tuple]] = {}

    def load_cases(self) -> list[EvalCase]:
        kwargs: dict = {}
        if self._bird_root:
            kwargs["root"] = self._bird_root
        by_id = {c.case_id: c for c in self._loader(**kwargs)}
        missing = [cid for cid in self._case_ids if cid not in by_id]
        if missing:
            raise RuntimeError(
                f"SQL picks missing from loaded corpus: {missing}. "
                "Re-run scripts/prepare_bird.py and confirm dev_databases is intact."
            )
        cases: list[EvalCase] = []
        for cid in self._case_ids:
            sc = by_id[cid]
            self._expected[cid] = run_sql(sc.db_path, sc.gold_sql)
            cases.append(EvalCase(
                case_id=cid,
                difficulty=sc.difficulty,
                # SQL has no failure-type buckets; difficulty stands in so the
                # per-bucket view still stratifies something meaningful.
                bucket=sc.difficulty,
                payload={"sql_case": sc},
            ))
        return cases

    def build_prompt(self, case: EvalCase) -> str:
        return build_sql_prompt(case.payload["sql_case"])

    def grade(
        self, case: EvalCase, stdout: str, exit_code: int | None,
        generated_code: str = "",  # output-graded: source is not consulted
    ) -> bool:
        expected = self._expected.get(case.case_id)
        if expected is None:
            return False
        sc: SqlCase = case.payload["sql_case"]
        try:
            return compare_results(
                parse_rows(stdout), expected, order_sensitive=sc.expects_ordering
            )
        except Exception:  # noqa: BLE001 — mirror Phase 1's grade guard
            return False
