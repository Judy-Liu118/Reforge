"""ResearchMemory — cross-session hypothesis pattern recall.

Reads from ResearchStore to surface confirmed/rejected patterns from past
similar research sessions. Used by ResearchPlanner to avoid re-testing known
patterns and to build on prior conclusions.

Does not maintain a separate storage file — it is a query view over ResearchStore.
"""

from __future__ import annotations

from reforge.runtime.research.store import ResearchStore

_MAX_PATTERN_LENGTH = 60
_MAX_CONFIRMED_PATTERNS = 3
_MAX_REJECTED_PATTERNS = 2


class ResearchMemory:
    """Extracts hypothesis patterns from past research for new session planning."""

    def __init__(self, store: ResearchStore | None = None) -> None:
        self._store = store or ResearchStore()

    def recall_patterns(self, question: str, limit: int = 3) -> str:
        """Return formatted confirmed/rejected patterns from similar past research.

        Returns empty string when no relevant history exists (graceful degradation).
        """
        similar = self._store.find_by_question(question, limit=limit)
        if not similar:
            return ""

        lines: list[str] = []
        for r in similar:
            confirmed = [
                h.hypothesis[:_MAX_PATTERN_LENGTH]
                for h in r.final_hypotheses
                if h.status == "confirmed"
            ]
            rejected = [
                h.hypothesis[:_MAX_PATTERN_LENGTH]
                for h in r.final_hypotheses
                if h.status == "rejected"
            ]
            if not confirmed and not rejected:
                continue

            lines.append(f"Similar: '{r.question[:60]}'")
            if confirmed:
                lines.append(
                    "  Confirmed: " + "; ".join(confirmed[:_MAX_CONFIRMED_PATTERNS])
                )
            if rejected:
                lines.append(
                    "  Rejected:  " + "; ".join(rejected[:_MAX_REJECTED_PATTERNS])
                )

        return "\n".join(lines) if lines else ""
