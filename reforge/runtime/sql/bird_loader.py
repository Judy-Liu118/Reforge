"""Load BIRD-SQL dev cases into Reforge SqlCase objects.

Usage (after running scripts/prepare_bird.py):

    from reforge.runtime.sql.bird_loader import load_bird_dev

    cases = load_bird_dev(limit=20)            # first 20
    cases = load_bird_dev(db_ids={"california_schools"})
    SqlBenchSession().run(cases)

Schema injection: for each unique db_id we read every table inside the
SQLite via `sqlite_master.sql` and concatenate the CREATE statements.
This is the same shape BIRD's published baselines use, and keeps the
prompt-side schema in lock-step with the actual DB.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from reforge.runtime.sql.models import SqlCase

_DEFAULT_ROOT = Path("data") / "bird"


def load_bird_dev(
    *,
    root: str | Path = _DEFAULT_ROOT,
    limit: int | None = None,
    db_ids: Iterable[str] | None = None,
    difficulty: Iterable[str] | None = None,
) -> list[SqlCase]:
    root = Path(root)
    questions_path = root / "dev.json"
    if not questions_path.exists():
        raise FileNotFoundError(
            f"BIRD dev.json not found at {questions_path}. "
            "Run `python scripts/prepare_bird.py` first."
        )

    raw = json.loads(questions_path.read_text(encoding="utf-8"))
    wanted_ids = set(db_ids) if db_ids else None
    wanted_diff = set(difficulty) if difficulty else None

    schema_cache: dict[str, str] = {}
    cases: list[SqlCase] = []
    for item in raw:
        db_id = item["db_id"]
        if wanted_ids and db_id not in wanted_ids:
            continue
        if wanted_diff and item.get("difficulty") not in wanted_diff:
            continue

        db_path = (root / "dev_databases" / db_id / f"{db_id}.sqlite").resolve()
        if not db_path.exists():
            # Skip rather than fail — partial BIRD installs are common.
            continue

        if db_id not in schema_cache:
            schema_cache[db_id] = _extract_schema(db_path)

        cases.append(SqlCase(
            case_id=f"bird_{item.get('question_id', len(cases))}_{db_id}",
            db_path=str(db_path),
            schema_ddl=schema_cache[db_id],
            question=item["question"],
            gold_sql=item["SQL"],
            evidence=item.get("evidence", ""),
            difficulty=item.get("difficulty", "easy"),
            expects_ordering="ORDER BY" in item["SQL"].upper(),
        ))
        if limit is not None and len(cases) >= limit:
            break

    return cases


def _extract_schema(db_path: Path) -> str:
    """Return all CREATE TABLE statements concatenated, in declaration order."""
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY rowid"
        )
        ddls = [row[0] for row in cur.fetchall() if row[0]]
    finally:
        conn.close()
    return ";\n".join(ddl.strip() for ddl in ddls) + ";"
