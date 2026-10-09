"""Contract-augmented variant of the memory-transfer probe corpus.

The pre-registered corpus (`memory_probe_cases.py`) is left byte-identical —
its pilot (`docs/eval/MEMORY_TRANSFER_PROBE_PILOT.md`) failed for one reason:
the model "repaired" the missing table / column / file by *gracefully masking*
it — returning 0, or printing a warning and carrying on — and the no-gold
heuristic evaluator accepted that as RECOVERED. With the trap never actually
paid for, cold runs never spent budget discovering the legend and warm runs
had no correct repair to recall.

This module derives a second instrument from that same corpus by appending an
**output contract** to each request. The contract does not reveal the legend
or the encoding; it only forbids the masking path:

    * print the aggregate AND the number of rows that went into it
    * fail with a non-zero exit code when the target cannot be located or
      when zero rows were aggregated

That turns a silent wrong answer into a loud crash, which is the precondition
the whole self-heal loop depends on — a failure the runtime cannot see is a
failure it cannot classify, retry, or remember. Nothing about the runtime is
modified: verification lives in the task contract, the one place a no-gold
evaluator can still be given teeth.

Deliberately NOT a loosening of the pre-registered corpus: the schema, the
legend, the fingerprint axes and the gold answers are untouched, and the
original cases remain available for anyone reproducing the pilot.
"""

from __future__ import annotations

from dataclasses import replace

from reforge.benchmark.experience_cases import PairedCase
from reforge.benchmark.memory_probe_cases import LEGEND_MARKERS, PAIRED_CASES_MP
from reforge.benchmark.models import BenchmarkCase

__all__ = ["CONTRACT_CLAUSE", "LEGEND_MARKERS", "PAIRED_CASES_MPC", "with_contract"]


# Appended verbatim to every request. Phrased as a reporting requirement, not
# as a hint: it says what the script must print and when it must fail, never
# that names are encoded or that a legend exists.
CONTRACT_CLAUSE = (
    "\n\n硬性要求（必须遵守）：\n"
    "1. 打印两行，第一行是汇总值，第二行是 ROWS=<实际参与汇总的行数>。\n"
    "2. 如果目标列/表/文件定位不到，或参与汇总的行数为 0，"
    "必须抛出异常以非零退出码结束。\n"
    "3. 禁止返回 0、禁止打印警告后继续、禁止用空结果冒充答案。"
)


def with_contract(case: BenchmarkCase) -> BenchmarkCase:
    """Return *case* with the output contract appended to its request."""
    return replace(
        case,
        id=f"{case.id}_contract",
        request=case.request + CONTRACT_CLAUSE,
        description=f"{case.description}（+输出契约：禁止静默掩盖）",
    )


PAIRED_CASES_MPC: list[PairedCase] = [
    PairedCase(
        pair_id=f"{pair.pair_id}C",
        fingerprint_axis=pair.fingerprint_axis,
        seed=with_contract(pair.seed),
        transfer=with_contract(pair.transfer),
    )
    for pair in PAIRED_CASES_MP
]
