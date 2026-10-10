"""Silent-Trap paired cases for the Experience Memory Benchmark.

WHY THIS EXISTS
---------------
The existing P1–P5 (`experience_cases.py`) are all *error-driven* traps:
the naive script raises an exception (KeyError / ModuleNotFoundError / ...)
whose traceback basically reveals the fix. A capable model reads the
traceback and self-heals on the first retry *without needing memory* —
which is exactly why the experience benchmark came back null on a strong
model.

Silent-Trap cases are different: the naive script **runs to completion and
prints a plausible-but-WRONG number**. There is no traceback to copy the
fix from. The only thing that rescues the second (surface-different) task
is a learned *habit* distilled into memory:

    "Before aggregating external data, inspect/normalise it —
     the failure here is a wrong number, not a crash."

That habit is transferable across every contaminant below, so it is the
signal we want memory to carry. This is the regime where ReasoningBank-style
distilled-reasoning memory beats error-recovery memory.

HOW CORRECTNESS IS JUDGED (driver note)
---------------------------------------
NOT by `expected_outcome` (there is no error to RECOVER from — outcome is
SUCCESS either way) and NOT by keyword gating. Judge by the printed numeric
value against `gold`, with `tolerance` (reuse the comma_trap gold-checker:
parse the last number on stdout, compare |value - gold| <= tolerance).

Each case carries a deterministic `build(data_dir) -> str` that writes the
contaminated CSV into a PRIVATE directory and sets `self.gold`. Gold is
computed from the clean generating values, so it is exact and independent
of how the model parses the file.

COLD vs WARM (same as experience_cases)
---------------------------------------
Cold: run seed and transfer on a fresh substrate (both likely WRONG).
Warm: let `seed` run first and populate memory, THEN run `transfer`.
The signal is: Warm-transfer prints the RIGHT number (habit transferred),
Cold-transfer prints the plausible-wrong one.
"""

from __future__ import annotations

import csv
import hashlib
import os
import random
from dataclasses import dataclass
from typing import Callable


# ---------------------------------------------------------------------------
# Case model
# ---------------------------------------------------------------------------

@dataclass
class SilentTrapCase:
    id: str
    request: str                      # natural-language task (what the agent sees)
    contaminant: str                  # human label of the silent defect
    naive_wrong: str                  # what the careless script prints (for docs)
    correct_strategy: str             # the transferable habit memory should carry
    builder: Callable[[str], None]    # writes CSV(s) into data_dir, sets gold
    gold: float = 0.0                 # set by builder — the correct answer
    tolerance: float = 1.0            # gold-check tolerance

    def build(self, data_dir: str) -> str:
        os.makedirs(data_dir, exist_ok=True)
        self.builder_dir = data_dir
        self.builder(data_dir)        # builder sets self.gold via closure
        return data_dir


@dataclass(frozen=True)
class SilentPair:
    pair_id: str
    contaminant_axis: str
    seed: SilentTrapCase
    transfer: SilentTrapCase


# ---------------------------------------------------------------------------
# Deterministic data helpers
# ---------------------------------------------------------------------------

def _write_csv(path: str, header: list[str], rows: list[list]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _rng(seed_str: str) -> random.Random:
    # Stable across processes — builtin hash() is salted per interpreter
    # (PYTHONHASHSEED), which would make gold/data differ between the Cold
    # and Warm runs. A benchmark needs the SAME contaminated file both legs.
    digest = hashlib.md5(seed_str.encode()).hexdigest()
    return random.Random(int(digest[:8], 16))


# Each builder below is a factory: it returns a closure that writes the file
# AND assigns gold onto the owning case. We bind the case lazily via a holder.

def _make(case_ref: dict, fn: Callable[[str, "SilentTrapCase"], None]):
    def _builder(data_dir: str) -> None:
        fn(data_dir, case_ref["case"])
    return _builder


# ---------------------------------------------------------------------------
# S1 — thousand-separators silently coerced to NaN and dropped
#      Naive: pd.to_numeric(col, errors="coerce").sum()  → comma rows → NaN → dropped
#      Fix habit: strip separators BEFORE converting; lose no rows.
# ---------------------------------------------------------------------------

def _s1(filename: str, value_col: str):
    ref: dict = {}
    def fn(data_dir: str, case: SilentTrapCase) -> None:
        rng = _rng(case.id)
        clean = [round(rng.uniform(500, 5000), 2) for _ in range(40)]
        case.gold = round(sum(clean), 2)
        rows = []
        for i, v in enumerate(clean):
            # ~40% of values written WITH a thousand-separator, as strings
            if i % 5 < 2 and v >= 1000:
                rows.append([f"{v:,.2f}"])          # "1,234.50"
            else:
                rows.append([f"{v:.2f}"])            # "834.00"
        _write_csv(os.path.join(data_dir, filename), [value_col], rows)
    return ref, fn


# ---------------------------------------------------------------------------
# S2 — trailing TOTAL/summary row silently included in the aggregate
#      Naive: df[col].sum()  → includes the footer → ~doubled
#      Fix habit: detect & exclude non-data summary rows before aggregating.
# ---------------------------------------------------------------------------

def _s2(filename: str, label_col: str, value_col: str):
    ref: dict = {}
    def fn(data_dir: str, case: SilentTrapCase) -> None:
        rng = _rng(case.id)
        labels = [f"item_{i:02d}" for i in range(30)]
        vals = [round(rng.uniform(10, 900), 2) for _ in range(30)]
        case.gold = round(sum(vals), 2)
        rows = [[lb, f"{v:.2f}"] for lb, v in zip(labels, vals)]
        rows.append(["TOTAL", f"{sum(vals):.2f}"])   # footer summary row
        _write_csv(os.path.join(data_dir, filename), [label_col, value_col], rows)
    return ref, fn


# ---------------------------------------------------------------------------
# S3 — whitespace / case variants split one group into several
#      Task asks "how many rows where region == 'North'".
#      Naive: df[df[col] == "North"] → misses "North ", " north", "NORTH"
#      Fix habit: normalise keys (strip + casefold) before comparing/grouping.
# ---------------------------------------------------------------------------

def _s3(filename: str, key_col: str, target_display: str):
    ref: dict = {}
    def fn(data_dir: str, case: SilentTrapCase) -> None:
        rng = _rng(case.id)
        variants = [target_display, target_display + " ", " " + target_display,
                    target_display.lower(), target_display.upper()]
        others = ["South", "East", "West"]
        rows = []
        north_count = 0
        for _ in range(60):
            if rng.random() < 0.5:
                rows.append([rng.choice(variants)])   # all are really "North"
                north_count += 1
            else:
                rows.append([rng.choice(others)])
        case.gold = float(north_count)
        case.tolerance = 0.0
        _write_csv(os.path.join(data_dir, filename), [key_col], rows)
    return ref, fn


# ---------------------------------------------------------------------------
# S4 — sentinel value (-1) meaning "missing" is treated as a real number
#      Naive: df[col].mean()  → averages the -1s in → biased low
#      Fix habit: check for sentinel/placeholder missing codes before stats.
# ---------------------------------------------------------------------------

def _s4(filename: str, value_col: str):
    ref: dict = {}
    def fn(data_dir: str, case: SilentTrapCase) -> None:
        rng = _rng(case.id)
        real = [rng.randint(18, 90) for _ in range(50)]
        case.gold = round(sum(real) / len(real), 4)
        case.tolerance = 0.05
        rows = [[str(v)] for v in real]
        # inject 15 sentinel "-1" rows meaning missing
        rows += [["-1"] for _ in range(15)]
        rng.shuffle(rows)
        _write_csv(os.path.join(data_dir, filename), [value_col], rows)
    return ref, fn


# ---------------------------------------------------------------------------
# S5 — exact-duplicate rows inflate a distinct count
#      Task asks "how many DISTINCT customers".
#      Naive: len(df)  → counts duplicates → inflated
#      Fix habit: dedupe on identity before counting distinct entities.
# ---------------------------------------------------------------------------

def _s5(filename: str, id_col: str, name_col: str):
    ref: dict = {}
    def fn(data_dir: str, case: SilentTrapCase) -> None:
        rng = _rng(case.id)
        n_unique = 35
        base = [[f"C{i:03d}", f"name_{i:03d}"] for i in range(n_unique)]
        case.gold = float(n_unique)
        case.tolerance = 0.0
        rows = list(base)
        # duplicate ~12 of them verbatim
        for _ in range(12):
            rows.append(list(rng.choice(base)))
        rng.shuffle(rows)
        _write_csv(os.path.join(data_dir, filename), [id_col, name_col], rows)
    return ref, fn


# ---------------------------------------------------------------------------
# S6 — mixed-unit column: some values are "1.2K" / "3M" strings
#      Naive: pd.to_numeric(col, errors="coerce").sum() → K/M rows dropped
#      Fix habit: normalise units/suffixes before aggregating.
# ---------------------------------------------------------------------------

def _s6(filename: str, value_col: str):
    ref: dict = {}
    def fn(data_dir: str, case: SilentTrapCase) -> None:
        rng = _rng(case.id)
        raw = [rng.randint(100, 900) for _ in range(30)]          # plain ints
        big = [rng.randint(1, 9) for _ in range(10)]              # become "xK"
        case.gold = float(sum(raw) + sum(v * 1000 for v in big))
        case.tolerance = 0.0
        rows = [[str(v)] for v in raw] + [[f"{v}K"] for v in big]
        rng.shuffle(rows)
        _write_csv(os.path.join(data_dir, filename), [value_col], rows)
    return ref, fn


# ---------------------------------------------------------------------------
# Assemble the pairs. Within a pair: same contaminant, different surface
# (different file / column / domain). Across pairs: different contaminant —
# so within-pair Warm transfer is the clean signal, and cross-pair is the
# harder generalisation probe (as P1 vs P5 did).
# ---------------------------------------------------------------------------

def _case(id_, request, contaminant, naive_wrong, strategy, ref_fn):
    ref, fn = ref_fn
    c = SilentTrapCase(
        id=id_, request=request, contaminant=contaminant,
        naive_wrong=naive_wrong, correct_strategy=strategy,
        builder=lambda d, _fn=fn, _ref=ref: _fn(d, _ref["case"]),
    )
    ref["case"] = c
    return c


SILENT_PAIRS: list[SilentPair] = [
    SilentPair(
        pair_id="S1", contaminant_axis="thousand-separator → coerced NaN dropped",
        seed=_case(
            "S1_seed_revenue_sum",
            "读取 data.csv,计算 revenue 列的总和,打印最终结果(保留两位小数)。",
            "部分 revenue 值带千分位逗号(如 '1,234.50'),以字符串存储。",
            "pd.to_numeric(errors='coerce') 把带逗号的值变 NaN 丢掉 → 和偏小。",
            "聚合数值列前,先检查是否为字符串/含分隔符;清洗分隔符再转数值,不丢行。",
            _s1("data.csv", "revenue")),
        transfer=_case(
            "S1_transfer_amount_sum",
            "读取 payments.csv,计算 amount 列的总和并打印(保留两位小数)。",
            "部分 amount 值带千分位逗号,字符串存储(不同文件/列名)。",
            "同上:coerce 丢行 → 和偏小。",
            "同一习惯:聚合前清洗分隔符再转数值。",
            _s1("payments.csv", "amount")),
    ),
    SilentPair(
        pair_id="S2", contaminant_axis="trailing TOTAL footer row included",
        seed=_case(
            "S2_seed_sales_sum",
            "读取 sales.csv,计算 value 列的总和并打印(保留两位小数)。",
            "最后一行是 label='TOTAL' 的汇总行,value 等于上面所有行之和。",
            "df['value'].sum() 把 TOTAL 行也加进去 → 结果约为真值两倍。",
            "聚合前检查并排除非数据的汇总/footer 行(如 label 为 TOTAL/合计)。",
            _s2("sales.csv", "label", "value")),
        transfer=_case(
            "S2_transfer_expense_sum",
            "读取 expenses.csv,计算 cost 列的总和并打印(保留两位小数)。",
            "最后一行是 category='TOTAL' 的汇总行(不同文件/列名)。",
            "同上:footer 被计入 → 约两倍。",
            "同一习惯:聚合前剔除汇总行。",
            _s2("expenses.csv", "category", "cost")),
    ),
    SilentPair(
        pair_id="S3", contaminant_axis="whitespace/case splits a group",
        seed=_case(
            "S3_seed_region_north",
            "读取 orders.csv,统计 region 等于 'North' 的订单数量并打印整数。",
            "'North' 以 'North'/'North '/' north'/'NORTH' 等多种变体出现,都应算作 North。",
            "df[df.region=='North'] 只匹配精确写法 → 漏掉变体 → 偏少。",
            "按 key 过滤/分组前先规范化(strip + casefold),再比较。",
            _s3("orders.csv", "region", "North")),
        transfer=_case(
            "S3_transfer_status_paid",
            "读取 invoices.csv,统计 status 等于 'Paid' 的发票数量并打印整数。",
            "'Paid' 以 'Paid'/'paid '/' PAID' 等变体出现(不同文件/列名)。",
            "同上:精确匹配漏掉变体 → 偏少。",
            "同一习惯:比较前规范化 key。",
            _s3("invoices.csv", "status", "Paid")),
    ),
    SilentPair(
        pair_id="S4", contaminant_axis="sentinel -1 treated as real value",
        seed=_case(
            "S4_seed_age_mean",
            "读取 users.csv,计算 age 列的平均值并打印(保留两位小数)。",
            "缺失年龄用 -1 占位(约 15 行),应作为缺失排除。",
            "df['age'].mean() 把 -1 一起平均 → 均值被拉低。",
            "统计前检查哨兵/占位缺失码(-1、9999、'N/A'),先排除再计算。",
            _s4("users.csv", "age")),
        transfer=_case(
            "S4_transfer_score_mean",
            "读取 exams.csv,计算 score 列的平均值并打印(保留两位小数)。",
            "缺考用 -1 占位(不同文件/列名),应排除。",
            "同上:-1 混入 → 均值偏低。",
            "同一习惯:先排除哨兵缺失码再求均值。",
            _s4("exams.csv", "score")),
    ),
    SilentPair(
        pair_id="S5", contaminant_axis="exact-duplicate rows inflate distinct count",
        seed=_case(
            "S5_seed_distinct_customers",
            "读取 customers.csv,统计不同客户的数量(去重)并打印整数。",
            "文件含约 12 行完全重复的记录,去重后才是真实客户数。",
            "len(df) 把重复也算 → 数量偏多。",
            "统计实体数量前,先按标识去重(drop_duplicates)。",
            _s5("customers.csv", "customer_id", "name")),
        transfer=_case(
            "S5_transfer_distinct_products",
            "读取 catalog.csv,统计不同商品的数量(去重)并打印整数。",
            "含完全重复的商品行(不同文件/列名),需去重。",
            "同上:重复行导致偏多。",
            "同一习惯:计数前先去重。",
            _s5("catalog.csv", "product_id", "title")),
    ),
    SilentPair(
        pair_id="S6", contaminant_axis="mixed-unit suffix (K) dropped",
        seed=_case(
            "S6_seed_views_sum",
            "读取 posts.csv,计算 views 列的总和并打印整数。",
            "部分 views 以 'K' 后缀的字符串存储(如 '3K' = 3000)。",
            "pd.to_numeric(errors='coerce') 把 '3K' 变 NaN 丢掉 → 和偏小。",
            "聚合前识别并归一化单位后缀(K/M),再求和。",
            _s6("posts.csv", "views")),
        transfer=_case(
            "S6_transfer_downloads_sum",
            "读取 apps.csv,计算 downloads 列的总和并打印整数。",
            "部分 downloads 带 'K' 后缀(不同文件/列名)。",
            "同上:带后缀的行被丢掉 → 偏小。",
            "同一习惯:归一化单位后缀再聚合。",
            _s6("apps.csv", "downloads")),
    ),
]


def all_cases() -> list[SilentTrapCase]:
    out: list[SilentTrapCase] = []
    for p in SILENT_PAIRS:
        out.append(p.seed)
        out.append(p.transfer)
    return out


# Smoke test: generate every case's data and verify gold is set & finite.
if __name__ == "__main__":
    import tempfile

    for p in SILENT_PAIRS:
        for c in (p.seed, p.transfer):
            with tempfile.TemporaryDirectory() as d:
                c.build(d)
                files = os.listdir(d)
                print(f"{c.id:30s} gold={c.gold:>12}  tol={c.tolerance}  files={files}")
    print("\nOK — all cases generate deterministically and set gold.")
