"""MemorySubstrate — unified protocol for runtime experience memory.

Allows planning, reflection, and trajectory modules to depend on a stable
interface instead of concrete storage classes. Swap backend (JSONL → SQLite
→ vector DB) by providing a different MemorySubstrate implementation.
"""

from __future__ import annotations

from collections import Counter
from typing import Protocol, runtime_checkable

from reforge.memory.models import MemoryRecord, MemoryType
from reforge.memory.retrieval import MemoryRetriever
from reforge.memory.store import MemoryStore


@runtime_checkable
class MemorySubstrate(Protocol):
    """Read/write interface for runtime experience memory."""

    def write(self, record: MemoryRecord) -> None: ...
    def recall(self, query: str, limit: int = 5) -> list[MemoryRecord]: ...
    def find_by_error(self, error_type: str, limit: int = 3) -> list[MemoryRecord]: ...
    def recall_for_planning(self, user_request: str, limit: int = 3) -> list[MemoryRecord]: ...
    def recall_repair_pattern(
        self, signature: dict, limit: int = 3,
    ) -> list[MemoryRecord]: ...


class CompositeMemorySubstrate:
    """Wraps MemoryStore + MemoryRetriever to satisfy MemorySubstrate.

    Composes existing storage and retrieval without replacing them.
    recall_for_planning returns only SUCCESS_PATTERN and RECOVERY records —
    failure-only records carry no useful repair signal for planners.
    """

    def __init__(
        self,
        store: MemoryStore | None = None,
        retriever: MemoryRetriever | None = None,
    ) -> None:
        self._store = store or MemoryStore()
        self._retriever = retriever or MemoryRetriever(self._store)

    def write(self, record: MemoryRecord) -> None:
        self._store.save(record)

    def recall(self, query: str, limit: int = 5) -> list[MemoryRecord]:
        return self._retriever.search(query, limit=limit)

    def find_by_error(self, error_type: str, limit: int = 3) -> list[MemoryRecord]:
        return self._retriever.find_by_error_type(error_type, limit=limit)

    def recall_for_planning(self, user_request: str, limit: int = 3) -> list[MemoryRecord]:
        """Return past successes and recoveries relevant to this request."""
        candidates = self._retriever.search(user_request, limit=limit * 2)
        useful = [
            r for r in candidates
            if r.memory_type in (MemoryType.SUCCESS_PATTERN, MemoryType.RECOVERY)
        ]
        return useful[:limit]

    def recall_repair_pattern(
        self, signature: dict, limit: int = 3,
    ) -> list[MemoryRecord]:
        """Return past RECOVERY records whose fingerprint matches *signature*.

        The cross-task repair-transfer signal: ask "what repair worked last
        time a failure looked structurally like this?" — ranked by overlap
        of typed fingerprint fields, not by query-string keyword overlap.
        """
        return self._retriever.find_by_signature(signature, limit=limit)

    def recall_distilled(self, user_request: str, limit: int = 1) -> list[MemoryRecord]:
        """Experiment read path: records carrying a distilled strategy card,
        ranked by request similarity — full scan, no index.

        Both polarities are eligible: a failure card's prevention strategy is
        the point of distilling failures at all, so the SUCCESS/RECOVERY
        filter of recall_for_planning does not apply here.
        """
        cards = [r for r in self._store.list_all() if r.distilled_strategy]
        query = _char_ngrams(user_request)
        cards.sort(key=lambda r: _cosine(query, _char_ngrams(r.user_request)), reverse=True)
        return cards[:limit]


def _char_ngrams(text: str, n: int = 2) -> Counter:
    # Character n-grams rather than whitespace tokens: the requests are
    # Chinese with no word boundaries. Lexical stand-in for an embedding.
    t = "".join(text.lower().split())
    return Counter(t[i:i + n] for i in range(max(len(t) - n + 1, 0)))


def _cosine(a: Counter, b: Counter) -> float:
    dot = sum(v * b[k] for k, v in a.items())
    na = sum(v * v for v in a.values()) ** 0.5
    nb = sum(v * v for v in b.values()) ** 0.5
    return dot / (na * nb) if na and nb else 0.0
