"""LLM-judge probe — REAL LLM, burns tokens. Tests whether option B can work.

Does NOT touch the production HeuristicEvaluator. It replays a batch of tasks,
and for each final solution records THREE independent verdicts:

  gold       — the held-out test suite (field of record: is it actually right)
  heuristic  — the runtime evaluator's outcome (SUCCESS/RECOVERED = "passed")
  llm_judge  — a fresh LLM asked "is this correct?" given ONLY the request, the
               artifact, and its stdout — a no-gold judge, the honest stand-in
               for option B.

JUDGE_MODE picks the judge prompt:
  v1 — terse "is this correct?" (task-agnostic, one-line verdict).
  v2 — same task-agnostic framing but with an explicit reason-first step: name
       the requirement's error-prone / ambiguous points, invent a concrete
       example for each, trace the artifact's output on it, compare to intent.
       Deliberately worded WITHOUT "code/function/unit test" so the judge stays
       general (the runtime also grades SQL / data / vision tasks), not a
       code-only judge.

Load-bearing question: on the gold-wrong answers the heuristic waved through
(the FPs), how many does the judge catch, and how many correct answers does it
wrongly kill? Token accounting is recorded so the v1→v2 cost delta is measured,
not estimated.

Governor arm × 1 seed. DURABLE: appends per-task to a JSONL and resumes. Run:

    python scripts/fetch_mbpp.py       # once
    python scripts/llm_judge_probe.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from reforge.benchmark.targeted.mbpp_task import MbppTask
from reforge.models.adapters.llm_client import LLMClient
from reforge.observability.llm_events import token_accounting
from reforge.runtime.orchestration.engine.runner import RuntimeRunner

DATASET = "mbpp"
N = 50
SEED = 0
JUDGE_MODE = "v2"   # "v1" (terse) | "v2" (reason-first, still task-agnostic)

# When non-empty, run EXACTLY these task_ids instead of the first N. This is
# the gold-wrong set from the fp_sweep (100-task) run — every one is a known FP
# candidate, so token spend goes straight to testing judge recall. Replay is
# non-deterministic, so a few may come out correct this time; _report keys off
# the fresh gold verdict, not the label, so that stays honest.
FP_TASK_IDS = (63, 67, 74, 83, 87, 89, 91, 102, 106, 117,
               124, 128, 132, 140, 143, 160, 163, 164)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RECORDS = _REPO_ROOT / (
    f"llm_judge_probe_{DATASET}{'_fp' if FP_TASK_IDS else ''}_{JUDGE_MODE}.jsonl"
)

_JUDGE_SYSTEM_V1 = (
    "你是严格的代码正确性审查员。给定一道编程任务的需求、一段实现代码、以及它"
    "运行时的输出。判断这段代码是否**正确且完整地**实现了需求。\n"
    "注意：输出看起来正常、不报错，并不代表逻辑正确——你要判断的是正确性本身，"
    "包括边界情况、参数含义、返回值语义是否都对。\n"
    "严格按两行回复：\n"
    "VERDICT: PASS 或 FAIL\n"
    "REASON: 一句话理由"
)

# Task-agnostic on purpose: says "产物（代码/查询/脚本等）", never "function" or
# "unit test", so the same judge can grade non-code tasks the runtime handles.
_JUDGE_SYSTEM_V2 = (
    "你是严格的正确性审查员。给定一项任务的需求、完成它的产物（代码/查询/脚本等）、"
    "以及产物运行后的输出。判断这个产物是否**正确且完整地**满足了需求。\n"
    "不要只看输出是否正常、是否报错——那不代表正确。请先推理再下结论：\n"
    "1. 找出需求里最容易出错或被误解的关键点：边界/特殊情况、含义模糊的术语、"
    "参数或顺序的约定、对“正确结果”的不同可能理解；\n"
    "2. 针对每个关键点，举一个具体的例子输入，根据产物的实际逻辑推演它会产出什么；\n"
    "3. 把推演结果和需求的真实意图对照；若需求本身有多种合理解读，指出产物选了"
    "哪一种、是否可能与预期不符；\n"
    "4. 只要有一个关键点上产物与需求意图不符，就判 FAIL。\n"
    "严格按格式回复：\n"
    "ANALYSIS: <逐条推演，简洁>\n"
    "VERDICT: PASS 或 FAIL\n"
    "REASON: 一句话"
)

_JUDGE_SYSTEMS = {"v1": _JUDGE_SYSTEM_V1, "v2": _JUDGE_SYSTEM_V2}


def _parse_verdict(resp: str) -> tuple[bool, str]:
    verdict, reason = None, ""
    for line in resp.splitlines():
        s = line.strip()
        m = re.match(r"VERDICT:\s*(PASS|FAIL)", s, re.IGNORECASE)
        if m:
            verdict = m.group(1).upper() == "PASS"
        elif s.upper().startswith("REASON:"):
            reason = s.split(":", 1)[1].strip()
    if verdict is None:  # fallback: look for a bare PASS/FAIL token
        u = resp.upper()
        if "FAIL" in u:
            verdict = False
        elif "PASS" in u:
            verdict = True
        else:
            verdict = True  # unparseable → lenient, mirrors a permissive judge
    return verdict, (reason or resp.strip()[:120])


def llm_judge(request: str, code: str, stdout: str, mode: str) -> tuple[bool, str]:
    user = (
        f"任务需求：\n{request}\n\n"
        f"产物：\n```\n{code}\n```\n\n"
        f"运行输出：\n{stdout[:800]}\n\n"
        "这个产物是否正确且完整地满足了需求？"
    )
    resp = LLMClient().chat(_JUDGE_SYSTEMS[mode], user)
    return _parse_verdict(resp)


def _replay(task: MbppTask, case) -> dict:
    """Governor-arm replay; return final artifact, heuristic verdict, gold, tokens."""
    prompt = task.build_prompt(case)
    final = None
    with token_accounting(case_id=case.case_id, seed=SEED) as rl:
        for _node, st in RuntimeRunner().stream(prompt):
            final = st
    code = (final.generated_code or "") if final else ""
    stdout = (final.exec_state.stdout or "") if final else ""
    outcome = (final.outcome_state.task_outcome or "") if final else ""
    heuristic_pass = outcome in ("SUCCESS", "RECOVERED")
    gold_pass = task.grade(case, (final.outcome_state.final_answer or "") if final else "",
                           None, code)
    return {"code": code, "stdout": stdout, "outcome": outcome,
            "heuristic_pass": heuristic_pass, "gold_pass": bool(gold_pass),
            "runtime_prompt_tok": rl.prompt_tokens, "runtime_comp_tok": rl.completion_tokens}


def main() -> None:
    task = MbppTask(task_ids=FP_TASK_IDS) if FP_TASK_IDS else MbppTask(limit=N)
    cases = task.load_cases()
    done = {}
    if _RECORDS.exists():
        for line in _RECORDS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done[r["case_id"]] = r
    print(f"LLM-judge probe [{DATASET}] mode={JUDGE_MODE}: {len(cases)} tasks, "
          f"seed={SEED} ({len(done)} cached)\n")

    for i, case in enumerate(cases, 1):
        if case.case_id in done:
            r = done[case.case_id]
        else:
            rp = _replay(task, case)
            with token_accounting(case_id=case.case_id, seed=SEED) as jl:
                jpass, jreason = llm_judge(
                    case.payload["prompt"], rp["code"], rp["stdout"], JUDGE_MODE)
            r = {"case_id": case.case_id, "gold_pass": rp["gold_pass"],
                 "heuristic_pass": rp["heuristic_pass"], "outcome": rp["outcome"],
                 "llm_judge_pass": jpass, "llm_judge_reason": jreason,
                 "runtime_prompt_tok": rp["runtime_prompt_tok"],
                 "runtime_comp_tok": rp["runtime_comp_tok"],
                 "judge_prompt_tok": jl.prompt_tokens, "judge_comp_tok": jl.completion_tokens,
                 "code": rp["code"], "stdout": rp["stdout"][:300]}
            with _RECORDS.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            done[case.case_id] = r
        tag = "" if r["gold_pass"] else "  <-- GOLD WRONG"
        print(f"[{i}/{len(cases)}] {case.case_id} gold={r['gold_pass']} "
              f"heur={r['heuristic_pass']} judge={r['llm_judge_pass']}{tag}")

    _report([done[c.case_id] for c in cases if c.case_id in done])


def _report(rows: list[dict]) -> None:
    # The FP set: gold-wrong answers the heuristic accepted.
    fp = [r for r in rows if not r["gold_pass"] and r["heuristic_pass"]]
    caught = [r for r in fp if not r["llm_judge_pass"]]
    correct = [r for r in rows if r["gold_pass"]]
    judge_kills = [r for r in correct if not r["llm_judge_pass"]]

    print(f"\n================ THREE-WAY (tasks, judge={JUDGE_MODE}) ================")
    print(f"  tasks={len(rows)}  gold_pass={sum(r['gold_pass'] for r in rows)}")
    print(f"  heuristic false positives (gold wrong, heuristic accepted): {len(fp)}")
    if fp:
        print(f"  --> LLM judge CAUGHT {len(caught)}/{len(fp)} = {len(caught)/len(fp):.0%}")
    if correct:
        print(f"  LLM judge wrongly killed {len(judge_kills)}/{len(correct)} correct "
              f"= {len(judge_kills)/len(correct):.0%} (its own FN)")

    print(f"\n---- FP cases (the {len(fp)} the heuristic waved through) ----")
    for r in fp:
        verdict = "CAUGHT" if not r["llm_judge_pass"] else "MISSED"
        print(f"  {r['case_id']:12s} judge={r['llm_judge_pass']} [{verdict}] "
              f"reason={r['llm_judge_reason']!r}")
    if judge_kills:
        print(f"\n---- judge wrongly killed ({len(judge_kills)} correct) ----")
        for r in judge_kills:
            print(f"  {r['case_id']:12s} reason={r['llm_judge_reason']!r}")

    # Measured token cost (only rows that carry token fields).
    toks = [r for r in rows if "judge_comp_tok" in r]
    if toks:
        n = len(toks)
        rt = sum(r.get("runtime_prompt_tok", 0) + r.get("runtime_comp_tok", 0) for r in toks) / n
        jp = sum(r["judge_prompt_tok"] for r in toks) / n
        jc = sum(r["judge_comp_tok"] for r in toks) / n
        jt = jp + jc
        total = rt + jt
        print(f"\n---- TOKENS (avg/task, mode={JUDGE_MODE}) ----")
        print(f"  runtime replay : {rt:.0f}")
        print(f"  judge          : prompt {jp:.0f} + completion {jc:.0f} = {jt:.0f}")
        print(f"  judge share    : {jt/total:.0%} of {total:.0f}/task")
        print("  (compare judge completion vs v1's ~40 to get the v1→v2 delta)")

    print("\nProbe done. Report the three-way + FP verdicts + token block.")


if __name__ == "__main__":
    main()
