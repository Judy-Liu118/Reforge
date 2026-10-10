"""Multi-step Silent-Trap cases for the Experience Memory Benchmark.

Companion to silent_trap_cases.py. Those were ~1-step single-file
aggregations. These are genuine 3–5 step pipelines (load → join →
filter/group → aggregate → report), where a silent defect in an
INTERMEDIATE step (almost always a join) propagates to a wrong final
number — with no exception raised.

The transferable habit memory should carry here is a multi-step one:

    "In a multi-step pipeline, verify the SHAPE of each intermediate
     result (row count after a join, number of groups after a groupby)
     before aggregating downstream — a silent join defect becomes a
     plausible wrong total, not a crash."

Same judging/COLD-WARM contract as silent_trap_cases.py:
  - Judge by printed numeric value vs `gold` + `tolerance` (NOT the
    heuristic evaluator, which sees exit 0 + a clean number and passes).
  - Each case has a deterministic `build(data_dir)` writing the CSVs and
    setting `self.gold`. Stable across processes (hashlib seed).
  - Cold: seed+transfer on fresh substrate (both likely wrong).
    Warm: seed runs first and seeds memory, then transfer.
"""

from __future__ import annotations

import csv
import hashlib
import os
import random
from dataclasses import dataclass
from typing import Callable


@dataclass
class SilentTrapCase:
    id: str
    request: str
    contaminant: str
    naive_wrong: str
    correct_strategy: str
    builder: Callable[[str], None]
    gold: float = 0.0
    tolerance: float = 1.0

    def build(self, data_dir: str) -> str:
        os.makedirs(data_dir, exist_ok=True)
        self.builder(data_dir)
        return data_dir


@dataclass(frozen=True)
class SilentPair:
    pair_id: str
    contaminant_axis: str
    seed: SilentTrapCase
    transfer: SilentTrapCase


def _write_csv(path: str, header: list[str], rows: list[list]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _rng(seed_str: str) -> random.Random:
    digest = hashlib.md5(seed_str.encode()).hexdigest()
    return random.Random(int(digest[:8], 16))


# ---------------------------------------------------------------------------
# M1 — join KEY DTYPE MISMATCH (int vs string) → empty join → total 0
#   Steps: load orders + customers → merge on customer_id → filter region →
#          sum amount. If keys are int on one side and str on the other,
#          the merge matches NOTHING, downstream sum silently = 0.
#   Habit: normalise join-key dtype before merging; sanity-check match count.
# ---------------------------------------------------------------------------

def _m1(orders_f: str, cust_f: str, target_region: str,
        key_col: str = "customer_id", id_prefix: str = "CUST-"):
    def _b(data_dir: str, case: "SilentTrapCase"):
        rng = _rng(case.id)
        regions = ["North", "South", "East", "West"]
        n_cust = 25
        cust_region = {i: rng.choice(regions) for i in range(1000, 1000 + n_cust)}
        # Both sides store customer_id as a STRING, but in DIFFERENT formats
        # ("CUST-1000" vs "C1000"). Both read back as object dtype, so a plain
        # merge does NOT raise — it just matches nothing and the join is empty
        # (silent). A bare-numeric string would be re-inferred as int and match
        # by accident; an int-vs-str merge would RAISE. Different string
        # formats are the only genuinely silent cross-source key mismatch.
        _write_csv(os.path.join(data_dir, cust_f), [key_col, "region"],
                   [[f"C{cid}", reg] for cid, reg in cust_region.items()])
        orders = []
        gold = 0.0
        for k in range(80):
            cid = rng.choice(list(cust_region))
            amt = round(rng.uniform(50, 800), 2)
            orders.append([k, f"{id_prefix}{cid}", f"{amt:.2f}"])   # different format
            if cust_region[cid] == target_region:
                gold += amt
        _write_csv(os.path.join(data_dir, orders_f),
                   ["order_id", key_col, "amount"], orders)
        case.gold = round(gold, 2)
    return _b


# ---------------------------------------------------------------------------
# M2 — MANY-TO-MANY FAN-OUT: dirty dimension table has duplicate keys →
#   join duplicates every order row → amount summed multiple times.
#   Steps: load orders + products → merge on product_id → filter category →
#          sum amount.
#   Habit: dedupe/aggregate the dimension table (or check cardinality)
#          before joining; a join should not inflate row count.
# ---------------------------------------------------------------------------

def _m2(orders_f: str, prod_f: str, target_cat: str,
        key_col: str = "product_id"):
    def _b(data_dir: str, case: "SilentTrapCase"):
        rng = _rng(case.id)
        cats = ["Electronics", "Home", "Toys"]
        prod_cat = {f"P{i:03d}": rng.choice(cats) for i in range(20)}
        # products.csv — SOME product rows duplicated verbatim (dirty dim table)
        prod_rows = [[pid, cat] for pid, cat in prod_cat.items()]
        for _ in range(6):
            pid = rng.choice(list(prod_cat))
            prod_rows.append([pid, prod_cat[pid]])            # exact dup key
        rng.shuffle(prod_rows)
        _write_csv(os.path.join(data_dir, prod_f), [key_col, "category"], prod_rows)
        # orders.csv — each order once
        orders = []
        gold = 0.0
        for k in range(70):
            pid = rng.choice(list(prod_cat))
            amt = round(rng.uniform(20, 500), 2)
            orders.append([k, pid, f"{amt:.2f}"])
            if prod_cat[pid] == target_cat:
                gold += amt
        _write_csv(os.path.join(data_dir, orders_f),
                   ["order_id", key_col, "amount"], orders)
        case.gold = round(gold, 2)
    return _b


# ---------------------------------------------------------------------------
# M3 — INNER JOIN SILENTLY DROPS UNMATCHED rows: some orders reference a
#   customer_id absent from customers.csv. Default pd.merge is inner, so
#   those orders vanish and the COUNT is undercounted.
#   Steps: load orders + customers → merge → count rows.
#   Task wants ALL orders retained. Habit: default merge is inner and drops
#   unmatched — use how='left' / verify no rows lost.
# ---------------------------------------------------------------------------

def _m3(orders_f: str, cust_f: str,
        key_col: str = "customer_id", fact_id_col: str = "order_id"):
    def _b(data_dir: str, case: "SilentTrapCase"):
        rng = _rng(case.id)
        known = [f"C{i:03d}" for i in range(30)]
        _write_csv(os.path.join(data_dir, cust_f), [key_col, "name"],
                   [[c, f"name_{c}"] for c in known])
        orphan = [f"X{i:03d}" for i in range(8)]              # not in customers
        orders = []
        total = 0
        for k in range(90):
            # ~20% of orders reference an orphan customer id
            cid = rng.choice(orphan) if rng.random() < 0.2 else rng.choice(known)
            orders.append([k, cid, f"{rng.uniform(10,200):.2f}"])
            total += 1
        _write_csv(os.path.join(data_dir, orders_f),
                   [fact_id_col, key_col, "amount"], orders)
        case.gold = float(total)      # every order must be counted
        case.tolerance = 0.0
    return _b


# ---------------------------------------------------------------------------
# M4 — GROUPBY key split by whitespace/case → wrong "top group" total.
#   Steps: load → groupby region → sum → pick max group → report its total.
#   The true top region is fragmented into variants, so its total is
#   understated and a different region looks like the max.
#   Habit: normalise group keys (strip+casefold) before grouping/ranking.
# ---------------------------------------------------------------------------

def _m4(sales_f: str, top_region: str, group_col: str = "region",
        value_col: str = "amount", others_names: tuple[str, ...] = ("South", "East", "West")):
    def _b(data_dir: str, case: "SilentTrapCase"):
        rng = _rng(case.id)
        variants = [top_region, top_region + " ", " " + top_region,
                    top_region.lower(), top_region.upper()]
        others = {name: 0.0 for name in others_names}
        rows = []
        top_total = 0.0
        # make top_region genuinely the largest once normalised
        for _ in range(50):
            amt = round(rng.uniform(100, 300), 2)
            rows.append([rng.choice(variants), f"{amt:.2f}"])
            top_total += amt
        for _ in range(60):
            r = rng.choice(list(others))
            amt = round(rng.uniform(20, 120), 2)
            rows.append([r, f"{amt:.2f}"])
            others[r] += amt
        rng.shuffle(rows)
        _write_csv(os.path.join(data_dir, sales_f), [group_col, value_col], rows)
        # gold = the top region's TRUE total after normalising its variants
        case.gold = round(top_total, 2)
    return _b


def _mk(id_, request, contaminant, naive_wrong, strategy, builder, tol=1.0):
    c = SilentTrapCase(id=id_, request=request, contaminant=contaminant,
                       naive_wrong=naive_wrong, correct_strategy=strategy,
                       builder=None, tolerance=tol)  # type: ignore
    c.builder = lambda d, _b=builder, _c=c: _b(d, _c)
    return c


MULTISTEP_PAIRS: list[SilentPair] = [
    SilentPair(
        "M1", "join key dtype mismatch → empty join",
        _mk("M1_seed_orders_north",
            "读取 orders.csv 和 customers.csv,按 customer_id 关联,"
            "计算 region 为 'North' 的客户的订单 amount 总和,打印结果(两位小数)。",
            "orders 的 id 形如 'CUST-1000',customers 的形如 'C1000' → 格式不同、关联匹配不上。",
            "merge(两边都是字符串)不报错,但匹配为空 → 下游求和静默得 0。",
            "两个来源的 join key 编码常不同;关联前抽取/归一化成同一形式(如取数字部分),并核对匹配行数不为 0。",
            _m1("orders.csv", "customers.csv", "North")),
        _mk("M1_transfer_sales_east",
            "读取 sales.csv 和 clients.csv,按 client_id 关联,"
            "计算 region 为 'East' 的客户的 sales amount 总和并打印(两位小数)。",
            "两边 client_id 的 key 格式不同(CUST- vs C,不同文件/列名)。",
            "同上:空关联 → 0。",
            "同一习惯:统一 key 格式,核对匹配行数。",
            _m1("sales.csv", "clients.csv", "East", key_col="client_id")),
    ),
    SilentPair(
        "M2", "many-to-many fan-out inflates total",
        _mk("M2_seed_electronics",
            "读取 orders.csv 和 products.csv,按 product_id 关联得到 category,"
            "计算 category 为 'Electronics' 的订单 amount 总和并打印(两位小数)。",
            "products.csv 有重复的 product_id 行(脏维表)→ 关联后订单行被复制。",
            "join 把订单行翻倍 → 金额被重复累加 → 偏大。",
            "关联维表前先按 key 去重(或聚合);join 不应让行数膨胀,关联后核对行数。",
            _m2("orders.csv", "products.csv", "Electronics")),
        _mk("M2_transfer_home",
            "读取 txns.csv 和 items.csv,按 item_id 关联得到 category,"
            "计算 category 为 'Home' 的交易 amount 总和并打印(两位小数)。",
            "items.csv 有重复 item_id 行(不同文件/列名)。",
            "同上:fan-out 使金额偏大。",
            "同一习惯:关联前去重维表,核对行数不膨胀。",
            _m2("txns.csv", "items.csv", "Home", key_col="item_id")),
    ),
    SilentPair(
        "M3", "inner join silently drops unmatched rows",
        _mk("M3_seed_order_count",
            "读取 orders.csv 和 customers.csv,按 customer_id 关联客户信息后,"
            "统计共有多少笔订单(每一笔订单都应保留)并打印整数。",
            "部分订单的 customer_id 在 customers.csv 中不存在(孤儿订单)。",
            "pd.merge 默认 inner join 把孤儿订单丢掉 → 订单数偏少。",
            "默认 merge 是 inner 会丢未匹配行;用 how='left' 或核对关联前后行数一致。",
            _m3("orders.csv", "customers.csv"), tol=0.0),
        _mk("M3_transfer_ticket_count",
            "读取 tickets.csv 和 agents.csv,按 agent_id 关联坐席信息后,"
            "统计共有多少张工单(每一张都应保留)并打印整数。",
            "部分工单的 agent_id 不在 agents.csv 中(不同文件/列名)。",
            "同上:inner join 丢未匹配 → 偏少。",
            "同一习惯:用 left join 或核对行数守恒。",
            _m3("tickets.csv", "agents.csv", key_col="agent_id", fact_id_col="ticket_id"),
            tol=0.0),
    ),
    SilentPair(
        "M4", "groupby key split → wrong top-group total",
        _mk("M4_seed_top_region",
            "读取 sales.csv,按 region 分组求 amount 总和,"
            "找出总额最高的地区,打印该地区的总额(两位小数)。",
            "最高地区 'North' 以 'North'/'north'/'NORTH ' 等变体出现,被拆成多组。",
            "分组键未规范化 → 'North' 被拆散 → 其总额被低估、甚至选出错误的最高地区。",
            "分组/排序前规范化 key(strip+casefold),再求每组和与最大值。",
            _m4("sales.csv", "North")),
        _mk("M4_transfer_top_dept",
            "读取 payroll.csv,按 dept 分组求 salary 总和,"
            "找出总额最高的部门,打印该部门的总额(两位小数)。",
            "最高部门以多种大小写/空格变体出现(不同文件/列名)。",
            "同上:组被拆散 → 最高组总额被低估或选错。",
            "同一习惯:分组前规范化 key。",
            _m4("payroll.csv", "Sales", group_col="dept", value_col="salary",
                others_names=("HR", "Ops", "IT"))),
    ),
]


def all_cases() -> list[SilentTrapCase]:
    out: list[SilentTrapCase] = []
    for p in MULTISTEP_PAIRS:
        out.append(p.seed)
        out.append(p.transfer)
    return out


if __name__ == "__main__":
    import tempfile
    for p in MULTISTEP_PAIRS:
        for c in (p.seed, p.transfer):
            with tempfile.TemporaryDirectory() as d:
                c.build(d)
                print(f"{c.id:26s} gold={c.gold:>11}  tol={c.tolerance}  files={sorted(os.listdir(d))}")
    print("\nOK — all multi-step cases generate deterministically and set gold.")
