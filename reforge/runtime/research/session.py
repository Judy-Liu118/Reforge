"""ResearchSession — iterative investigation loop.

Each round:
  1. PlannerAgent generates 2-3 hypotheses (informed by prior findings)
  2. HypothesisRanker sorts candidates by relevance to confirmed findings
  3. Each hypothesis is verified by a VerifierAgent — either sequentially
     (default) or in parallel via ResearchOrchestrator
  4. Confirmed evidence feeds into the next round's context

Terminates when (whichever comes first after round 2):
  - max_rounds reached
  - all hypotheses resolved (no pending/inconclusive)
  - confirmed_ratio ≥ confirmed_exit_threshold (adaptive exit)
"""

from __future__ import annotations

from collections.abc import Iterator

from reforge.runtime.agents.role import (
    PlannerAgent,
    SynthesizerAgent,
    VerifierAgent,
)
from reforge.runtime.agents.synthesizer import DefaultSynthesizer
from reforge.runtime.agents.verifier import RunnerVerifier
from reforge.runtime.orchestration.engine.runner import RuntimeRunner
from reforge.runtime.research.aggregator import EvidenceAggregator
from reforge.runtime.research.models import (
    HypothesisRecord,
    ResearchResult,
    ResearchRound,
)
from reforge.runtime.research.orchestrator import ResearchOrchestrator
from reforge.runtime.research.planner import ResearchPlanner
from reforge.runtime.research.ranker import HypothesisRanker
from reforge.runtime.infrastructure.trajectory.store import TrajectoryStore

_DEFAULT_MAX_ROUNDS = 3
_MAX_HYPOTHESES_PER_ROUND = 3
_DEFAULT_CONFIRMED_EXIT_THRESHOLD = 0.7
_DEFAULT_PARALLEL_WORKERS = 4


class ResearchSession:
    """Multi-round hypothesis → verify → evidence loop with adaptive exit.

    Accepts injectable agents (planner / verifier / synthesizer); when none
    are given, builds the standard adapters over RuntimeRunner +
    EvidenceAggregator + ResearchPlanner.

    `parallel_verification=True` routes each round's hypotheses through
    `ResearchOrchestrator` so independent verifications run concurrently.
    """

    def __init__(
        self,
        planner: PlannerAgent | None = None,
        aggregator: EvidenceAggregator | None = None,
        runner: RuntimeRunner | None = None,
        ranker: HypothesisRanker | None = None,
        verifier: VerifierAgent | None = None,
        synthesizer: SynthesizerAgent | None = None,
        max_rounds: int = _DEFAULT_MAX_ROUNDS,
        confirmed_exit_threshold: float = _DEFAULT_CONFIRMED_EXIT_THRESHOLD,
        trajectory_store: TrajectoryStore | None = None,
        parallel_verification: bool = False,
        max_workers: int = _DEFAULT_PARALLEL_WORKERS,
    ) -> None:
        self._planner = planner or ResearchPlanner()
        self._aggregator = aggregator or EvidenceAggregator()
        self._ranker = ranker or HypothesisRanker()
        self._max_rounds = max_rounds
        self._confirmed_exit_threshold = confirmed_exit_threshold
        self._verifier = verifier or RunnerVerifier(
            aggregator=self._aggregator,
            runner=runner if runner is not None else RuntimeRunner(
                trajectory_store=trajectory_store
            ),
        )
        self._synthesizer = synthesizer or DefaultSynthesizer(
            aggregator=self._aggregator
        )
        self._parallel = parallel_verification
        self._orchestrator = (
            ResearchOrchestrator(verifier=self._verifier, max_workers=max_workers)
            if parallel_verification
            else None
        )

    def run(self, question: str, question_context: str = "") -> ResearchResult:
        all_rounds: list[ResearchRound] = []
        all_hypotheses: list[HypothesisRecord] = []
        prior_findings: list[str] = []

        for round_num in range(1, self._max_rounds + 1):
            outcome = self._run_round(
                round_num,
                question,
                question_context,
                prior_findings,
                all_hypotheses,
            )
            if outcome is None:
                break
            ranked, verified, round_record = outcome
            all_hypotheses.extend(verified)
            prior_findings.extend(round_record.new_findings)
            all_rounds.append(round_record)

            if round_num >= 2 and _should_exit(
                all_hypotheses, self._confirmed_exit_threshold
            ):
                break

        synthesis = self._synthesizer.synthesize(question, all_hypotheses)
        return ResearchResult(
            question=question,
            rounds=all_rounds,
            final_hypotheses=all_hypotheses,
            conclusion=synthesis.conclusion,
            contradictions_detected=synthesis.contradictions,
            total_rounds=len(all_rounds),
        )

    def stream(
        self, question: str, question_context: str = ""
    ) -> Iterator[tuple[int, HypothesisRecord, HypothesisRecord]]:
        """Yield (round, original, updated) tuples as hypotheses get verified.

        In parallel mode, all hypotheses of a round are batch-verified, then
        yielded together — there is no per-hypothesis streaming inside a round.
        prior_findings update happens AFTER the full round is yielded, so the
        next round sees the same context as `run()`.
        """
        prior_findings: list[str] = []
        all_hypotheses: list[HypothesisRecord] = []

        for round_num in range(1, self._max_rounds + 1):
            outcome = self._run_round(
                round_num,
                question,
                question_context,
                prior_findings,
                all_hypotheses,
            )
            if outcome is None:
                break
            ranked, verified, round_record = outcome
            for original, updated in zip(ranked, verified):
                all_hypotheses.append(updated)
                yield round_num, original, updated
            prior_findings.extend(round_record.new_findings)

            if round_num >= 2 and _should_exit(
                all_hypotheses, self._confirmed_exit_threshold
            ):
                break

    def _run_round(
        self,
        round_num: int,
        question: str,
        question_context: str,
        prior_findings: list[str],
        all_hypotheses: list[HypothesisRecord],
    ) -> tuple[list[HypothesisRecord], list[HypothesisRecord], ResearchRound] | None:
        """Plan + rank + verify one round. Returns None when planner is exhausted."""
        plan = self._planner.plan(question, prior_findings, context=question_context)
        if not plan.hypotheses:
            return None

        confirmed_so_far = [h for h in all_hypotheses if h.status == "confirmed"]
        ranked = self._ranker.rank(
            plan.hypotheses[:_MAX_HYPOTHESES_PER_ROUND],
            prior_confirmed=confirmed_so_far,
        )
        ranked = [h.model_copy(update={"round_number": round_num}) for h in ranked]

        verified = self._verify_round(ranked)
        round_record = ResearchRound(
            round_number=round_num,
            hypotheses_tested=[h.hypothesis_id for h in verified],
            new_findings=[
                h.evidence[0][:200]
                for h in verified
                if h.status == "confirmed" and h.evidence
            ],
            contradictions=self._aggregator.detect_contradictions(verified),
        )
        return ranked, verified, round_record

    def _verify_round(
        self, hypotheses: list[HypothesisRecord]
    ) -> list[HypothesisRecord]:
        """Verify a round's hypotheses serially or in parallel."""
        if not hypotheses:
            return []
        if self._orchestrator is not None:
            return self._orchestrator.verify_batch(hypotheses)
        return [self._verifier.verify(h) for h in hypotheses]


def _should_exit(
    all_hypotheses: list[HypothesisRecord],
    threshold: float,
) -> bool:
    """Return True when all resolved OR confirmed ratio ≥ threshold."""
    if not all_hypotheses:
        return False
    unresolved = [h for h in all_hypotheses if h.status in ("pending", "inconclusive")]
    if not unresolved:
        return True
    confirmed = sum(1 for h in all_hypotheses if h.status == "confirmed")
    return confirmed / len(all_hypotheses) >= threshold
