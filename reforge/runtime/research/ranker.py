"""HypothesisRanker — sort hypothesis candidates by relevance to confirmed findings.

Scoring per hypothesis:
  +2.0 per word overlapping with any confirmed hypothesis text
  +1.0 if hypothesis has non-empty rationale
  -3.0 if overlap ratio > 0.8 (near-duplicate of already-confirmed text)

Hypotheses with higher scores are tested first, so investigation focuses
on areas most likely to yield new confirmed findings.
"""

from __future__ import annotations

from reforge.runtime.research.models import HypothesisRecord

_CONFIRMED_WORD_SCORE = 2.0
_RATIONALE_BONUS = 1.0
_DUPLICATE_PENALTY = -3.0
_DUPLICATE_THRESHOLD = 0.8


class HypothesisRanker:
    """Rank hypothesis candidates to test the most promising ones first."""

    def rank(
        self,
        candidates: list[HypothesisRecord],
        prior_confirmed: list[HypothesisRecord] | None = None,
    ) -> list[HypothesisRecord]:
        """Return candidates sorted by relevance score, highest first."""
        if not candidates:
            return []
        confirmed_words = _words_from(prior_confirmed or [])
        scored = [(_score(h, confirmed_words), h) for h in candidates]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [h for _, h in scored]


def _words_from(hypotheses: list[HypothesisRecord]) -> set[str]:
    words: set[str] = set()
    for h in hypotheses:
        words.update(h.hypothesis.lower().split())
    return words


def _score(hyp: HypothesisRecord, confirmed_words: set[str]) -> float:
    hyp_words = set(hyp.hypothesis.lower().split())
    if not hyp_words:
        return 0.0

    overlap = len(hyp_words & confirmed_words)

    # Near-duplicate: return fixed low score, discarding any overlap bonus
    if confirmed_words and overlap / len(hyp_words) > _DUPLICATE_THRESHOLD:
        return _DUPLICATE_PENALTY

    score = overlap * _CONFIRMED_WORD_SCORE
    if hyp.rationale:
        score += _RATIONALE_BONUS
    return score
