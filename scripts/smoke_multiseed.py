"""Smoke-test the multi-seed Experience Memory Benchmark (mocked LLM).

Runs 5 pairs × 5 seeds × 4 legs = 100 fake states, then renders the
multi-seed markdown so the layout can be sanity-checked without burning
LLM credit.
"""

from __future__ import annotations

import io
import sys
from types import SimpleNamespace

from reforge.benchmark.experience_multiseed import MultiSeedDriver
from reforge.benchmark.experience_multiseed_reporter import (
    render_multiseed_markdown,
)


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

    # Scripted scenario: across 5 seeds, P1-P4 always have cold-fail/warm-pass
    # (clean transfer signal), P5 is noisy — cold-fail in 3 of 5 seeds.
    # Used to verify the reporter's CI-excludes-zero verdict logic.
    n_seeds = 5
    pairs_per_seed = 5
    states: list = []
    for seed_idx in range(n_seeds):
        for pair_idx in range(pairs_per_seed):
            # cold.A
            states.append(fake_state("RECOVERED", retry=2, score=0.85))
            # cold.A'
            if pair_idx < 4:
                states.append(fake_state("FAILED", retry=3, score=0.2))
            else:
                # P5: noisy — fail seeds 1-3, pass seeds 4-5
                if seed_idx < 3:
                    states.append(fake_state("FAILED", retry=3, score=0.2))
                else:
                    states.append(fake_state("SUCCESS", retry=0, score=1.0))
            # warm.A (seeding)
            states.append(fake_state("RECOVERED", retry=2, score=0.85))
            # warm.A'
            states.append(fake_state("SUCCESS", retry=0, score=1.0))

    idx = {"i": 0}

    def factory():
        s = states[idx["i"]]
        idx["i"] += 1
        return FakeRunner(s)

    driver = MultiSeedDriver(runner_factory=factory)
    report = driver.run_all(n_seeds)

    print(render_multiseed_markdown(
        report,
        title="SMOKE: Experience Memory Benchmark Multi-Seed (mocked LLM)",
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
