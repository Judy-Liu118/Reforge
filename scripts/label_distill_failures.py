"""Post-hoc causal labels for transfer runs of run_distill_ablation.py.

For every transfer run (cold baseline included), an LLM judge (the distiller
endpoint, deepseek-v4-pro) reads the plan + code against the case's TRUE
contaminant and answers three yes/no questions:

    plan_reflects_card   the plan contains a step taken from the injected card
                         (null when no card was injected)
    plan_addresses_trap  the plan contains a step aimed at the actual defect
    code_addresses_trap  the code contains a step aimed at the actual defect

A failed warm run then gets one cause label:

    plan_missed_trap      ① strategy did not reach the plan (nothing aimed at the defect)
    plan_hit_exec_failed  ② plan aimed at the defect, number still wrong
                             (S2-smoke type: planned, executed silently ineffective)

Gold-pass/fail itself never comes from the judge — it is in the run record.

    python scripts/label_distill_failures.py runs/distill_full.jsonl
    → runs/distill_full.labels.jsonl
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_distill_ablation import _configure_models  # noqa: E402

_SYSTEM = """你是代码审阅员。给你一个数据任务、其中真实存在的静默数据缺陷、agent 的计划和代码，
以及(可能有的)注入给 agent 的策略卡。只根据文本判断，回答以下问题，只输出 JSON:
{"plan_reflects_card": true/false/null, "plan_addresses_trap": true/false, "code_addresses_trap": true/false, "note": "一句话"}
- plan_reflects_card: 计划中是否有步骤明显来自策略卡;没有策略卡时填 null。
- plan_addresses_trap: 计划中是否有步骤针对的正是这个真实缺陷(泛泛的"检查数据质量"不算，必须能对上该缺陷)。
- code_addresses_trap: 代码中是否有针对这个真实缺陷的处理(不论处理是否奏效)。"""


def _case_index() -> dict:
    from reforge.benchmark.silent_trap_cases import SILENT_PAIRS
    from reforge.benchmark.silent_trap_multistep_cases import MULTISTEP_PAIRS

    idx = {}
    for p in list(SILENT_PAIRS) + list(MULTISTEP_PAIRS):
        for c in (p.seed, p.transfer):
            idx[c.id] = c
    return idx


def judge(client, case, row: dict) -> dict:
    card = row.get("card") or {}
    card_txt = (f"{card.get('title', '')}\n{card.get('description', '')}\n{card.get('content', '')}"
                if row["arm"] != "cold_transfer" and card else "(无)")
    msg = (f"## 任务\n{case.request}\n\n## 真实缺陷\n{case.contaminant}\n正确做法: {case.correct_strategy}\n\n"
           f"## 注入的策略卡\n{card_txt}\n\n## 计划\n{row['plan_head']}\n\n## 代码\n{row['code_tail']}")
    raw = client.chat(_SYSTEM, msg)
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        return json.loads(m.group(0)) if m else {"error": raw[:200]}
    except (json.JSONDecodeError, ValueError):
        return {"error": raw[:200]}


def cause(row: dict, j: dict) -> str:
    if row["passed"] or row["arm"] == "cold_transfer":
        return ""
    return "plan_hit_exec_failed" if j.get("plan_addresses_trap") else "plan_missed_trap"


def main() -> None:
    src = Path(sys.argv[1])
    dst = src.with_suffix(".labels.jsonl")
    _configure_models("qwen-plus")   # only to populate REFORGE_DISTILL_* from .env
    import os

    from reforge.models.adapters.llm_client import LLMClient

    client = LLMClient(base_url=os.environ["REFORGE_DISTILL_BASE_URL"],
                       api_key=os.environ["REFORGE_DISTILL_API_KEY"],
                       model=os.environ["REFORGE_DISTILL_MODEL"])
    cases = _case_index()
    done = set()
    if dst.exists():
        for line in dst.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            done.add((r["replicate"], r["arm"], r["case_id"]))
    rows = [json.loads(x) for x in src.read_text(encoding="utf-8").splitlines() if x.strip()]
    with open(dst, "a", encoding="utf-8") as fh:
        for row in rows:
            key = (row["replicate"], row["arm"], row["case_id"])
            if row["role"] != "transfer" or key in done:
                continue
            j = judge(client, cases[row["case_id"]], row)
            out = {"replicate": row["replicate"], "arm": row["arm"], "pair_id": row["pair_id"],
                   "case_id": row["case_id"], "passed": row["passed"], **j, "cause": cause(row, j)}
            fh.write(json.dumps(out, ensure_ascii=False) + "\n")
            fh.flush()
            print(f"  rep{row['replicate']} {row['arm']:13s} {row['case_id']:28s} "
                  f"pass={row['passed']} card={j.get('plan_reflects_card')} "
                  f"plan={j.get('plan_addresses_trap')} code={j.get('code_addresses_trap')} {out['cause']}",
                  flush=True)
    print(f"labels → {dst}")


if __name__ == "__main__":
    main()
