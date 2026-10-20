"""Gold side of AppWorld: the state-based hidden tests. Measurement only.

Call after the agent is done and before the task is closed; the result goes
to a record/shadow file, never back into the agent's prompt or control flow.
"""

from __future__ import annotations

from appworld_client import AppWorldClient


def evaluate(client: AppWorldClient, task_id: str) -> dict:
    """→ {success, num_tests, passes[...], failures[{requirement, trace}], ...} (AppWorld's TestTracker.to_dict)."""
    out = client._post("/evaluate", {"task_id": task_id, "suppress_errors": True, "report": False})
    return out


def report_text(client: AppWorldClient, task_id: str) -> str:
    return client._post("/evaluate", {"task_id": task_id, "suppress_errors": True, "report": True})
