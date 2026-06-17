"""RunnerVerifier — default VerifierAgent backed by a RuntimeRunner.

Supports two ownership modes:

- **shared runner** (`runner=existing_runner`): single RuntimeRunner instance
  reused across calls — appropriate for serial ResearchSession execution.

- **per-call factory** (`runner_factory=callable`): a fresh RuntimeRunner is
  built for every `verify()` call — required for parallel orchestration so
  each worker has an independent `session_id` and (optionally) its own
  `MemorySubstrate` / `TrajectoryStore`.

When neither is provided, a single default RuntimeRunner is constructed lazily.
"""

from __future__ import annotations

from collections.abc import Callable

from reforge.runtime.agents.capability import AgentCapability, unrestricted
from reforge.runtime.orchestration.engine.runner import RuntimeRunner
from reforge.runtime.research.aggregator import EvidenceAggregator
from reforge.runtime.research.models import HypothesisRecord


class RunnerVerifier:
    """VerifierAgent implementation backed by RuntimeRunner + EvidenceAggregator.

    Accepts an optional `capability` declaring runtime-level isolation for
    this verifier instance. Defaults to unrestricted so legacy callers are
    unaffected.
    """

    def __init__(
        self,
        aggregator: EvidenceAggregator | None = None,
        runner: RuntimeRunner | None = None,
        runner_factory: Callable[[], RuntimeRunner] | None = None,
        capability: AgentCapability | None = None,
    ) -> None:
        if runner is not None and runner_factory is not None:
            raise ValueError(
                "Pass either runner or runner_factory, not both — they are "
                "mutually exclusive ownership modes."
            )
        self._aggregator = aggregator or EvidenceAggregator()
        self._runner = runner
        self._runner_factory = runner_factory
        self._capability = capability or unrestricted("verifier")

    @property
    def capability(self) -> AgentCapability:
        return self._capability

    def verify(self, hypothesis: HypothesisRecord) -> HypothesisRecord:
        runner = self._acquire_runner()
        state = runner.run(hypothesis.verification_request)
        stdout = ""
        exit_code = -1
        if state.execution_output:
            stdout = state.execution_output.stdout or ""
            exit_code = state.execution_output.exit_code
        return self._aggregator.update(hypothesis, stdout, exit_code)

    def _acquire_runner(self) -> RuntimeRunner:
        if self._runner_factory is not None:
            return self._runner_factory()
        if self._runner is None:
            self._runner = RuntimeRunner()
        return self._runner
