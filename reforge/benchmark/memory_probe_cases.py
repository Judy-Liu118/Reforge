"""Memory-transfer probe corpus — private-encoding PairedCases.

Constructed AFTER docs/eval/MEMORY_TRANSFER_PROBE_PREREG.md was committed,
under its §3 rules. Within each pair, seed A and transfer A' share the SAME
schema and the SAME root cause — a private encoding resolvable only via a
legend in the same dataset — so the recalled repair *strategy* ("consult the
legend") transfers while no literal value does (L8-safe: strategy, not value).

The three axes carry distinct fingerprints (KeyError / OperationalError /
FileNotFoundError) so cross-pair recall does not mask within-pair transfer.
Prompts never mention the legend: a cold run must discover it, a warm run may
recall the strategy. Difficulty is "hard" (load → discover encoding → consult
legend → aggregate). Data: scripts/build_memory_probe_data.py.

`LEGEND_MARKERS` names the legend artifact per axis, used by the pilot's
diagnostic to detect whether a cold run recovered by *autonomous
introspection* (its generated code read the legend with no external hint).
"""

from __future__ import annotations

from reforge.benchmark.experience_cases import PairedCase
from reforge.benchmark.models import BenchmarkCase

# Per-pair legend artifact substring — a generated-code hit on this means the
# run consulted the legend on its own. Keyed by pair_id.
LEGEND_MARKERS: dict[str, str] = {
    "MP1": "mp_metrics_legend",
    "MP2": "_legend",
    "MP3": "mp_manifest",
}


PAIRED_CASES_MP: list[PairedCase] = [
    # -- MP1: encoded columns, KeyError --------------------------------------
    PairedCase(
        pair_id="MP1",
        fingerprint_axis="KeyError + encoded column (legend-resolvable)",
        seed=BenchmarkCase(
            id="MP1_seed_revenue",
            category="memory_probe",
            difficulty="hard",
            request=(
                "读取 benchmark_data/mp_metrics.csv，计算 revenue 列的总和，"
                "只打印这个数字。"
            ),
            expected_outcome="RECOVERED",
            expected_keywords=["750"],
            description="revenue 实为编码列 c_0001；修复需读 mp_metrics_legend.csv 映射。",
        ),
        transfer=BenchmarkCase(
            id="MP1_transfer_cost",
            category="memory_probe",
            difficulty="hard",
            request=(
                "读取 benchmark_data/mp_metrics.csv，计算 cost 列的总和，"
                "只打印这个数字。"
            ),
            expected_outcome="RECOVERED",
            expected_keywords=["188"],
            description="同一 schema、同一 legend：cost 实为 c_0002；同根因，策略可迁移。",
        ),
    ),

    # -- MP2: encoded sqlite tables, OperationalError ------------------------
    PairedCase(
        pair_id="MP2",
        fingerprint_axis="OperationalError + encoded table (legend-resolvable)",
        seed=BenchmarkCase(
            id="MP2_seed_inventory",
            category="memory_probe",
            difficulty="hard",
            request=(
                "连接 benchmark_data/mp_warehouse.db，计算 inventory 表中 qty 列"
                "的总和，只打印这个数字。"
            ),
            expected_outcome="RECOVERED",
            expected_keywords=["660"],
            description="inventory 实为编码表 t_a1；修复需查 _legend 表映射。",
        ),
        transfer=BenchmarkCase(
            id="MP2_transfer_shipments",
            category="memory_probe",
            difficulty="hard",
            request=(
                "连接 benchmark_data/mp_warehouse.db，计算 shipments 表中 qty 列"
                "的总和，只打印这个数字。"
            ),
            expected_outcome="RECOVERED",
            expected_keywords=["1500"],
            description="同一 db、同一 _legend：shipments 实为 t_a2；同根因，策略可迁移。",
        ),
    ),

    # -- MP3: encoded filenames, FileNotFoundError ---------------------------
    PairedCase(
        pair_id="MP3",
        fingerprint_axis="FileNotFoundError + encoded filename (legend-resolvable)",
        seed=BenchmarkCase(
            id="MP3_seed_sales",
            category="memory_probe",
            difficulty="hard",
            request=(
                "读取 benchmark_data/ 下名为 sales 的数据集（CSV 文件），"
                "计算 amount 列的总和，只打印这个数字。"
            ),
            expected_outcome="RECOVERED",
            expected_keywords=["650"],
            description="sales 实为编码文件 mp_d001.csv；修复需读 mp_manifest.csv 映射。",
        ),
        transfer=BenchmarkCase(
            id="MP3_transfer_returns",
            category="memory_probe",
            difficulty="hard",
            request=(
                "读取 benchmark_data/ 下名为 returns 的数据集（CSV 文件），"
                "计算 amount 列的总和，只打印这个数字。"
            ),
            expected_outcome="RECOVERED",
            expected_keywords=["1010"],
            description="同一目录、同一 manifest：returns 实为 mp_d002.csv；同根因，策略可迁移。",
        ),
    ),
]
