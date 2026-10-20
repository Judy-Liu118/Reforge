"""Gate runs on AppWorld: a bare ReAct baseline (no cards, no hints, no verifier).

Not the AppWorldAgent — just enough to (gate 0) measure tokens per task and
(gate 2) see how a plain agent fails. The prompt describes the environment as
AppWorld's README does (how to read API docs, where the supervisor's data is,
complete_task) and nothing more: no login / pagination / time hints.

Per task, everything is stored under runs/appworld_gate/<task_id>/:
    steps.jsonl   one row per step: model text, code, execution output, tokens
    eval.json     AppWorld's state-based tests (gold) — written AFTER the agent stopped
AppWorld itself also writes environment_io.md under its experiment outputs.

    python scripts/appworld_baseline.py --tasks <id1,id2>     # needs `appworld serve environment` running
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from appworld_client import AppWorldClient  # noqa: E402

OUT = _ROOT / "runs" / "appworld_gate"
EXPERIMENT = "reforge_gate"
MAX_STEPS, KEEP_OUTPUTS, OUT_CHARS, TASK_TOKEN_CAP = 20, 4, 1500, 150_000   # gate 0 ran 30 / 8 / 3000

SYSTEM = """You are an autonomous agent completing a task on behalf of a person (the supervisor) in a world of apps.
You act by writing Python code that runs in a persistent IPython shell; variables persist between steps.
App APIs are called as `apis.<app_name>.<api_name>(**params)`. To find out what exists, use
`apis.api_docs.show_app_descriptions()`, `apis.api_docs.show_api_descriptions(app_name=...)` and
`apis.api_docs.show_api_doc(app_name=..., api_name=...)`. Information about the supervisor (account
passwords, addresses, payment cards, profile) is available through the `apis.supervisor` app.
When the task is done, call `apis.supervisor.complete_task()`; if the task asks a question, pass the answer as
`apis.supervisor.complete_task(answer=...)`.
Each turn: think briefly, then output exactly one ```python``` code block. Print whatever you need to see."""


def _code(text: str) -> str:
    m = re.findall(r"```python\s*(.*?)```", text, flags=re.S)
    return m[-1].strip() if m else ""


def llm(model: str, messages: list[dict], thinking: bool) -> tuple[str, int, int]:
    from openai import OpenAI

    c = OpenAI(base_url=os.environ["VISION_LLM_BASE_URL"], api_key=os.environ["VISION_LLM_API_KEY"], timeout=300)
    r = c.chat.completions.create(model=model, messages=messages, temperature=0,
                                  extra_body={"enable_thinking": thinking})
    u = r.usage
    return r.choices[0].message.content or "", u.prompt_tokens, u.completion_tokens


def _window(history: list[dict]) -> list[dict]:
    """Keep the last KEEP_OUTPUTS execution outputs verbatim; older ones become a stub."""
    out, seen = [], 0
    for m in reversed(history):
        if m["role"] == "user" and m.get("_obs"):
            seen += 1
            if seen > KEEP_OUTPUTS:
                m = {"role": "user", "content": "[older output omitted]"}
        out.append({"role": m["role"], "content": m["content"]})
    return out[::-1]


def run_task(client: AppWorldClient, task_id: str, model: str, thinking: bool) -> dict:
    import appworld_gold

    d = OUT / task_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "steps.jsonl").unlink(missing_ok=True)
    # timeout_seconds=None: AppWorld's per-execution timeout uses signal.SIGALRM, which Windows lacks
    info = client.initialize(task_id, EXPERIMENT, timeout_seconds=None)
    sup = info["supervisor"]
    first = (f"Supervisor: {sup['first_name']} {sup['last_name']} (email {sup['email']}, phone {sup['phone_number']}).\n"
             f"Current date and time: {info['datetime']}.\nTask: {info['instruction']}")
    history = [{"role": "user", "content": first}]
    tok_p = tok_c = 0
    status, t0 = "max_steps", time.time()
    for step in range(1, MAX_STEPS + 1):
        text, p, c = llm(model, [{"role": "system", "content": SYSTEM}] + _window(history), thinking)
        tok_p, tok_c = tok_p + p, tok_c + c
        code = _code(text)
        output = client.execute(task_id, code) if code else "No ```python``` code block found in your reply."
        with open(d / "steps.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"step": step, "text": text, "code": code, "output": output[:20000],
                                 "prompt_tokens": p, "completion_tokens": c}, ensure_ascii=False) + "\n")
        history += [{"role": "assistant", "content": text},
                    {"role": "user", "content": "Output:\n" + output[:OUT_CHARS], "_obs": True}]
        if client.task_completed(task_id):
            status = "completed"
            break
        if tok_p + tok_c > TASK_TOKEN_CAP:
            status = "token_cap"
            break
    # ---- measurement only: the agent has stopped -------------------------------------------
    ev = appworld_gold.evaluate(client, task_id)
    client.save_logs(task_id)
    client.close(task_id)
    rec = {"task_id": task_id, "cfg": f"{MAX_STEPS}/{KEEP_OUTPUTS}/{OUT_CHARS}", "status": status, "steps": step, "prompt_tokens": tok_p, "completion_tokens": tok_c,
           "seconds": round(time.time() - t0, 1), "instruction": info["instruction"],
           "success": ev.get("success"), "num_tests": ev.get("num_tests"),
           "num_failures": len(ev.get("failures", []))}
    (d / "eval.json").write_text(json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(OUT / "records.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--model", default="qwen3.8-27b")
    ap.add_argument("--thinking", action="store_true")
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    args = ap.parse_args()
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
    OUT.mkdir(parents=True, exist_ok=True)
    client = AppWorldClient(args.url)
    print(client.ping())
    for t in [x.strip() for x in args.tasks.split(",") if x.strip()]:
        r = run_task(client, t, args.model, args.thinking)
        print(json.dumps(r, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
