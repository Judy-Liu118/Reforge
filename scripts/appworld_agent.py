"""Minimal AppWorldAgent: the gate baseline's ReAct loop plus constraint cards.

A card is one atomic constraint {trigger, guardrail}. Cards go into the system
prompt as reasoning guidance — never as code to run and never spliced into an
execution output. The prompt is otherwise the gate baseline's, word for word.

Control side only: no gold here. Scoring happens in the runner after `run()`
returns (see run_appworld_hero.py).
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from appworld_baseline import SYSTEM
from appworld_client import AppWorldClient

CARD_HEADER = ("\n\nGuardrails learned from earlier verified failures in this environment "
               "(guidance for your reasoning, not code to copy):")


def system_prompt(cards: list[dict]) -> str:
    if not cards:
        return SYSTEM
    return SYSTEM + CARD_HEADER + "".join(f"\n- When: {c['trigger']}\n  Then: {c['guardrail']}" for c in cards)


def _code(text: str) -> str:
    m = re.findall(r"```python\s*(.*?)```", text, flags=re.S)
    return m[-1].strip() if m else ""


def llm(model: str, messages: list[dict], thinking: bool = False) -> tuple[str, str, int, int]:
    """→ (content, reasoning, prompt_tokens, completion_tokens incl. reasoning). Reasoning is logged, never fed back."""
    from openai import OpenAI

    c = OpenAI(base_url=os.environ["VISION_LLM_BASE_URL"], api_key=os.environ["VISION_LLM_API_KEY"], timeout=600)
    r = c.chat.completions.create(model=model, messages=messages, temperature=0, extra_body={"enable_thinking": thinking})
    m = r.choices[0].message
    return m.content or "", getattr(m, "reasoning_content", None) or "", r.usage.prompt_tokens, r.usage.completion_tokens


class AppWorldAgent:
    def __init__(self, client: AppWorldClient, model: str = "qwen3.8-27b", cards: list[dict] | None = None,
                 max_steps: int = 30, keep_outputs: int = 4, out_chars: int = 3000, thinking: bool = False) -> None:
        self.client, self.model, self.cards, self.thinking = client, model, list(cards or []), thinking
        self.max_steps, self.keep_outputs, self.out_chars = max_steps, keep_outputs, out_chars

    def _window(self, history: list[dict]) -> list[dict]:
        """Last keep_outputs execution outputs verbatim; older ones become a stub."""
        out, seen = [], 0
        for m in reversed(history):
            if m.get("_obs"):
                seen += 1
                if seen > self.keep_outputs:
                    m = {"role": "user", "content": "[older output omitted]"}
            out.append({"role": m["role"], "content": m["content"]})
        return out[::-1]

    def run(self, task_id: str, experiment: str, out_dir: Path) -> dict:
        """Run one episode; the world stays open (the runner scores, probes and closes it)."""
        out_dir.mkdir(parents=True, exist_ok=True)
        steps_path = out_dir / "steps.jsonl"
        steps_path.unlink(missing_ok=True)
        info = self.client.initialize(task_id, experiment, timeout_seconds=None)   # no SIGALRM on Windows
        sup = info["supervisor"]
        history = [{"role": "user", "content":
                    f"Supervisor: {sup['first_name']} {sup['last_name']} (email {sup['email']}, phone {sup['phone_number']}).\n"
                    f"Current date and time: {info['datetime']}.\nTask: {info['instruction']}"}]
        system = system_prompt(self.cards)
        tok_p = tok_c = 0
        status, done_step, t0 = "max_steps", None, time.time()
        for step in range(1, self.max_steps + 1):
            text, reasoning, p, c = llm(self.model, [{"role": "system", "content": system}] + self._window(history),
                                        self.thinking)
            tok_p, tok_c = tok_p + p, tok_c + c
            code = _code(text)
            output = self.client.execute(task_id, code) if code else "No ```python``` code block found in your reply."
            with open(steps_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"step": step, "text": text, "code": code, "output": output[:20000],
                                     "reasoning": reasoning, "prompt_tokens": p, "completion_tokens": c}, ensure_ascii=False) + "\n")
            history += [{"role": "assistant", "content": text},
                        {"role": "user", "content": "Output:\n" + output[:self.out_chars], "_obs": True}]
            if self.client.task_completed(task_id):
                status, done_step = "completed", step
                break
        return {"task_id": task_id, "instruction": info["instruction"], "status": status, "steps": step,
                "done_step": done_step, "prompt_tokens": tok_p, "completion_tokens": tok_c,
                "seconds": round(time.time() - t0, 1), "cards": [c.get("id") for c in self.cards],
                "thinking": self.thinking, "cfg": f"{self.max_steps}/{self.keep_outputs}/{self.out_chars}"}
