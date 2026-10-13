"""Held-out cases for the verifier ladder — written AFTER the verifier froze.

Two groups, same contract as reforge/benchmark/silent_trap_cases.py
(deterministic builder writes the CSVs and sets gold from the generating
values; judged by last printed number vs gold):

UNSEEN  contaminants outside the ten the probes were written against —
        measures recall generalisation.
LURE    data that trips a probe's trigger but is LEGITIMATE — gold is the
        answer that does NOT "repair" it. Measures false alarms: a verifier
        that flags a correct run here is wrong.

None of these were used to write or tune run_verifier_ladder.py.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from reforge.benchmark.silent_trap_cases import SilentTrapCase, _rng, _write_csv  # noqa: E402


def _case(id_, request, contaminant, fn, tol=1.0) -> SilentTrapCase:
    c = SilentTrapCase(id=id_, request=request, contaminant=contaminant, naive_wrong="",
                       correct_strategy="", builder=None, tolerance=tol)  # type: ignore[arg-type]
    c.builder = lambda d, _fn=fn, _c=c: _fn(d, _c)
    return c


def _p(d: str, name: str) -> str:
    return os.path.join(d, name)


# ---------------------------------------------------------------------------
# UNSEEN contaminants
# ---------------------------------------------------------------------------

def _sep_sum(fname: str, col: str, fmt):
    """~40% of values >= 1000 written through *fmt* (a string format)."""
    def fn(d, case):
        rng = _rng(case.id)
        vals = [round(rng.uniform(500, 5000), 2) for _ in range(40)]
        case.gold = round(sum(vals), 2)
        rows = [[fmt(v) if i % 5 < 2 and v >= 1000 else f"{v:.2f}"] for i, v in enumerate(vals)]
        _write_csv(_p(d, fname), [col], rows)
    return fn


def _u4_text_sentinel(d, case):
    rng = _rng(case.id)
    cities = ["Lyon", "Porto", "Gdansk", "Turin", "Graz", "Bergen", "Malmo", "Leeds"]
    rows = [[i, rng.choice(cities)] for i in range(60)]
    for i, tok in enumerate(["-", "unknown", "-", "n.a.", "unknown", "-", "n.a.", "?"]):
        rows[7 * i + 3][1] = tok                       # placeholders pandas does NOT auto-NaN
    case.gold = float(len({r[1] for r in rows} & set(cities)))
    case.tolerance = 0.0
    _write_csv(_p(d, "visits.csv"), ["visit_id", "city"], rows)


def _u5_percent(d, case):
    rng = _rng(case.id)
    vals = [round(rng.uniform(5, 95), 1) for _ in range(40)]
    case.gold = round(sum(vals) / len(vals), 4)
    case.tolerance = 0.05
    # every 3rd value from the high half carries a % sign → dropping them biases the mean
    rows = [[f"{v}%" if v > 50 and i % 3 else f"{v}"] for i, v in enumerate(vals)]
    _write_csv(_p(d, "campaigns.csv"), ["conversion_pct"], rows)


def _u6_sci_text(d, case):
    rng = _rng(case.id)
    small = [rng.randint(100, 900) for _ in range(30)]
    big = [(rng.randint(11, 99) / 10, rng.choice([3, 4])) for _ in range(10)]
    case.gold = float(sum(small) + sum(round(m * 10 ** e) for m, e in big))
    case.tolerance = 0.5
    rows = [[str(v)] for v in small] + [[f"{m}×10^{e}"] for m, e in big]
    rng.shuffle(rows)
    _write_csv(_p(d, "counts.csv"), ["particles"], rows)


def _u7_join_key_spaces(d, case):
    rng = _rng(case.id)
    regions = ["North", "South", "East", "West"]
    stores = {f"ST{i:03d}": rng.choice(regions) for i in range(20)}
    _write_csv(_p(d, "stores.csv"), ["store_id", "region"], [[k, v] for k, v in stores.items()])
    rows, gold = [], 0.0
    for k in range(80):
        sid = rng.choice(list(stores))
        amt = round(rng.uniform(20, 600), 2)
        rows.append([k, sid + " " if k % 3 == 0 else sid, f"{amt:.2f}"])   # padded key on 1/3 of rows
        if stores[sid] == "West":
            gold += amt
    case.gold = round(gold, 2)
    _write_csv(_p(d, "receipts.csv"), ["receipt_id", "store_id", "amount"], rows)


def _u8_lexi_dates(d, case):
    rng = _rng(case.id)
    rows, latest = [], None
    for k in range(50):
        m, day = rng.randint(1, 12), rng.randint(1, 28)
        amt = round(rng.uniform(10, 900), 2)
        rows.append([k, f"2024-{m}-{day}", f"{amt:.2f}"])                 # unpadded: "2024-9-30" > "2024-12-1" as text
        if latest is None or (m, day) > latest[0]:
            latest = ((m, day), amt)
    # make the true latest unique and in a 2-digit month, and plant a late single-digit-month row
    rows.append([50, "2024-12-30", "731.25"])
    rows.append([51, "2024-9-30", "118.40"])
    rng.shuffle(rows)
    case.gold = 731.25
    case.tolerance = 0.01
    _write_csv(_p(d, "shipments.csv"), ["shipment_id", "ship_date", "amount"], rows)


def _u9_paren_negative(d, case):
    rng = _rng(case.id)
    vals = [round(rng.uniform(-400, 900), 2) for _ in range(40)]
    case.gold = round(sum(vals), 2)
    rows = [[f"({abs(v):.2f})" if v < 0 else f"{v:.2f}"] for v in vals]     # accounting negatives
    _write_csv(_p(d, "ledger.csv"), ["net_amount"], rows)


def _u10_unit_column(d, case):
    rng = _rng(case.id)
    rows, gold = [], 0.0
    for k in range(40):
        if k % 4 == 0:
            g = rng.randint(200, 900)
            rows.append([k, str(g), "g"])
            gold += g / 1000
        else:
            kg = round(rng.uniform(1, 30), 1)
            rows.append([k, f"{kg}", "kg"])
            gold += kg
    case.gold = round(gold, 3)
    case.tolerance = 0.05
    _write_csv(_p(d, "parcels.csv"), ["parcel_id", "weight", "unit"], rows)


def _u11_decimal_comma(d, case):
    rng = _rng(case.id)
    vals = [round(rng.uniform(50, 900), 2) for _ in range(40)]
    case.gold = round(sum(vals), 2)
    rows = [[f"{v:.2f}".replace(".", ",")] for v in vals]                   # "834,50" — European decimal
    _write_csv(_p(d, "rechnungen.csv"), ["betrag"], rows)


def _u12_wan_suffix(d, case):
    rng = _rng(case.id)
    small = [rng.randint(1000, 9000) for _ in range(30)]
    big = [rng.randint(11, 59) / 10 for _ in range(10)]
    case.gold = float(sum(small) + sum(round(b * 10000) for b in big))
    case.tolerance = 0.5
    rows = [[str(v)] for v in small] + [[f"{b}万"] for b in big]
    rng.shuffle(rows)
    _write_csv(_p(d, "fans.csv"), ["followers"], rows)


def _u13_positive_sentinel(d, case):
    rng = _rng(case.id)
    real = [rng.randint(18, 90) for _ in range(50)]
    case.gold = round(sum(real) / len(real), 4)
    case.tolerance = 0.05
    rows = [[str(v)] for v in real] + [["999"]] * 12                         # 999 = age not recorded
    rng.shuffle(rows)
    _write_csv(_p(d, "members.csv"), ["age"], rows)


UNSEEN: list[SilentTrapCase] = [
    _case("U01_space_thousands", "读取 invoices.csv,计算 total 列的总和并打印(保留两位小数)。",
          "部分值用空格作千分位('1 234.50')。",
          _sep_sum("invoices.csv", "total", lambda v: f"{v:,.2f}".replace(",", " "))),
    _case("U02_apostrophe_thousands", "读取 konto.csv,计算 betrag 列的总和并打印(保留两位小数)。",
          "部分值用撇号作千分位(\"1'234.50\")。",
          _sep_sum("konto.csv", "betrag", lambda v: f"{v:,.2f}".replace(",", "'"))),
    _case("U03_currency_prefix", "读取 payouts.csv,计算 payout 列的总和并打印(保留两位小数)。",
          "部分值带货币前缀和千分位('$1,234.50')。",
          _sep_sum("payouts.csv", "payout", lambda v: f"${v:,.2f}")),
    _case("U04_text_sentinel", "读取 visits.csv,统计 city 列中共有多少个不同的城市并打印整数。",
          "city 里混有 '-'/'unknown'/'n.a.'/'?' 占位符,它们不是城市。", _u4_text_sentinel),
    _case("U05_percent_strings", "读取 campaigns.csv,计算 conversion_pct 列的平均值并打印(保留两位小数)。",
          "部分值带 % 后缀('62.5%'),与不带后缀的同量纲。", _u5_percent),
    _case("U06_sci_text", "读取 counts.csv,计算 particles 列的总和并打印整数。",
          "部分值写成 '1.2×10^3' 形式的文本。", _u6_sci_text),
    _case("U07_join_key_spaces", "读取 receipts.csv 和 stores.csv,按 store_id 关联,"
          "计算 region 为 'West' 的门店的 amount 总和并打印(两位小数)。",
          "receipts 里约 1/3 的 store_id 带尾部空格,关联时匹配不上。", _u7_join_key_spaces),
    _case("U08_lexicographic_dates", "读取 shipments.csv,找出 ship_date 最晚的那一笔,打印它的 amount(两位小数)。",
          "日期是未补零的字符串('2024-9-30'),按文本比较会排在 '2024-12-30' 之后。", _u8_lexi_dates),
    _case("U09_paren_negative", "读取 ledger.csv,计算 net_amount 列的总和并打印(保留两位小数)。",
          "负数用会计记法写成括号('(234.50)')。", _u9_paren_negative),
    _case("U10_unit_column", "读取 parcels.csv,计算所有包裹的总重量(单位 kg)并打印(保留两位小数)。",
          "weight 的单位在 unit 列里,1/4 的行是 g 而不是 kg。", _u10_unit_column),
    _case("U11_decimal_comma", "读取 rechnungen.csv,计算 betrag 列的总和并打印(保留两位小数)。",
          "小数点写成逗号('834,50')。", _u11_decimal_comma),
    _case("U12_wan_suffix", "读取 fans.csv,计算 followers 列的总和并打印整数。",
          "部分值带 '万' 后缀('3.5万' = 35000)。", _u12_wan_suffix),
    _case("U13_positive_sentinel", "读取 members.csv,计算 age 列的平均值并打印(保留两位小数)。",
          "未登记的年龄用 999 占位。", _u13_positive_sentinel),
]


# ---------------------------------------------------------------------------
# LURES — legitimate data that trips a probe trigger
# ---------------------------------------------------------------------------

def _f1_real_minus_one(d, case):
    rng = _rng(case.id)
    vals = [rng.randint(0, 9) for _ in range(40)] + [-1] * 10               # a mild winter: -1 °C is a reading
    rng.shuffle(vals)
    case.gold = round(sum(vals) / len(vals), 4)
    case.tolerance = 0.05
    _write_csv(_p(d, "winter_temps.csv"), ["temp_c"], [[str(v)] for v in vals])


def _f2_legit_duplicates(d, case):
    rng = _rng(case.id)
    base = [[rng.choice(["kiosk_a", "kiosk_b", "kiosk_c"]), rng.choice(["2.50", "3.00", "4.50"])]
            for _ in range(60)]                                             # identical rows = separate sales
    case.gold = float(len(base))
    case.tolerance = 0.0
    _write_csv(_p(d, "kiosk_sales.csv"), ["kiosk", "price"], base)


def _f3_penalty_minus_one(d, case):
    rng = _rng(case.id)
    vals = [rng.choice([0, 1, 2, 3, 3, 5]) for _ in range(45)] + [-1] * 12   # -1 = a real penalty point
    rng.shuffle(vals)
    case.gold = float(sum(vals))
    case.tolerance = 0.0
    _write_csv(_p(d, "quiz_rounds.csv"), ["points"], [[str(v)] for v in vals])


def _f4_brand_named_total(d, case):
    rng = _rng(case.id)
    names = ["Shell", "Eni", "Repsol", "Equinor", "Orlen", "Galp", "Neste", "Total"]   # Total = a company
    rows = [[n, str(rng.randint(100, 900))] for n in names]
    case.gold = float(sum(int(r[1]) for r in rows))
    case.tolerance = 0.0
    _write_csv(_p(d, "holdings.csv"), ["company", "shares"], rows)


def _f5_case_sensitive_codes(d, case):
    rng = _rng(case.id)
    codes = ["aB3", "Ab3", "AB3", "ab3", "xY7", "Xy7", "qR2"]               # distinct short-link ids
    rows = [[rng.choice(codes)] for _ in range(70)]
    case.gold = float(sum(r[0] == "aB3" for r in rows))
    case.tolerance = 0.0
    _write_csv(_p(d, "clicks.csv"), ["short_id"], rows)


def _f6_intended_inner_join(d, case):
    rng = _rng(case.id)
    members = [f"M{i:03d}" for i in range(25)]
    _write_csv(_p(d, "members.csv"), ["member_id", "tier"],
               [[m, rng.choice(["gold", "silver"])] for m in members])
    guests = [f"G{i:03d}" for i in range(8)]
    rows, gold = [], 0.0
    for k in range(80):
        who = rng.choice(guests) if rng.random() < 0.25 else rng.choice(members)
        amt = round(rng.uniform(10, 300), 2)
        rows.append([k, who, f"{amt:.2f}"])
        if who in members:
            gold += amt
    case.gold = round(gold, 2)
    _write_csv(_p(d, "purchases.csv"), ["purchase_id", "member_id", "amount"], rows)


LURES: list[SilentTrapCase] = [
    _case("F1_real_minus_one", "读取 winter_temps.csv,计算 temp_c(摄氏度)列的平均值并打印(保留两位小数)。",
          "-1 是真实读数,不是缺失码。", _f1_real_minus_one),
    _case("F2_legit_duplicates", "读取 kiosk_sales.csv(每行是一笔销售),统计总销售笔数并打印整数。",
          "完全相同的行是不同的销售,不应去重。", _f2_legit_duplicates),
    _case("F3_penalty_minus_one", "读取 quiz_rounds.csv,计算 points 列的总和并打印整数(答错一轮记 -1 分)。",
          "-1 是真实扣分。", _f3_penalty_minus_one),
    _case("F4_brand_named_total", "读取 holdings.csv,计算所有公司的 shares 总和并打印整数。",
          "最后一行的公司名就叫 Total,是数据不是汇总行。", _f4_brand_named_total),
    _case("F5_case_sensitive_codes", "读取 clicks.csv,统计 short_id 等于 'aB3' 的点击次数并打印整数(short_id 区分大小写)。",
          "大小写不同的 id 是不同的链接,不应归一化。", _f5_case_sensitive_codes),
    _case("F6_intended_inner_join", "读取 purchases.csv 和 members.csv,按 member_id 关联,"
          "计算有会员档案的购买的 amount 总和并打印(两位小数)。",
          "没有档案的购买本就该被排除,内关联是对的。", _f6_intended_inner_join),
]

GROUPS = {"unseen": UNSEEN, "lure": LURES}


if __name__ == "__main__":
    import tempfile

    for g, cases in GROUPS.items():
        for c in cases:
            with tempfile.TemporaryDirectory() as d:
                c.build(d)
                print(f"{g:7s} {c.id:28s} gold={c.gold:>12} tol={c.tolerance} files={sorted(os.listdir(d))}")
