"""SQLite-backed MemorySubstrate — faster reads, indexed queries, single-file DB.

Drop-in replacement for CompositeMemorySubstrate.  Uses stdlib sqlite3 only.
Default path: data/memory/memory.db (next to the existing JSON files).

Scoring for recall() replicates MemoryRetriever._score() so retrieval quality
is identical regardless of which backend is in use.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from reforge.memory.models import MemoryRecord, MemoryType
from reforge.paths import memory_db_path

_DEFAULT_DB = memory_db_path()

_DDL = """
CREATE TABLE IF NOT EXISTS memory_records (
    memory_id          TEXT PRIMARY KEY,
    session_id         TEXT NOT NULL DEFAULT '',
    timestamp          TEXT NOT NULL DEFAULT '',
    memory_type        TEXT NOT NULL DEFAULT 'RECOVERY',
    user_request       TEXT NOT NULL DEFAULT '',
    error_type         TEXT NOT NULL DEFAULT '',
    reflection_summary TEXT NOT NULL DEFAULT '',
    recovery_action    TEXT NOT NULL DEFAULT '',
    outcome            TEXT NOT NULL DEFAULT '',
    retry_count        INTEGER NOT NULL DEFAULT 0,
    tags               TEXT NOT NULL DEFAULT '[]',
    problem_signature  TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_memory_type ON memory_records(memory_type);
CREATE INDEX IF NOT EXISTS idx_error_type  ON memory_records(error_type);
CREATE INDEX IF NOT EXISTS idx_timestamp   ON memory_records(timestamp DESC);
"""


def _row_to_record(row: sqlite3.Row) -> MemoryRecord:
    d = dict(row)
    d["tags"] = json.loads(d["tags"])
    d["problem_signature"] = json.loads(d["problem_signature"])
    return MemoryRecord.model_validate(d)


def _score(rec: MemoryRecord, query_lower: str, query_words: set[str]) -> float:
    """Keyword + fingerprint scoring — mirrors MemoryRetriever._score()."""
    score = 0.0
    sig = rec.problem_signature or {}

    ec = sig.get("error_class") or sig.get("error_type") or rec.error_type or ""
    if ec and ec.lower() in query_lower:
        score += 5.0
    elif ec and any(w in ec.lower() for w in query_words if len(w) > 3):
        score += 2.0

    mm = sig.get("missing_module", "")
    if mm and mm.lower() in query_lower:
        score += 5.0

    mk = sig.get("missing_key", "")
    if mk and mk.lower() in query_lower:
        score += 4.0

    mf = sig.get("missing_file", "")
    if mf and mf.lower() in query_lower:
        score += 3.0

    un = sig.get("undefined_name", "")
    if un and un.lower() in query_lower:
        score += 3.0

    domain = sig.get("domain", "")
    if domain and domain in query_lower:
        score += 3.0

    rc = sig.get("root_cause", "")
    if rc and rc.replace("_", " ") in query_lower:
        score += 1.0

    req_words = set(rec.user_request.lower().split())
    score += len(query_words & req_words) * 0.3

    tag_overlap = len({t.lower() for t in rec.tags} & query_words)
    score += tag_overlap * 0.5

    if rec.reflection_summary and any(
        w in rec.reflection_summary.lower() for w in query_words if len(w) > 2
    ):
        score += 0.5

    if rec.recovery_action and query_lower in rec.recovery_action.lower():
        score += 1.0

    return score


class SqliteMemorySubstrate:
    """MemorySubstrate backed by a local SQLite database.

    Thread-safe: each call acquires a per-instance lock and uses
    check_same_thread=False so the connection can be shared across threads.
    WAL mode is enabled for better concurrent read performance.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        path = Path(db_path) if db_path else _DEFAULT_DB
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_DDL)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.commit()

    # ------------------------------------------------------------------
    # MemorySubstrate protocol

    def write(self, record: MemoryRecord) -> None:
        row = (
            record.memory_id,
            record.session_id,
            record.timestamp,
            record.memory_type.value,
            record.user_request,
            record.error_type,
            record.reflection_summary,
            record.recovery_action,
            record.outcome,
            record.retry_count,
            json.dumps(record.tags, ensure_ascii=False),
            json.dumps(record.problem_signature, ensure_ascii=False),
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO memory_records VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                row,
            )
            self._conn.commit()

    def recall(self, query: str, limit: int = 5) -> list[MemoryRecord]:
        """Return up to *limit* records most relevant to *query*."""
        query_lower = query.lower()
        query_words = set(query_lower.split())

        # Pre-filter: any record where error_type, user_request, or
        # reflection_summary contains at least one query word.
        word_conditions = " OR ".join(
            "error_type LIKE ? OR user_request LIKE ? OR reflection_summary LIKE ?"
            for _ in query_words
        )
        params: list[Any] = []
        for w in query_words:
            like = f"%{w}%"
            params += [like, like, like]

        sql = (
            f"SELECT * FROM memory_records WHERE {word_conditions}"
            if word_conditions
            else "SELECT * FROM memory_records"
        )

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()

        if not rows:
            return []

        records = [_row_to_record(r) for r in rows]
        scored = [(s, r) for r in records if (s := _score(r, query_lower, query_words)) > 0]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:limit]]

    def find_by_error(self, error_type: str, limit: int = 3) -> list[MemoryRecord]:
        """Return RECOVERY records whose error_type contains *error_type*."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memory_records "
                "WHERE memory_type = 'RECOVERY' AND error_type LIKE ? "
                "ORDER BY timestamp DESC LIMIT ?",
                (f"%{error_type}%", limit),
            ).fetchall()
        return [_row_to_record(r) for r in rows]

    def recall_for_planning(self, user_request: str, limit: int = 3) -> list[MemoryRecord]:
        """Return recent SUCCESS_PATTERN + RECOVERY records relevant to *user_request*."""
        candidates = self._recall_typed(
            user_request, ("RECOVERY", "SUCCESS_PATTERN"), limit=limit * 2
        )
        return candidates[:limit]

    def recall_repair_pattern(
        self, signature: dict, limit: int = 3,
    ) -> list[MemoryRecord]:
        """Return RECOVERY records whose fingerprint structurally overlaps *signature*.

        Mirrors `CompositeMemorySubstrate.recall_repair_pattern` so callers
        get identical results regardless of backend. Implementation reuses
        the shared scorer from `reforge.memory.retrieval`.
        """
        from reforge.memory.retrieval import _score_signature  # local to avoid cycle

        if not signature:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memory_records WHERE memory_type = 'RECOVERY'"
            ).fetchall()
        records = [_row_to_record(r) for r in rows]
        scored = [
            (s, r) for r in records
            if (s := _score_signature(signature, r.problem_signature or {})) > 0
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:limit]]

    # ------------------------------------------------------------------
    # Internal helpers

    def _recall_typed(
        self, query: str, types: tuple[str, ...], limit: int
    ) -> list[MemoryRecord]:
        placeholders = ",".join("?" * len(types))
        query_lower = query.lower()
        query_words = set(query_lower.split())

        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM memory_records WHERE memory_type IN ({placeholders})",
                list(types),
            ).fetchall()

        records = [_row_to_record(r) for r in rows]
        scored = [(s, r) for r in records if (s := _score(r, query_lower, query_words)) > 0]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:limit]]

    def list_all(self, mem_type: str | None = None) -> list[MemoryRecord]:
        """List all records, optionally filtered by type. Useful for inspection."""
        if mem_type:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT * FROM memory_records WHERE memory_type = ? ORDER BY timestamp DESC",
                    (mem_type,),
                ).fetchall()
        else:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT * FROM memory_records ORDER BY timestamp DESC"
                ).fetchall()
        return [_row_to_record(r) for r in rows]

    def find(self, memory_id: str) -> MemoryRecord | None:
        """Return a single record by exact memory_id, or None if not found."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memory_records WHERE memory_id = ?", (memory_id,)
            ).fetchone()
        return _row_to_record(row) if row else None

    def stats(self) -> dict[str, object]:
        """Return aggregate statistics: counts by type and top error types."""
        with self._lock:
            type_rows = self._conn.execute(
                "SELECT memory_type, COUNT(*) as cnt FROM memory_records "
                "GROUP BY memory_type ORDER BY cnt DESC"
            ).fetchall()
            error_rows = self._conn.execute(
                "SELECT error_type, COUNT(*) as cnt FROM memory_records "
                "WHERE error_type != '' GROUP BY error_type ORDER BY cnt DESC LIMIT 10"
            ).fetchall()
            total = self._conn.execute(
                "SELECT COUNT(*) FROM memory_records"
            ).fetchone()[0]
        return {
            "total": total,
            "by_type": {r["memory_type"]: r["cnt"] for r in type_rows},
            "top_errors": [(r["error_type"], r["cnt"]) for r in error_rows],
        }

    def close(self) -> None:
        """Close the database connection. Safe to call multiple times."""
        with self._lock:
            self._conn.close()
