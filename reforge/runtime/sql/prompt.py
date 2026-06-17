"""Prompt assembly for the SQL benchmark.

Goal: produce a `user_request` string that the codegen node turns into
Python — the Python must open the SQLite DB at `db_path`, run a single
SQL, and print rows in a deterministic, easy-to-parse format.

The expected output format is one row per line, fields separated by
` | `. That makes the comparator's job purely about parsing + matching,
without having to reason about whitespace games in the LLM output.
"""

from __future__ import annotations

from reforge.runtime.sql.models import SqlCase


_PROMPT_TEMPLATE = (
    "You are answering a question by writing Python that queries a "
    "SQLite database. Follow these rules exactly:\n\n"
    "1. Open the database at DB_PATH using sqlite3.\n"
    "2. Run a SINGLE SQL statement that answers the question.\n"
    "3. Fetch all rows and print them one per line, fields joined by "
    "' | '. Print nothing else (no headers, no preamble, no trailing "
    "summary).\n"
    "4. If a column value is None, print the literal string 'NULL'.\n"
    "5. Use only the tables and columns shown in the schema.\n\n"
    "Database path: {db_path}\n\n"
    "Schema:\n```sql\n{schema}\n```\n\n"
    "{evidence_block}"
    "Question: {question}\n"
)


def build_prompt(case: SqlCase) -> str:
    evidence_block = ""
    if case.evidence:
        evidence_block = f"Evidence / hint: {case.evidence}\n\n"
    return _PROMPT_TEMPLATE.format(
        db_path=case.db_path.replace("\\", "/"),
        schema=case.schema_ddl.strip(),
        evidence_block=evidence_block,
        question=case.question.strip(),
    )


# ---------------------------------------------------------------------------
# Output parsing — the predicted stdout -> list[tuple]
# ---------------------------------------------------------------------------


_BANNER_PREFIXES = ("#", "---", "===", "...")


def parse_rows(stdout: str) -> list[tuple]:
    """Convert prompt-formatted stdout back into rows of values.

    We only skip empty lines and explicit banner lines (`#`, `---`, etc.).
    Every other line is taken as a row — split on ` | ` for multi-column
    output, kept as a single-cell row otherwise. This intentionally accepts
    lines that contain only text (people names, departments) because the
    benchmark question may legitimately return such rows.
    """
    rows: list[tuple] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(_BANNER_PREFIXES):
            continue
        if " | " in line:
            cells = tuple(c.strip() for c in line.split(" | "))
        else:
            cells = (line,)
        rows.append(cells)
    return rows
