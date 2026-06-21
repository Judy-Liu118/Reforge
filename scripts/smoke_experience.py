"""Smoke-test the Experience Memory Benchmark driver with a mock runner.

Validates the full pipeline (PairedCase → Driver → Report → Markdown)
without hitting the real LLM. Useful as a dry-run before launching the
real benchmark.

Usage:
    python scripts/smoke_experience.py
"""

from __future__ import annotations

import io
import sys
from types import SimpleNamespace

from reforge.benchmark.experience_driver import ExperienceDriver
from reforge.benchmark.experience_reporter import render_experience_markdown


def fake_state(outcome: str = "SUCCESS", retry: int = 0, score: float = 1.0):
    return SimpleNamespace(
        outcome_state=SimpleNamespace(task_outcome=outcome, final_answer=""),
        control_state=SimpleNamespace(retry_count=retry),
        semantic_state=SimpleNamespace(
            evaluation_result=SimpleNamespace(score=score)
        ),
    )


class FakeRunner:
    def __init__(self, state) -> None:
        self._state = state
        self._memory_substrate = None

    def run(self, request: str):
        return self._state


def main() -> int:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    # Simulated outcome per pair leg:
    #   cold.A   : RECOVERED (2 retries)        — typical first encounter
    #   cold.A'  : FAILED                       — no memory, no luck
    #   warm.A   : RECOVERED (2 retries)        — same as cold.A
    #   warm.A'  : SUCCESS (1 attempt)          — memory injected the fix
    states = []
    for _ in range(5):  # 5 pairs
        states.extend([
            fake_state("RECOVERED", retry=2, score=0.85),
            fake_state("FAILED", retry=3, score=0.20),
            fake_state("RECOVERED", retry=2, score=0.85),
            fake_state("SUCCESS", retry=0, score=1.0),
        ])

    idx = {"i": 0}

    def factory():
        s = states[idx["i"]]
        idx["i"] += 1
        return FakeRunner(s)

    driver = ExperienceDriver(runner_factory=factory)
    report = driver.run_all()

    print(render_experience_markdown(
        report, title="SMOKE: Experience Memory Benchmark (mocked LLM)"
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
