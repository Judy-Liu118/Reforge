"""EvidenceAggregator — update hypothesis status from execution outputs.

Status rules:
  exit_code != 0                    → rejected   (confidence 0.1)
  empty / too-short output          → inconclusive (confidence 0.3)
  output contains error keywords    → inconclusive (confidence 0.4)
  non-empty clean output            → confirmed  (confidence 0.8)

Contradiction detection: confirmed/rejected hypothesis pairs with ≥3 shared words
in their hypothesis text are flagged as contradictory.
"""

from __future__ import annotations

from reforge.runtime.research.models import HypothesisRecord

_ERROR_KEYWORDS = frozenset({"error", "traceback", "exception", "failed", "not found"})
_MIN_EVIDENCE_LENGTH = 10
_CONTRADICTION_WORD_OVERLAP = 3


class EvidenceAggregator:
    """Heuristic evidence interpretation and contradiction detection."""

    def update(
        self,
        hypothesis: HypothesisRecord,
        stdout: str,
        exit_code: int,
    ) -> HypothesisRecord:
        evidence = stdout.strip()[:300] if stdout else ""
        lower_out = evidence.lower()
        has_error = any(kw in lower_out for kw in _ERROR_KEYWORDS)

        if exit_code != 0:
            status, confidence = "rejected", 0.1
        elif len(evidence) < _MIN_EVIDENCE_LENGTH:
            status, confidence = "inconclusive", 0.3
        elif has_error:
            status, confidence = "inconclusive", 0.4
        else:
            status, confidence = "confirmed", 0.8

        return hypothesis.model_copy(update={
            "status": status,
            "confidence": confidence,
            "evidence": [evidence] if evidence else [],
        })

    def detect_contradictions(
        self, hypotheses: list[HypothesisRecord]
    ) -> list[str]:
        """Return description strings for confirmed/rejected pairs with keyword overlap."""
        confirmed = [h for h in hypotheses if h.status == "confirmed"]
        rejected = [h for h in hypotheses if h.status == "rejected"]
        contradictions: list[str] = []
        for c in confirmed:
            c_words = set(c.hypothesis.lower().split())
            for r in rejected:
                r_words = set(r.hypothesis.lower().split())
                if len(c_words & r_words) >= _CONTRADICTION_WORD_OVERLAP:
                    contradictions.append(
                        f"Confirmed '{c.hypothesis[:60]}' contradicts rejected "
                        f"'{r.hypothesis[:60]}'"
                    )
        return contradictions
