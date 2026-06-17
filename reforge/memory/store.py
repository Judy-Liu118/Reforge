"""Local JSON-based memory store. No database, no vector DB."""

from __future__ import annotations

import json
from pathlib import Path

from reforge.memory.models import MemoryRecord
from reforge.paths import memory_json_dir

_MEMORY_DIR = memory_json_dir()


class MemoryStore:
    """Append-only JSON file store for runtime experience memory."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self._dir = base_dir if base_dir is not None else _MEMORY_DIR
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, record: MemoryRecord) -> None:
        """Append record to the appropriate memory type file."""
        path = self._path_for(record.memory_type)
        records = self._load_file(path)
        records.append(record.model_dump())
        path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")

    def list_all(self, mem_type: str | None = None) -> list[MemoryRecord]:
        """List all records, optionally filtered by type."""
        if mem_type:
            return [MemoryRecord.model_validate(r) for r in self._read_type(mem_type)]
        all_records: list[MemoryRecord] = []
        for mt in ("RECOVERY", "FAILURE", "SUCCESS_PATTERN"):
            all_records.extend(MemoryRecord.model_validate(r) for r in self._read_type(mt))
        return all_records

    def _read_type(self, mem_type: str) -> list[dict]:
        return self._load_file(self._path_for_type(mem_type))

    def _path_for(self, mem_type: str) -> Path:
        return self._path_for_type(mem_type)

    def _path_for_type(self, mem_type: str) -> Path:
        mapping = {
            "RECOVERY": "recovery.json",
            "FAILURE": "failures.json",
            "SUCCESS_PATTERN": "success_patterns.json",
        }
        filename = mapping.get(mem_type, f"{mem_type.lower()}.json")
        return self._dir / filename

    @staticmethod
    def _load_file(path: Path) -> list[dict]:
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, ValueError):
            return []
