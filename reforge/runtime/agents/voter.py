"""VerifierVoter — consensus resolver for multi-verifier results.

Given N independently-verified copies of the same hypothesis, the voter
produces one authoritative HypothesisRecord:

  Voting rule (strict majority, > 50%):
    confirmed   if  #confirmed  > N/2
    rejected    if  #rejected   > N/2
    inconclusive otherwise (tie, split, or no clear winner)

  Confidence  = arithmetic mean of all individual confidences
  Evidence    = union of all individual evidence lists, order-preserving
                and deduplicated so no entry appears twice

The voter carries no I/O — it is a pure function over the input list and
is independently testable without any bus or runner involvement.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reforge.runtime.research.models import HypothesisRecord


class VerifierVoter:
    """Reduces a list of parallel verification results to one consensus record."""

    def vote(
        self,
        results: list[HypothesisRecord],
        original: HypothesisRecord | None = None,
    ) -> HypothesisRecord:
        """Return the consensus HypothesisRecord for *results*.

        If *results* is empty, returns an inconclusive record. When *original*
        is provided (callers that know the hypothesis identity, e.g. BusVerifier),
        its identity fields are preserved so downstream consumers can still
        match the result to the original hypothesis.
        Otherwise the first element supplies the base identity fields.
        """
        from reforge.runtime.research.models import HypothesisRecord as HR

        if not results:
            if original is not None:
                return original.model_copy(update={
                    "status": "inconclusive",
                    "confidence": 0.0,
                    "evidence": [],
                })
            return HR(status="inconclusive", confidence=0.0)

        counts = Counter(r.status for r in results)
        total = len(results)
        confirmed = counts.get("confirmed", 0)
        rejected = counts.get("rejected", 0)

        if confirmed > total / 2:
            final_status = "confirmed"
        elif rejected > total / 2:
            final_status = "rejected"
        else:
            final_status = "inconclusive"

        avg_confidence = min(1.0, sum(r.confidence for r in results) / total)

        seen: set[str] = set()
        aggregated_evidence: list[str] = []
        for r in results:
            for e in r.evidence:
                if e not in seen:
                    seen.add(e)
                    aggregated_evidence.append(e)

        return results[0].model_copy(update={
            "status": final_status,
            "confidence": avg_confidence,
            "evidence": aggregated_evidence,
        })
