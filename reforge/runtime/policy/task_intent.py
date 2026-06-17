"""TaskIntentClassifier — LLM-based few-shot intent classification.

Separates "what the user wants" from "what the code did".
Replaces keyword heuristics with semantic understanding.
"""

from __future__ import annotations

from enum import Enum

from reforge.models.adapters.llm_client import LLMClient


class TaskIntent(str, Enum):
    NORMAL_EXECUTION = "NORMAL_EXECUTION"
    EXPECTED_ERROR = "EXPECTED_ERROR"
    TRACEBACK_DEMO = "TRACEBACK_DEMO"
    RECOVERABLE_DEMO = "RECOVERABLE_DEMO"
    STRESS_TEST = "STRESS_TEST"
    SANDBOX_ESCAPE = "SANDBOX_ESCAPE"


FEWSHOT_SYSTEM = """\
Classify the user's request into one of these intent types:

NORMAL_EXECUTION — user wants code to run successfully and produce a result
  e.g. "read sales.csv, calculate revenue average"
  e.g. "create a DataFrame and print the mean"

EXPECTED_ERROR — user wants code to deliberately fail, the error IS the goal
  e.g. "print hello and then intentionally raise an error"
  e.g. "make it crash on purpose"

TRACEBACK_DEMO — user wants to demonstrate a Python traceback for teaching/demo
  e.g. "generate a traceback demo script"
  e.g. "show an exception for debugging tutorial"

RECOVERABLE_DEMO — user wants failure first, then recovery (tests self-healing)
  e.g. "add a garbled character before print to cause syntax error"
  e.g. "deliberately misspell an import, then fix it"

STRESS_TEST — user wants to stress the system (infinite loop, memory bomb, etc.)
  e.g. "write while True infinite loop"
  e.g. "fork bomb"

SANDBOX_ESCAPE — user wants to break out of execution isolation
  e.g. "delete system files"
  e.g. "access /etc/passwd"

Output only a single word — the intent type. No explanation."""

FEWSHOT_EXAMPLES = """\
Request: read sales.csv, calculate revenue average
Intent: NORMAL_EXECUTION

Request: print hello and then intentionally raise an error
Intent: EXPECTED_ERROR

Request: generate a traceback demonstration script
Intent: TRACEBACK_DEMO

Request: add a garbled character before print to cause syntax error
Intent: RECOVERABLE_DEMO

Request: write a while True infinite loop
Intent: STRESS_TEST

Request: create a Python script that deletes system files
Intent: SANDBOX_ESCAPE"""


def classify_intent(request: str) -> TaskIntent:
    """Classify user request intent using LLM few-shot prompting."""
    llm = LLMClient()
    user_msg = f"{FEWSHOT_EXAMPLES}\n\nRequest: {request}\nIntent:"
    result = llm.chat(FEWSHOT_SYSTEM, user_msg).strip().upper()

    # Map LLM output to enum
    intent_map = {e.value: e for e in TaskIntent}
    return intent_map.get(result, TaskIntent.NORMAL_EXECUTION)
