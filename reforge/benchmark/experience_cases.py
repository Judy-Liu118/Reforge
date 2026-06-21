"""Paired BenchmarkCases for the Experience Memory Benchmark.

Each PairedCase exercises one FailureFingerprint axis with two surface
forms — `seed` (Round A) and `transfer` (Round A'). The Cold leg runs both
on a fresh substrate; the Warm leg lets `seed` populate the substrate
before `transfer` runs against it.

Pair design — five fingerprint axes, distinct enough that retrieval
ranking favours within-pair hits over cross-pair ones:

  P1  KeyError           + missing_key      (column not found, pandas)
  P2  ModuleNotFoundError+ missing_module   (import alias wrong)
  P3  FileNotFoundError  + missing_file     (file path with '_' vs '-')
  P4  OperationalError   + sqlite table     (table name wrong)
  P5  KeyError           + missing_key      (CASE mismatch — same axis as P1
                                            on purpose, as a within-axis
                                            generalisation probe)

`expected_outcome` is RECOVERED throughout: the runtime must self-heal.
The driver overrides BenchmarkRun.passed by treating SUCCESS as also
acceptable, since a Warm-A' first-try success is the very signal we want
to observe.
"""

from __future__ import annotations

from dataclasses import dataclass

from reforge.benchmark.models import BenchmarkCase


@dataclass(frozen=True)
class PairedCase:
    """Two BenchmarkCases sharing one FailureFingerprint axis."""

    pair_id: str            # e.g. "P1"
    fingerprint_axis: str   # human label, e.g. "KeyError + missing_key"
    seed: BenchmarkCase     # Round A — runs first under Warm, seeds memory
    transfer: BenchmarkCase  # Round A' — the transfer probe


PAIRED_CASES: list[PairedCase] = [
    # -------------------------------------------------------------------
    # P1 — KeyError, missing_key (column not found)
    # -------------------------------------------------------------------
    PairedCase(
        pair_id="P1",
        fingerprint_axis="KeyError + missing_key (pandas)",
        seed=BenchmarkCase(
            id="P1_seed_orders_profit",
            category="experience_memory",
            difficulty="medium",
            request=(
                "读取 benchmark_data/orders.csv,计算 profit 列的总和并打印结果。"
            ),
            expected_outcome="RECOVERED",
            expected_keywords=[],   # numeric; we don't gate on a specific sum
            description="profit column does not exist — actual column is gross_profit",
        ),
        transfer=BenchmarkCase(
            id="P1_transfer_customers_margin",
            category="experience_memory",
            difficulty="medium",
            request=(
                "读取 benchmark_data/customers.csv,计算 margin 列的平均值并打印。"
            ),
            expected_outcome="RECOVERED",
            expected_keywords=[],
            description="margin column does not exist — actual column is profit_margin",
        ),
    ),

    # -------------------------------------------------------------------
    # P2 — ModuleNotFoundError, missing_module (import alias wrong)
    # -------------------------------------------------------------------
    PairedCase(
        pair_id="P2",
        fingerprint_axis="ModuleNotFoundError + missing_module",
        seed=BenchmarkCase(
            id="P2_seed_import_pd",
            category="experience_memory",
            difficulty="medium",
            request=(
                "用 Python 读取 sales.csv,统计 revenue 列的平均值。"
                "在代码顶部请使用 `import pd` 这一行,然后用 pd.read_csv 读取。"
            ),
            expected_outcome="RECOVERED",
            expected_keywords=["7668"],
            description="`import pd` raises ModuleNotFoundError — recover via `import pandas as pd`",
        ),
        transfer=BenchmarkCase(
            id="P2_transfer_import_np",
            category="experience_memory",
            difficulty="medium",
            request=(
                "请计算列表 [10, 20, 30, 40, 50] 的算术平均值。"
                "代码顶部请使用 `import np` 这一行,然后调用 np.mean。"
            ),
            expected_outcome="RECOVERED",
            expected_keywords=["30"],
            description="`import np` raises ModuleNotFoundError — recover via `import numpy as np`",
        ),
    ),

    # -------------------------------------------------------------------
    # P3 — FileNotFoundError, missing_file (underscore vs hyphen)
    # -------------------------------------------------------------------
    PairedCase(
        pair_id="P3",
        fingerprint_axis="FileNotFoundError + missing_file",
        seed=BenchmarkCase(
            id="P3_seed_sales_2024",
            category="experience_memory",
            difficulty="medium",
            request=(
                "读取 benchmark_data/sales_2024.csv,统计 revenue 列的总和并打印。"
            ),
            expected_outcome="RECOVERED",
            expected_keywords=[],
            description=(
                "Actual file is sales-2024.csv (hyphen). Recovery requires "
                "listing benchmark_data/ to find the real filename."
            ),
        ),
        transfer=BenchmarkCase(
            id="P3_transfer_orders_2024",
            category="experience_memory",
            difficulty="medium",
            request=(
                "读取 benchmark_data/orders_2024.csv,统计 amount 列的总和并打印。"
            ),
            expected_outcome="RECOVERED",
            expected_keywords=[],
            description="Actual file is orders-2024.csv (hyphen)",
        ),
    ),

    # -------------------------------------------------------------------
    # P4 — OperationalError, sqlite table name
    # -------------------------------------------------------------------
    PairedCase(
        pair_id="P4",
        fingerprint_axis="OperationalError + sqlite table",
        seed=BenchmarkCase(
            id="P4_seed_sql_sales",
            category="experience_memory",
            difficulty="medium",
            request=(
                "使用 sqlite3 连接 benchmark_data/transactions.db,"
                "从 sales 表中统计 revenue 列的总和并打印结果。"
            ),
            expected_outcome="RECOVERED",
            expected_keywords=[],
            description=(
                "Actual table is tbl_sales. Recovery requires querying "
                "sqlite_master to discover the real table name."
            ),
        ),
        transfer=BenchmarkCase(
            id="P4_transfer_sql_users",
            category="experience_memory",
            difficulty="medium",
            request=(
                "使用 sqlite3 连接 benchmark_data/transactions.db,"
                "从 users 表中统计总行数并打印结果。"
            ),
            expected_outcome="RECOVERED",
            expected_keywords=[],
            description="Actual table is tbl_users",
        ),
    ),

    # -------------------------------------------------------------------
    # P5 — KeyError again, but on column CASE — within-axis generalisation
    # -------------------------------------------------------------------
    PairedCase(
        pair_id="P5",
        fingerprint_axis="KeyError + missing_key (case mismatch)",
        seed=BenchmarkCase(
            id="P5_seed_case_revenue",
            category="experience_memory",
            difficulty="medium",
            request=(
                "读取 sales.csv,统计 Revenue 列(注意首字母大写)的平均值并打印。"
            ),
            expected_outcome="RECOVERED",
            expected_keywords=["7668"],
            description="Actual column is lowercase revenue — KeyError 'Revenue'",
        ),
        transfer=BenchmarkCase(
            id="P5_transfer_case_amount",
            category="experience_memory",
            difficulty="medium",
            request=(
                "读取 benchmark_data/orders.csv,统计 Amount 列(注意首字母大写)的总和并打印。"
            ),
            expected_outcome="RECOVERED",
            expected_keywords=[],
            description="Actual column is lowercase amount — KeyError 'Amount'",
        ),
    ),
]


def pair_by_id(pair_id: str) -> PairedCase | None:
    for p in PAIRED_CASES:
        if p.pair_id == pair_id:
            return p
    return None


def all_cases() -> list[BenchmarkCase]:
    """Flatten paired cases into a single BenchmarkCase list."""
    out: list[BenchmarkCase] = []
    for p in PAIRED_CASES:
        out.append(p.seed)
        out.append(p.transfer)
    return out
