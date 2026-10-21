"""Live experiment: the experience loop inside the main runtime, real LLM, Reforge's own domain.

Prereg: runs/runtime_live/prereg.md. Everything goes through RuntimeRunner -> build_graph unpatched;
this driver only sets env (model, flag, isolated homes), observes each round through
runtime_hooks.PROBE_OBSERVERS, meters tokens, and scores the final answer AFTER the run (shadow —
the expected answers never reach the runner).

    python scripts/run_runtime_live.py discover
    python scripts/run_runtime_live.py canary  --card <id> --tasks D3,D5
    python scripts/run_runtime_live.py promote --card <id> --tasks D3,D5
    python scripts/run_runtime_live.py recur   --kind swallowed_exception|missing_return
    # L2 critic batch (RUNTIME_LIVE_DIR=runs/runtime_live/critic RUNTIME_LIVE_BUDGET=100000):
    python scripts/run_runtime_live.py critic-replay       # critic on the recorded rounds (0 generator tokens)
    python scripts/run_runtime_live.py discover --critic   # live, critic tier on
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
# RUNTIME_LIVE_DIR selects the batch directory (logs, memory, ledger); the task data is shared.
OUT = _ROOT / os.environ.get("RUNTIME_LIVE_DIR", "runs/runtime_live")
DATA, WORK = _ROOT / "runs" / "runtime_live" / "data", OUT / "work"
BUDGET = int(os.environ.get("RUNTIME_LIVE_BUDGET", "80000"))
PER_RUN = 10_000

# ---- env before any reforge import (config reads env at import) --------------------------------
from dotenv import dotenv_values  # noqa: E402

_env = dotenv_values(_ROOT / ".env")
os.environ.update({
    "LLM_BASE_URL": _env["VISION_LLM_BASE_URL"], "LLM_API_KEY": _env["VISION_LLM_API_KEY"],
    "LLM_MODEL": "qwen3.8-max", "LLM_ENABLE_THINKING": "false",
    "REFORGE_EXPERIENCE_LOG": str(OUT / "experience.jsonl"),
    "REFORGE_HOME": str(OUT / "home"), "REFORGE_PROJECT_DIR": str(OUT / "project"),
})
sys.path.insert(0, str(_ROOT))

from reforge.experience import CanaryReport, critic, runtime_hooks, runtime_probes  # noqa: E402
from reforge.observability.llm_events import token_accounting  # noqa: E402
from reforge.runtime.orchestration.engine.runner import RuntimeRunner  # noqa: E402


# ---- data (deterministic) + expected answers (shadow only) -----------------------------------
def build_data() -> dict:
    DATA.mkdir(parents=True, exist_ok=True)
    amounts = ["120.5", "1,200", "$35", "N/A", "", "80", "2,450.75", "15", "$1,000", "42"]
    (DATA / "amounts.csv").write_text("order_id,amount\n" + "".join(
        f'{i},"{a}"\n' for i, a in enumerate(amounts, 1)), encoding="utf-8")
    events = ["click"] * 5 + ["view"] * 4 + ["buy"] * 3
    lines = [json.dumps({"id": i, "type": t}) for i, t in enumerate(events)]
    lines.insert(4, '{"id": 99, "type": "click"')          # corrupted
    lines.insert(9, "not json at all")                       # corrupted
    (DATA / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    scores = [55, 72, 68, 91, 70, 83, 40, 77, 65, 99, 71]
    (DATA / "scores.csv").write_text("id,score\n" + "".join(f"{i},{s}\n" for i, s in enumerate(scores, 1)),
                                     encoding="utf-8")
    (DATA / "config.json").write_text(json.dumps({"Threshold": 70}), encoding="utf-8")
    sales = [("2024-01-03", 120), ("2024-01-20", 80), ("2024-02-02", 300), ("2024-02-15", 40),
             ("2024-03-01", 150), ("2024-03-09", 160), ("2024-04-11", 90)]
    (DATA / "sales.csv").write_text("date,sales\n" + "".join(f"{d},{v}\n" for d, v in sales), encoding="utf-8")
    qty = ["3", "4 pcs", " 7 ", "n/a", "10", "2 pcs", "", "5"]
    (DATA / "qty.csv").write_text("item,qty\n" + "".join(f'{i},"{q}"\n' for i, q in enumerate(qty, 1)),
                                  encoding="utf-8")
    regions = [("north", 120), ("south", 340), ("east", 90), ("south", 60), ("west", 300), ("north", 200)]
    (DATA / "regions.csv").write_text("region,revenue\n" + "".join(f"{r},{v}\n" for r, v in regions),
                                      encoding="utf-8")
    s = sorted(scores)
    return {"D1": 4943.25, "D2": {"click": 5, "view": 4, "buy": 3}, "D3": sum(x > 70 for x in scores),
            "D4": "2024-02", "D5": float(s[len(s) // 2]), "R": 31.0, "Rm": "south"}


def tasks() -> dict[str, str]:
    p = {k: str(DATA / v) for k, v in {"a": "amounts.csv", "e": "events.jsonl", "s": "scores.csv",
                                       "c": "config.json", "sa": "sales.csv", "q": "qty.csv",
                                       "r": "regions.csv"}.items()}
    return {
        "D1": f"Compute the total of the `amount` column in {p['a']}. The data is messy; make sure the script "
              f"does not crash. Print the total.",
        "D2": f"Count the events per `type` in {p['e']} (JSON lines). Some lines are corrupted; skip them and "
              f"print the count for each type.",
        "D3": f"Read the threshold from {p['c']} and count the rows in {p['s']} whose `score` is above it. "
              f"If anything goes wrong, the script must still finish. Print the count.",
        "D4": f"Define a function `best_month(path)` that finds the month (YYYY-MM) with the highest total "
              f"`sales` in the CSV at path. Then print the value returned by best_month('{p['sa']}').",
        "D5": f"Compute the median of the `score` column in {p['s']}. Print it.",
        "R": f"Sum the `qty` column in {p['q']}. The values are messy; the script must not crash. Print the sum.",
        "Rm": f"Define a function `top_region(path)` that finds the region with the highest total `revenue` in "
              f"the CSV at path. Then print the value returned by top_region('{p['r']}').",
    }


def score(task: str, stdout: str, expected) -> bool:
    """Shadow scoring, after the run. Numbers: the last number printed; names: the last line."""
    out = (stdout or "").strip()
    if isinstance(expected, dict):
        return all(re.search(rf"{k}\W+{v}\b", out) for k, v in expected.items())
    if isinstance(expected, str):
        last = out.splitlines()[-1] if out else ""
        return expected in last
    nums = re.findall(r"-?\d[\d,]*\.?\d*", out)
    try:
        return bool(nums) and abs(float(nums[-1].replace(",", "")) - float(expected)) < 0.01
    except ValueError:
        return False


# ---- one run through the production runtime ----------------------------------------------------
def _spent() -> int:
    total = 0
    for name in ("runs.jsonl", "critic_replay.jsonl"):
        p = OUT / name
        if p.exists():
            total += sum(json.loads(x)["tokens"] for x in p.read_text(encoding="utf-8").splitlines())
    return total


CRITIC_ENV = {"REFORGE_EXPERIENCE_CRITIC": "1", "REFORGE_EXPERIENCE_CRITIC_MODEL": "qwen3.8-27b",
              "REFORGE_EXPERIENCE_CRITIC_BASE_URL": _env["VISION_LLM_BASE_URL"],
              "REFORGE_EXPERIENCE_CRITIC_API_KEY": _env["VISION_LLM_API_KEY"],
              "REFORGE_EXPERIENCE_CRITIC_THINKING": "false"}


def critic_replay() -> None:
    """Recorded flag-on rounds of the first batch -> the production trigger + critic (no generation)."""
    src = _ROOT / "runs" / "runtime_live" / "runs.jsonl"
    crit = critic.Critic.from_env()
    for line in src.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        for rd in r["rounds"]:
            sus = runtime_probes.probe_only_suspicions(tasks()[r["task"]], rd["stdout"], rd["exit_code"], rd["code"])
            row = {"task": r["task"], "arm": r["arm"], "attempt": rd["attempt"], "signals": [x.kind for x in sus],
                   "tokens": 0}
            if sus:
                if _spent() + PER_RUN > BUDGET:
                    print("token guard — stop (prereg)")
                    return
                req = tasks()[r["task"]]
                j = crit.judge(req, critic.profile_text(critic.data_paths(req)), rd["code"], rd["stdout"])
                row.update(verdict=j.verdict, confidence=j.confidence, confirmed=j.confirmed, suspect=j.suspect,
                           tokens=j.total_tokens, error=j.error)
            with open(OUT / "critic_replay.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(json.dumps(row, ensure_ascii=False), flush=True)


def run(task: str, arm: str, *, cards_flag: bool, canary: str = "") -> dict | None:
    spent = _spent()
    if spent + PER_RUN > BUDGET:
        print(f"token guard: spent {spent}, next run could exceed {BUDGET} — stop (prereg)", flush=True)
        return None
    os.environ["REFORGE_EXPERIENCE_CARDS"] = "1" if cards_flag else "0"
    if canary:
        os.environ[runtime_hooks.CANARY_ENV] = canary
    else:
        os.environ.pop(runtime_hooks.CANARY_ENV, None)
    rounds: list[dict] = []
    runtime_hooks.PROBE_OBSERVERS[:] = [lambda st, rows: rounds.append({
        "attempt": st.control_state.retry_count + 1, "code": st.generated_code,
        "stdout": (st.execution_output.stdout or "")[-3000:], "stderr": (st.execution_output.stderr or "")[-1500:],
        "exit_code": st.execution_output.exit_code, "probe_rows": rows})]
    WORK.mkdir(parents=True, exist_ok=True)
    os.chdir(WORK)
    with token_accounting(f"{arm}:{task}", 0) as ledger:
        state = RuntimeRunner().run(tasks()[task])
    expected = build_data()[task]                                   # measurement only, after the run
    rec = {"task": task, "arm": arm, "canary": canary, "flag": cards_flag, "rounds": rounds,
           "final_stdout": (state.exec_state.stdout or "")[-3000:], "exit_code": state.exec_state.exit_code,
           "outcome": state.outcome_state.task_outcome, "retries": state.control_state.retry_count,
           "correct": score(task, state.exec_state.stdout, expected),
           "tokens": ledger.total_tokens, "calls": ledger.calls, "tokens_unknown": ledger.unknown}
    with open(OUT / "runs.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps({k: rec[k] for k in ("task", "arm", "outcome", "retries", "correct", "tokens", "calls")}
                     | {"probes": [r["probe_rows"] for r in rounds]}, ensure_ascii=False), flush=True)
    return rec


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("phase", choices=["discover", "canary", "promote", "recur", "critic-replay"])
    ap.add_argument("--critic", action="store_true", help="turn the L2 critic tier on (cross-model)")
    ap.add_argument("--card")
    ap.add_argument("--tasks")
    ap.add_argument("--kind")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    build_data()
    if a.critic or a.phase == "critic-replay":
        os.environ.update(CRITIC_ENV)
    if a.phase == "critic-replay":
        critic_replay()
    elif a.phase == "discover":
        for t in ("D1", "D2", "D3", "D4", "D5"):
            if run(t, "discover", cards_flag=True) is None:
                return
    elif a.phase == "canary":
        for t in a.tasks.split(","):
            if run(t, "canary", cards_flag=True, canary=a.card) is None:
                return
    elif a.phase == "promote":
        rows = [json.loads(x) for x in (OUT / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
        ts = a.tasks.split(",")
        ctl = {r["task"]: r["correct"] for r in rows if r["arm"] == "discover" and r["task"] in ts}
        trt = {r["task"]: r["correct"] for r in rows if r["arm"] == "canary" and r["canary"] == a.card}
        rep = CanaryReport(a.card, n=len(ts), control_success=sum(ctl.values()), treated_success=sum(trt.values()),
                           regressions=tuple(t for t in ts if ctl.get(t) and not trt.get(t)))
        print(rep, "passed" if rep.passed else "FAILED")
        if rep.passed:
            print(runtime_hooks.runtime_loop().promote(a.card, rep).status)
    else:
        task = "R" if a.kind == "swallowed_exception" else "Rm"
        for arm, flag in (("recall", True), ("nocard", False)):
            if run(task, arm, cards_flag=flag) is None:
                return


if __name__ == "__main__":
    main()
