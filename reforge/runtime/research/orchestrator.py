"""ResearchOrchestrator — parallel hypothesis verification.

A round of independent hypotheses can be verified concurrently because each
verification is a separate execution session (`RuntimeRunner.run`) with no
shared mutable state. The orchestrator delegates each hypothesis to a
`VerifierAgent`; workers run in a `ThreadPoolExecutor` because the underlying
LangGraph stream is synchronous.

Worker isolation contract (P17.4): when the verifier is built with
`RunnerVerifier(runner_factory=...)`, every worker thread invokes the factory
and gets its own `RuntimeRunner` — independent `session_id`, independently
injectable `MemorySubstrate` and `TrajectoryStore`.

Errors raised inside a worker are caught and surface as an `inconclusive`
hypothesis so a single failing verification does not abort the whole round.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from reforge.runtime.agents.role import VerifierAgent
from reforge.runtime.research.models import HypothesisRecord

_DEFAULT_MAX_WORKERS = 4


class ResearchOrchestrator:
    """Verifies a batch of independent hypotheses in parallel."""

    def __init__(
        self,
        verifier: VerifierAgent,
        max_workers: int = _DEFAULT_MAX_WORKERS,
    ) -> None:
        self._verifier = verifier
        self._max_workers = max(1, max_workers)

    def verify_batch(
        self, hypotheses: list[HypothesisRecord]
    ) -> list[HypothesisRecord]:
        """Verify every hypothesis concurrently; preserve input order in result."""
        if not hypotheses:
            return []
        if len(hypotheses) == 1:
            return [self._verify_safe(hypotheses[0])]

        workers = min(len(hypotheses), self._max_workers)
        results: dict[int, HypothesisRecord] = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(self._verify_safe, h): i
                for i, h in enumerate(hypotheses)
            }
            for future in as_completed(futures):
                idx = futures[future]
                results[idx] = future.result()

        return [results[i] for i in range(len(hypotheses))]

    def _verify_safe(self, hypothesis: HypothesisRecord) -> HypothesisRecord:
        """Run a single verification; convert exceptions into inconclusive."""
        try:
            return self._verifier.verify(hypothesis)
        except Exception as exc:  # noqa: BLE001 — orchestrator boundary
            return hypothesis.model_copy(
                update={
                    "status": "inconclusive",
                    "confidence": 0.0,
                    "evidence": [f"verification error: {exc}"[:300]],
                }
            )
