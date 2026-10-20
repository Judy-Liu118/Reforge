"""Thin httpx client for `appworld serve environment` (runs in its own Python 3.11 env).

AppWorld pins pydantic v1 / old cryptography, so it cannot share Reforge's
environment; Reforge talks to it over HTTP. The server holds ONE task world at a
time — tasks run sequentially.

Control-side only: initialize / execute / task_completed / close. Scoring
(`/evaluate`, hidden state-based tests = gold) lives in appworld_gold.py and is
never imported by anything that decides what the agent does.
"""

from __future__ import annotations

import httpx

DEFAULT_URL = "http://127.0.0.1:8000"


class AppWorldClient:
    def __init__(self, url: str = DEFAULT_URL, timeout: float = 300.0) -> None:
        self.url = url.rstrip("/")
        self._http = httpx.Client(timeout=timeout, trust_env=False)   # local server: never via a system proxy

    def _post(self, path: str, payload: dict):
        r = self._http.post(f"{self.url}{path}", json=payload)
        r.raise_for_status()
        return r.json()["output"]

    def ping(self) -> dict:
        r = self._http.get(f"{self.url}/")
        r.raise_for_status()
        return r.json()

    def initialize(self, task_id: str, experiment_name: str, **kw) -> dict:
        """→ {task_id, instruction, supervisor{first_name,last_name,email,phone_number}, datetime}."""
        return self._post("/initialize", {"task_id": task_id, "experiment_name": experiment_name, **kw})

    def execute(self, task_id: str, code: str) -> str:
        return self._post("/execute", {"task_id": task_id, "code": code})

    def task_completed(self, task_id: str) -> bool:
        return bool(self._post("/task_completed", {"task_id": task_id}))

    def save_logs(self, task_id: str) -> None:
        self._post("/save_logs", {"task_id": task_id})

    def close(self, task_id: str) -> None:
        self._post("/close", {"task_id": task_id})
