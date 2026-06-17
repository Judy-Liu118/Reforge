"""ResearchStore — append-only JSONL persistence for ResearchResult."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from reforge.paths import research_path
from reforge.runtime.research.models import ResearchResult

_DEFAULT_PATH = research_path()


class ResearchStore:
    """Persist and query research session results.

    Each line is a ResearchResult JSON object.
    find_by_question() ranks results by keyword overlap with the query.
    find_by_id() returns an exact match on research_id.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _DEFAULT_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, result: ResearchResult) -> None:
        if not result.timestamp:
            result = result.model_copy(
                update={"timestamp": datetime.now(timezone.utc).isoformat()}
            )
        with self._path.open("a", encoding="utf-8") as f:
            f.write(result.model_dump_json() + "\n")

    def list_all(self) -> list[ResearchResult]:
        if not self._path.exists():
            return []
        results = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    results.append(ResearchResult.model_validate_json(line))
                except Exception:
                    continue
        return results

    def find_by_id(self, research_id: str) -> ResearchResult | None:
        for r in self.list_all():
            if r.research_id == research_id:
                return r
        return None

    def find_by_question(self, query: str, limit: int = 5) -> list[ResearchResult]:
        if not query.strip():
            return []
        query_words = set(query.lower().split())
        scored: list[tuple[int, ResearchResult]] = []
        for result in self.list_all():
            overlap = len(query_words & set(result.question.lower().split()))
            if overlap > 0:
                scored.append((overlap, result))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:limit]]
