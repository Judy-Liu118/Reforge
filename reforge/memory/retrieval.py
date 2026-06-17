"""Lightweight memory retrieval — keyword overlap + error_type match + scoring.

No embedding. No vector DB. Pure heuristic ranking.
"""

from __future__ import annotations

from reforge.memory.models import MemoryRecord
from reforge.memory.store import MemoryStore


class MemoryRetriever:
    """Query past runtime experiences by keyword, error type, or task similarity."""

    def __init__(self, store: MemoryStore | None = None) -> None:
        self._store = store or MemoryStore()

    def search(self, query: str, limit: int = 5) -> list[MemoryRecord]:
        """Search memory for records relevant to *query*."""
        all_records = self._store.list_all()
        if not all_records:
            return []

        scored: list[tuple[float, MemoryRecord]] = []
        query_lower = query.lower()
        query_words = set(query_lower.split())

        for rec in all_records:
            score = self._score(rec, query_lower, query_words)
            if score > 0:
                scored.append((score, rec))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [rec for _, rec in scored[:limit]]

    def find_by_error_type(self, error_type: str, limit: int = 3) -> list[MemoryRecord]:
        """Find recovery records matching a specific error type."""
        records = self._store.list_all("RECOVERY")
        matches = [r for r in records if error_type.lower() in r.error_type.lower()]
        return matches[:limit]

    def find_by_signature(
        self, signature: dict, limit: int = 3,
    ) -> list[MemoryRecord]:
        """Return RECOVERY records whose `problem_signature` structurally
        overlaps with *signature*.

        Where `search()` matches against a free-form text query and
        `find_by_error_type()` does substring matching on a single field,
        this ranks past *recoveries* by how many fingerprint fields of the
        current failure also appear in the historical record. Concretely:
        the same `error_class` + `missing_key` + `domain` will score much
        higher than just a shared `error_class`. Use this to ask "what
        repair worked last time we saw a failure structurally like this
        one?" — the cross-task transfer signal.

        Empty / unknown signatures degrade gracefully to an empty result
        rather than a noisy keyword fallback.
        """
        if not signature:
            return []
        records = self._store.list_all("RECOVERY")
        if not records:
            return []
        scored = [
            (s, r) for r in records
            if (s := _score_signature(signature, r.problem_signature or {})) > 0
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:limit]]

    def _score(
        self, rec: MemoryRecord, query_lower: str, query_words: set[str],
    ) -> float:
        score = 0.0
        sig = rec.problem_signature or {}

        # --- Structured fingerprint matching (highest precision) ---
        # error_class / error_type — precise error name match
        ec = sig.get("error_class") or sig.get("error_type") or rec.error_type or ""
        if ec and ec.lower() in query_lower:
            score += 5.0
        elif ec and query_lower and any(w in ec.lower() for w in query_words if len(w) > 3):
            score += 2.0

        # missing_module — ImportError target (e.g. "pandas" in query)
        mm = sig.get("missing_module", "")
        if mm and mm.lower() in query_lower:
            score += 5.0

        # missing_key — KeyError target (e.g. "sales" in query)
        mk = sig.get("missing_key", "")
        if mk and mk.lower() in query_lower:
            score += 4.0

        # missing_file — FileNotFoundError target
        mf = sig.get("missing_file", "")
        if mf and mf.lower() in query_lower:
            score += 3.0

        # undefined_name — NameError target
        un = sig.get("undefined_name", "")
        if un and un.lower() in query_lower:
            score += 3.0

        # domain (pandas, numpy, filesystem, python)
        domain = sig.get("domain", "")
        if domain and domain in query_lower:
            score += 3.0

        # root_cause (legacy compat)
        rc = sig.get("root_cause", "")
        if rc and rc.replace("_", " ") in query_lower:
            score += 1.0

        # --- Keyword-based scoring (lower weight) ---
        req_words = set(rec.user_request.lower().split())
        score += len(query_words & req_words) * 0.3

        tag_overlap = len(set(t.lower() for t in rec.tags) & query_words)
        score += tag_overlap * 0.5

        if rec.reflection_summary and any(
            w in rec.reflection_summary.lower() for w in query_words if len(w) > 2
        ):
            score += 0.5

        if rec.recovery_action and query_lower in rec.recovery_action.lower():
            score += 1.0

        return score


# ---------------------------------------------------------------------------
# Structural-fingerprint scorer — shared with SqliteMemorySubstrate so both
# backends rank identically.
# ---------------------------------------------------------------------------

# Field weights — higher = more specific. A shared `missing_module` is
# stronger evidence that the same repair will apply than a shared `domain`.
_SIGNATURE_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("error_class",    5.0),
    ("error_type",     5.0),  # legacy field name
    ("missing_module", 5.0),
    ("missing_key",    4.0),
    ("missing_file",   4.0),
    ("undefined_name", 3.0),
    ("domain",         2.0),
    ("root_cause",     1.0),
)


def _score_signature(query_sig: dict, candidate_sig: dict) -> float:
    """Sum of weights for matching, non-empty fingerprint fields."""
    score = 0.0
    for field, weight in _SIGNATURE_WEIGHTS:
        q = (query_sig.get(field) or "").lower()
        c = (candidate_sig.get(field) or "").lower()
        if q and c and q == c:
            score += weight
    return score


def format_memory_results(records: list[MemoryRecord]) -> str:
    """Format retrieved memory records for CLI display."""
    if not records:
        return "No relevant memory found."

    lines = [f"Found {len(records)} relevant memory record(s):", ""]
    for i, rec in enumerate(records, 1):
        lines.append(f"  [{i}] {rec.memory_type} | {rec.error_type or 'N/A'}")
        lines.append(f"      Session: {rec.session_id}")
        lines.append(f"      Request: {rec.user_request[:80]}")
        if rec.reflection_summary:
            lines.append(f"      Reflection: {rec.reflection_summary[:80]}")
        if rec.recovery_action:
            lines.append(f"      Recovery: {rec.recovery_action[:80]}")
        lines.append(f"      Outcome: {rec.outcome} | Retries: {rec.retry_count}")
        lines.append(f"      Tags: {', '.join(rec.tags)}")
        lines.append("")
    return "\n".join(lines)
