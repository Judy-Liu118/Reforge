"""Comma-trap corpus — a 20-task sequential memory-transfer bench.

Every task is the same shape: read one CSV, sum one numeric column, print the
number. Every task carries the same buried defect, in three layers:

1. **Thousands separators** (``"1,234.56"``) — pandas types the column as
   ``object``; a naive ``sum()`` concatenates strings and ``astype(float)``
   raises ``ValueError``.
2. **Mixed conventions** — a minority of rows use the European form
   (``"1.234,56"``) and some negatives are parenthesised (``"(2,300.00)"``).
   This is the layer that matters: stripping commas globally turns
   ``"1.234,56"`` into ``1.23456`` and the run finishes *clean with a wrong
   number*. The fix is not legible in any traceback, which is the precondition
   under which recalled memory can carry information a retry cannot rederive
   (`MEMORY_TRANSFER_PROBE_PREREG.md` §1).
3. **A TOTAL footer row** — the last row is a label-column ``TOTAL`` whose
   value is a decoy (it does not equal the correct sum of the data rows).
   Summing it in inflates the answer.

The first smoke run of the single-layer version (thousands separators only,
deepseek-v4-pro, 2 tasks × 2 arms) passed 4/4 on attempt 1 with no failure to
learn from — a corpus where the trap never fires measures the instrument, not
the hypothesis. Layers 2 and 3 exist because of that result.

Known ambiguity, disclosed rather than hidden: "sum the column" on a table
carrying a TOTAL footer has a defensible second reading (include it). The gold
excludes the footer — the reading an analyst would take — and the footer value
is set so that either mistake (footer included, or European rows mangled)
lands far outside the grading tolerance instead of near-missing it.

Design rules (what each one is defending against):

* **One fingerprint axis, twenty surfaces.** Domain, file name, column names
  and row values differ per task; the root cause never does. Memory can only
  help by transferring the *strategy* ("strip the separators before summing"),
  never a literal value — the L8 trap that makes a recalled hint actively
  wrong.
* **Private workspace per task.** ``benchmark_data/comma_trap/tNN/`` holds one
  CSV. The memory-probe pilot was polluted by a shared database file; a run
  here cannot read another task's data even by accident.
* **Program-graded gold.** The sum is computed here, from the same rows the
  CSV is generated from, so grading never asks a heuristic whether an answer
  looks plausible. `gold_value` is the oracle; `expected_keywords` stays empty
  on purpose — substring matching on a float is what produced the evaluator
  false negatives in `EVALUATOR_CALIBRATION.md`.

Data is written by ``scripts/build_comma_trap_data.py``; both it and the gold
value derive from :func:`task_rows`, so the CSV and the oracle can never drift
apart.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

from reforge.benchmark.models import BenchmarkCase

# A number in model output: optional sign, digits with optional thousands
# separators, optional decimals. Commas are stripped before comparison, so a
# correct answer printed as "391,204.53" is not scored wrong — formatting is
# not what this bench measures.
_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

# Absolute tolerance used by `grade_answer` (see its docstring).
GRADE_TOLERANCE = 1.0

# Directory (relative to the repo root / sandbox workspace) holding one
# subdirectory per task.
DATA_ROOT = "benchmark_data/comma_trap"

# Row count is fixed so a task's cost does not vary with its index.
ROWS_PER_TASK = 12


@dataclass(frozen=True)
class CommaTrapSpec:
    """One task: which file, which column, which domain flavour."""

    index: int
    domain: str
    file_stem: str
    amount_column: str
    label_column: str

    @property
    def task_id(self) -> str:
        return f"CT{self.index:02d}"

    @property
    def dir_name(self) -> str:
        return f"t{self.index:02d}"

    @property
    def csv_path(self) -> str:
        return f"{DATA_ROOT}/{self.dir_name}/{self.file_stem}.csv"


# Twenty distinct surfaces over one defect. Kept literal (not generated) so the
# corpus is reviewable as data and frozen by the pre-registration.
SPECS: tuple[CommaTrapSpec, ...] = (
    CommaTrapSpec(1, "零售", "retail_sales", "amount", "store"),
    CommaTrapSpec(2, "订单", "orders", "order_total", "customer"),
    CommaTrapSpec(3, "库存", "inventory", "stock_value", "sku"),
    CommaTrapSpec(4, "工资", "payroll", "gross_pay", "employee"),
    CommaTrapSpec(5, "物流", "shipments", "freight_cost", "carrier"),
    CommaTrapSpec(6, "广告", "ad_spend", "spend", "channel"),
    CommaTrapSpec(7, "报销", "expenses", "claim_amount", "department"),
    CommaTrapSpec(8, "捐赠", "donations", "donation", "donor"),
    CommaTrapSpec(9, "租金", "rentals", "monthly_rent", "property"),
    CommaTrapSpec(10, "能耗", "energy", "kwh_cost", "site"),
    CommaTrapSpec(11, "保险", "claims", "payout", "policy"),
    CommaTrapSpec(12, "票务", "ticketing", "ticket_revenue", "event"),
    CommaTrapSpec(13, "采购", "procurement", "po_value", "supplier"),
    CommaTrapSpec(14, "会员", "membership", "fee_paid", "member"),
    CommaTrapSpec(15, "维修", "maintenance", "repair_cost", "asset"),
    CommaTrapSpec(16, "培训", "training", "course_fee", "team"),
    CommaTrapSpec(17, "差旅", "travel", "trip_cost", "traveller"),
    CommaTrapSpec(18, "授权", "licensing", "license_fee", "product"),
    CommaTrapSpec(19, "退款", "refunds", "refund_amount", "channel"),
    CommaTrapSpec(20, "分红", "dividends", "payout_amount", "account"),
)


def _us(value: float) -> str:
    """1234.56 → "1,234.56" (comma thousands, dot decimal)."""
    return f"{value:,.2f}"


def _eu(value: float) -> str:
    """1234.56 → "1.234,56" (dot thousands, comma decimal)."""
    return f"{value:,.2f}".translate(str.maketrans({",": ".", ".": ","}))


def _paren_negative(value: float) -> str:
    """-1234.56 → "(1,234.56)" — accounting notation for a negative."""
    return f"({abs(value):,.2f})"


def task_rows(spec: CommaTrapSpec) -> list[tuple[str, str, float]]:
    """Deterministic (label, raw_cell, signed_value) rows for *spec*.

    `raw_cell` is what lands in the CSV; `signed_value` is what a correct sum
    adds. The last element is the TOTAL footer row and carries a value of 0.0:
    it contributes nothing to the gold, so including it is always an error.

    Seeded on the task index alone, so regenerating the data on another
    machine reproduces byte-identical CSVs and the same gold value.
    """
    rng = random.Random(20260923 + spec.index)
    rows: list[tuple[str, str, float]] = []

    for i in range(ROWS_PER_TASK):
        # Values are ≥ 1000 so every row carries a thousands separator — the
        # trap must fire on the first row read, not depend on which rows the
        # model happens to inspect.
        value = round(rng.uniform(1_000, 99_000), 2)
        label = f"{spec.label_column}_{i + 1:02d}"

        # Row styles are assigned by position, not by chance: every task gets
        # exactly the same mix (3 European, 2 parenthesised negatives, the
        # rest plain US), so difficulty cannot drift between tasks and a
        # per-task result is never explained by "this one had more EU rows".
        if i in (2, 6, 9):
            rows.append((label, _eu(value), value))
        elif i in (4, 8):
            rows.append((label, _paren_negative(value), -value))
        else:
            rows.append((label, _us(value), value))

    # Decoy footer: deliberately ~17% above the true sum of the data rows, so
    # neither "summed the footer in" nor "mangled the EU rows" can land inside
    # the grading tolerance by luck.
    true_sum = sum(value for _, _, value in rows)
    rows.append(("TOTAL", _us(round(true_sum * 1.17, 2)), 0.0))
    return rows


def gold_value(spec: CommaTrapSpec) -> float:
    """The correct column sum: data rows only, signs honoured, footer excluded."""
    return round(sum(value for _, _, value in task_rows(spec)), 2)


def format_amount(value: float) -> str:
    """Render a value the way a plain US-style cell is stored."""
    return _us(value)


def grade_answer(answer: str, gold: float) -> tuple[bool, list[float]]:
    """Program-graded oracle: True when *answer* carries a number equal to *gold*.

    Lives next to the generator, not in the driver, so the oracle and the data
    cannot drift apart. Tolerance is deliberately loose enough to absorb
    rounding and integer truncation ("391204" or "391204.5" for 391204.53): a
    run that rounded is a run that summed correctly, and failing it would
    rebuild the evaluator false negatives documented in
    `docs/eval/EVALUATOR_CALIBRATION.md`. Every wrong answer this corpus
    produces misses by thousands — a mangled European row, a summed-in footer,
    a dropped row — never by under 1.0.
    """
    values: list[float] = []
    for raw in _NUMBER_RE.findall(answer or ""):
        try:
            values.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return any(abs(v - gold) <= GRADE_TOLERANCE for v in values), values


def build_case(spec: CommaTrapSpec) -> BenchmarkCase:
    """Wrap *spec* as a BenchmarkCase.

    The request never mentions the separators: discovering them is the task.
    `expected_outcome` is "SUCCESS" only so the dataclass is well-formed —
    this bench grades on `gold_value`, not on the outcome label, because a
    task solved on attempt 1 reports SUCCESS and the same task solved on
    attempt 2 reports RECOVERED.
    """
    return BenchmarkCase(
        id=spec.task_id,
        category="comma_trap",
        difficulty="easy",
        request=(
            f"读取 {spec.csv_path}，计算 {spec.amount_column} 列的总和，"
            f"只打印这个数字，不要打印其他内容。"
        ),
        expected_outcome="SUCCESS",
        expected_keywords=[],
        description=(
            f"{spec.domain}场景；{spec.amount_column} 列是字符串：美式千分位为主，"
            f"混 3 行欧式 1.234,56、2 行括号负数，末行 TOTAL 为诱饵汇总。"
            f"gold={gold_value(spec)}"
        ),
    )


CASES: tuple[BenchmarkCase, ...] = tuple(build_case(s) for s in SPECS)
SPEC_BY_ID: dict[str, CommaTrapSpec] = {s.task_id: s for s in SPECS}
