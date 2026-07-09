"""Lightweight JSONL-based execution history storage. No database."""

from __future__ import annotations

from pathlib import Path

from reforge.paths import history_dir
from reforge.runtime.infrastructure.history.models import SessionRecord

_HISTORY_DIR = history_dir()
_HISTORY_FILE = _HISTORY_DIR / "history.jsonl"


class HistoryStorage:
    """Append-only JSONL storage for runtime sessions."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _HISTORY_FILE
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, record: SessionRecord) -> None:
        line = record.model_dump_json()
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def list_all(self) -> list[SessionRecord]:
        if not self._path.exists():
            return []
        records: list[SessionRecord] = []
        with open(self._path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(SessionRecord.model_validate_json(line))
        return list(reversed(records))  # newest first

    def find(self, session_id: str) -> SessionRecord | None:
        records = self.list_all()
        for r in records:
            if r.session_id == session_id:
                return r
        # Also try prefix match
        for r in records:
            if r.session_id.startswith(session_id):
                return r
        return None

    @property
    def file_path(self) -> Path:
        return self._path
