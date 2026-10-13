"""Frozen verifier ladder on InfiAgent-DABench (real tables, closed-form gold).

Main question: on runs the agent got RIGHT, how often does the verifier raise
a false alarm? (Recall on naturally-occurring silent errors is a by-product.)

Subset: questions whose label is a single numeric answer ("table → number"),
so the answer is the last number printed and gold matching is unambiguous.
Gold match follows the benchmark's own rule: |pred − label| < 1e-6 on the
value inside `@name[value]`.

Data is NOT vendored (CC BY-NC 4.0): pass --data pointing at a checkout of
InfiAgent/examples/DA-Agent/data (da-dev-questions.jsonl, da-dev-labels.jsonl,
da-dev-tables/).

    python scripts/run_verifier_infiagent.py --data <dir> --limit 5
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _num(s: str) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


class DACase:
    """Duck-types SilentTrapCase for run_distill_ablation.run_one."""

    def __init__(self, q: dict, label: list, tables: Path):
        self.id = f"da{q['id']:04d}"
        self.q = q
        self.name, raw = label
        self.gold = float(raw)
        self.tolerance = 1e-6
        self.table = tables / q["file_name"]
        self.request = (f"Read the file {q['file_name']} in the current directory and answer:\n{q['question']}\n"
                        f"Constraints: {q['constraints']}\n"
                        f"The LAST line of output must be exactly in this format: {q['format']}")

    def build(self, data_dir: str) -> str:
        os.makedirs(data_dir, exist_ok=True)
        shutil.copy(self.table, Path(data_dir) / self.table.name)
        return data_dir

    def answer(self, stdout: str) -> float | None:
        m = re.findall(rf"@{re.escape(self.name)}\[([^\]]*)\]", stdout or "")
        return _num(m[-1].strip()) if m else None


def subset(data: Path) -> list[DACase]:
    qs = [json.loads(x) for x in (data / "da-dev-questions.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    labels = {j["id"]: j["common_answers"] for j in
              (json.loads(x) for x in (data / "da-dev-labels.jsonl").read_text(encoding="utf-8").splitlines() if x.strip())}
    out = []
    for q in qs:
        ans = labels.get(q["id"], [])
        if len(ans) == 1 and _num(ans[0][1]) is not None and (data / "da-dev-tables" / q["file_name"]).exists():
            out.append(DACase(q, ans[0], data / "da-dev-tables"))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--limit", type=int, default=0, help="0 = whole subset")
    ap.add_argument("--agent-token-stop", type=int, default=800_000,
                    help="pause (exit before verifying) once agent tokens reach this — quota guard")
    ap.add_argument("--critic-budget", type=int, default=980_000)
    ap.add_argument("--thinking-budget", type=int, default=4000, help="agent-only; 0 = uncapped")
    ap.add_argument("--agent-only", action="store_true")
    ap.add_argument("--levels", default="", help="comma list of easy,medium,hard; empty = all")
    ap.add_argument("--agent-model", default="qwen3.8-2.4t-a95b")
    ap.add_argument("--critic-model", default="qwen3.8-max-0902")
    ap.add_argument("--critic-on", choices=("all", "correct", "none"), default="all")
    ap.add_argument("--out", default="runs/verifier_infiagent.jsonl")
    args = ap.parse_args()

    from run_distill_ablation import _configure_models, run_one
    _configure_models(args.agent_model)
    os.environ["REFORGE_EXP_DISTILLED_MEMORY"] = "0"
    # Driver-only guard: one agent call hung >35 min on the SDK's default
    # 600 s × retries. Cap it here; a timed-out question is recorded with
    # `error` and retried on the next resume. Production client untouched.
    # Same wrapper caps the agent's thinking: uncapped, one hard question
    # spent 30–40k completion tokens / 10–15 min in a single call. The agent
    # only manufactures runs to verify; it is not the thing under test.
    import reforge.models.adapters.llm_client as _lc
    _RealOpenAI = _lc.OpenAI

    def _capped(*a, **k):
        client = _RealOpenAI(*a, timeout=300.0, max_retries=1, **k)
        orig = client.chat.completions.create

        def create(*ca, **ck):
            eb = dict(ck.pop("extra_body", None) or {})
            eb.update(enable_thinking=True, thinking_budget=args.thinking_budget)
            return orig(*ca, extra_body=eb, **ck)
        client.chat.completions.create = create
        return client
    if args.thinking_budget:
        _lc.OpenAI = _capped

    import verifier_apply
    from verifier_apply import VERIFIER_SHA, apply_ladder
    # Run-budget cap only: decides how many questions the critic may cover,
    # never what it says about one. Set from outside the frozen file.
    verifier_apply.V.TOKEN_BUDGET = args.critic_budget

    cases = subset(Path(args.data))
    print(f"single-numeric-answer subset: {len(cases)} questions over "
          f"{len({c.table.name for c in cases})} tables; running {args.limit or 'all'}")
    if args.levels:
        cases = [c for c in cases if c.q["level"] in args.levels.split(",")]
    # easy → medium → hard, so a token stop can only ever cut hard questions
    cases.sort(key=lambda c: ("easy", "medium", "hard").index(c.q["level"]))
    cases = cases[:args.limit] if args.limit else cases

    out = _ROOT / args.out
    agent_path = out.with_suffix(".agent.jsonl")
    done = {}
    if agent_path.exists():
        for line in agent_path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if not r["error"] and r.get("agent_model") == args.agent_model:
                done[r["case_id"]] = r
    spent = sum(r["tokens_prompt"] + r["tokens_completion"] for r in done.values())
    root = Path(tempfile.mkdtemp(prefix="reforge_infi_"))
    print(f"agent={os.environ['LLM_MODEL']} verifier_sha={VERIFIER_SHA}", flush=True)
    with open(agent_path, "a", encoding="utf-8") as fh:
        for i, c in enumerate(cases):
            if c.id in done:
                continue
            if spent >= args.agent_token_stop:
                skipped = [(x.id, x.q["level"]) for x in cases[i:] if x.id not in done]
                print(f"TOKEN STOP: agent tokens {spent} >= {args.agent_token_stop}; skipped {len(skipped)}: {skipped}",
                      flush=True)
                cases = [x for x in cases if x.id in done]
                break
            rec, _ = run_one(c, pair_id=c.q["level"], role="infiagent", arm="cold", replicate=0,
                             order_index=i, run_dir=root / c.id, preload=None, distill=False)
            r = asdict(rec)
            r["agent_model"] = args.agent_model
            r["thinking_budget"] = args.thinking_budget
            spent += r["tokens_prompt"] + r["tokens_completion"]
            # benchmark's own match: the value inside @name[...], not "last number"
            ans = c.answer(r["stdout_tail"])
            r["answer"] = ans
            r["passed"] = ans is not None and abs(ans - c.gold) < 1e-6
            r["answer_is_last_number"] = ans is not None and r["printed"] is not None and abs(ans - r["printed"]) < 1e-9
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            fh.flush()
            print(f"  {c.id} [{c.q['level']:6s}] {'PASS' if r['passed'] else 'FAIL'} answer={ans} gold={c.gold} "
                  f"{rec.outcome} tok={spent} {rec.error[:100]}", flush=True)
            if not rec.error:
                done[c.id] = r
    if args.agent_only:
        return
    missing = [c.id for c in cases if c.id not in done]
    if missing:
        sys.exit(f"STOPPED: {len(missing)} questions errored (rerun to resume): {missing[:10]}")

    data_dirs = {c.id: Path(c.build(str(root / "data" / c.id))) for c in cases}
    requests = {c.id: c.request for c in cases}
    rows = [{**done[c.id], "run": "infiagent", "group": c.q["level"], "wrong": not done[c.id]["passed"]}
            for c in cases if c.id in done]
    flt = {"all": None, "correct": (lambda r: not r["wrong"]), "none": None}[args.critic_on]
    apply_ladder(rows, data_dirs, requests, out,
                 critic_model=None if args.critic_on == "none" else args.critic_model, critic_filter=flt)
    print(f"records={len(rows)} → {out}")


if __name__ == "__main__":
    main()
