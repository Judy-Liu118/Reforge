"""Phase 0 calibration corpus — locked.

The 5 BIRD picks + 3 recovery toys + 1 timeout decoy that the
PHASE0_CORPUS.md sign-off pinned. Both the BIRD case_ids and the toy
designs are immutable from this point; changes must also re-issue the
PHASE0_CORPUS / PHASE0_METRICS revision log entries.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Locked 5 BIRD picks. case_id format matches
# `reforge.runtime.sql.bird_loader.load_bird_dev` output
# ``bird_{question_id}_{db_id}``.
BIRD_CASE_IDS: tuple[str, ...] = (
    "bird_7_california_schools",
    "bird_1313_student_club",
    "bird_354_card_games",
    "bird_697_codebase_community",
    "bird_838_superhero",
)

# Recoverability gate: at least one of these must produce a
# first-try-failure-then-recover under governor mode in the calibration,
# else the "BIRD as recovery anchor" claim is unsubstantiated.
BIRD_RECOVERABILITY_GATE_IDS: frozenset[str] = frozenset({
    "bird_7_california_schools",
    "bird_1313_student_club",
})


_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@dataclass(frozen=True)
class ToyCase:
    """One non-SQL calibration case run through RuntimeRunner.

    A toy case writes ``fixture_files`` into a per-run workspace, hands
    ``prompt`` to the runtime, then either compares stdout against
    ``expected_stdout`` (recovery toys) or asserts the deliberate-STOP
    path was triggered (decoy).

    ``execution_timeout_s`` is an optional per-case override applied via
    a scoped ``EXECUTION_TIMEOUT`` env mutation. Used by D1″ to pin the
    sandbox watchdog to the documented 30 s assumption regardless of
    what the host's ``.env`` configures (PHASE0_CORPUS.md D1″ math
    presumes 30 s; ablation symmetry comes from applying the override
    to both governor and naive runs of that case).
    """

    case_id: str
    prompt: str
    fixture_files: tuple[str, ...]  # filenames under fixtures/ to copy into workspace
    expected_stdout: str | None     # exact stripped match; None means N/A (decoy)
    kind: str                        # "recovery_eval" / "recovery_exec" / "decoy_timeout"
    execution_timeout_s: int | None = None

    def fixture_paths(self) -> list[Path]:
        return [_FIXTURES_DIR / name for name in self.fixture_files]


# T1 — eval-driven recovery anchor (silent-wrong on first attempt:
# df['revenue'].sum() concatenates strings like '$1,234.56$2,000.00...').
# Expected recovery: comparator/eval rejects → next attempt strips
# '$' and ',' → casts to float → 6734.55.
T1_SALES = ToyCase(
    case_id="t1_sales_currency_coerce",
    prompt=(
        "Read sales.csv with pandas. Compute the total revenue across all rows "
        "and print exactly that total on a single line as a number with two "
        "decimal places (e.g. 1234.56). "
        "Do not print any warnings, diagnostic messages, or fallback values."
    ),
    fixture_files=("sales.csv",),
    expected_stdout="6734.55",
    kind="recovery_eval",
)

# T2 — execution-error recovery (KeyError on missing column 'lifetime_value';
# schema only has total_spend). Expected recovery: traceback inspection →
# next attempt maps lifetime_value → total_spend → prints "Eve".
T2_USERS = ToyCase(
    case_id="t2_users_key_error",
    prompt=(
        "Read users.csv with pandas, find the user with the highest lifetime_value, "
        "and print exactly that user's name on a single line. "
        "Do not print any warnings, diagnostic messages, or fallback values — "
        "if the data is missing, let the error surface."
    ),
    fixture_files=("users.csv",),
    expected_stdout="Eve",
    kind="recovery_exec",
)

# T3 — execution-error recovery (TypeError on string subtraction of dates).
# Expected recovery: next attempt adds pd.to_datetime → prints 3.4.
T3_ORDERS = ToyCase(
    case_id="t3_orders_dtype_recover",
    prompt=(
        "Read orders.csv with pandas and compute the average number of days "
        "between order_date and ship_date. Print exactly that average on a "
        "single line, rounded to one decimal place. "
        "Do not print any warnings, diagnostic messages, or fallback values — "
        "if a date column cannot be parsed, let the error surface."
    ),
    fixture_files=("orders.csv",),
    expected_stdout="3.4",
    kind="recovery_exec",
)

# D1" — timeout deliberate-STOP probe.
# Prompt deliberately chosen so IntentStage returns NORMAL_EXECUTION
# (avoids the STRESS_TEST → outcome_resolver intent override that would
# hijack policy_reason away from "timeout"). See PHASE0_CORPUS.md and
# KNOWN_LIMITATIONS L3.
D1_TIMEOUT = ToyCase(
    case_id="d1_timeout_sleep_decoy",
    prompt='Sleep for 120 seconds, then print "ok".',
    fixture_files=(),
    expected_stdout=None,
    kind="decoy_timeout",
    # Pin watchdog to the 30 s assumption baked into PHASE0_CORPUS.md so
    # the deliberate-STOP "timeout" path triggers deterministically
    # regardless of the host EXECUTION_TIMEOUT (e.g. the local .env that
    # raises it to 300 s for normal interactive use).
    execution_timeout_s=30,
)


TOY_CASES: tuple[ToyCase, ...] = (T1_SALES, T2_USERS, T3_ORDERS, D1_TIMEOUT)
