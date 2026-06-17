"""Dirty-data Text-to-SQL benchmark.

Where `prepare_sql_toy.py` ships a clean school registry, this benchmark
seeds *real-world dirty-data patterns* into the data — the schema itself
is honest, but the rows carry the kind of mess every analyst meets in
production: NULL-dense columns, case inconsistency across the same field,
trailing whitespace in names, orphan foreign keys, mixed date formats,
sign-flipped numerics. Each question is written so the naive SQL produces
the wrong answer; the gold SQL pins the explicit cleaning step the agent
is expected to apply.

Produces:
  data/sql_bench/dirty.db
  data/sql_bench/dirty_cases.json
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "sql_bench"
DB_PATH = OUT_DIR / "dirty.db"
CASES_PATH = OUT_DIR / "dirty_cases.json"


SCHEMA = """
DROP TABLE IF EXISTS customers;
DROP TABLE IF EXISTS orders;

CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT,
    signup_date TEXT,
    tier TEXT
);

CREATE TABLE orders (
    order_id INTEGER PRIMARY KEY,
    customer_id INTEGER,
    amount REAL,
    status TEXT,
    placed_at TEXT
);
"""


# Dirty seeds:
#   - email is NULL for 2 / 8 rows
#   - "Bob Khan " has a trailing space; "  Quinn" has leading whitespace
#   - tier is mixed case: "gold" / "Gold" / "GOLD" / "silver" / " SILVER"
#   - signup_date mixes "2024-01-15" and US-format "01/15/2024"
CUSTOMERS = [
    (1, "Alice Chen",   "alice@example.com",  "2024-01-15",  "gold"),
    (2, "Bob Khan ",    None,                 "2024-02-10",  "Gold"),
    (3, "Carol Davis",  "carol@example.com",  "2024-03-08",  "GOLD"),
    (4, "Dan Park",     "dan@example.com",    "01/15/2024",  "silver"),
    (5, "Eve Liu",      None,                 "2024-05-20",  " SILVER"),
    (6, "  Quinn",      "quinn@example.com",  "2024-06-02",  "bronze"),
    (7, "Riley Stone",  "riley@example.com",  "2024-07-19",  "bronze"),
    (8, "Sara Vega",    "sara@example.com",   "2024-08-30",  "Gold"),
]


# Dirty seeds:
#   - customer_id 99 / 88 are orphans (no matching customer)
#   - amount -45.0 / -10.0 are refunds (negative, must be excluded for spend totals)
#   - status mixes "completed" / "Completed" / "COMPLETE" / "shipped" / "pending"
ORDERS = [
    (1001, 1,   120.50, "completed",  "2024-02-01"),
    (1002, 1,   -45.00, "completed",  "2024-02-12"),  # refund
    (1003, 2,    80.00, "Completed",  "2024-03-15"),
    (1004, 3,   220.00, "COMPLETE",   "2024-04-22"),
    (1005, 4,    35.00, "shipped",    "2024-05-01"),
    (1006, 4,    65.00, "completed",  "2024-05-20"),
    (1007, 5,   150.00, "completed",  "2024-06-10"),
    (1008, 6,    20.00, "pending",    "2024-07-04"),
    (1009, 8,   300.00, "Completed",  "2024-08-12"),
    (1010, 8,   -10.00, "completed",  "2024-08-25"),  # refund
    (1011, 99,   55.00, "completed",  "2024-09-01"),  # orphan FK
    (1012, 88,   25.00, "Completed",  "2024-09-05"),  # orphan FK
]


SCHEMA_DDL_FOR_PROMPT = """\
CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT,
    signup_date TEXT,
    tier TEXT
);
CREATE TABLE orders (
    order_id INTEGER PRIMARY KEY,
    customer_id INTEGER,
    amount REAL,
    status TEXT,
    placed_at TEXT
);"""


# Each case carries an `evidence` field that names the dirty pattern. The
# runtime sees this verbatim — analogous to BIRD's evidence column — so the
# LLM has a fighting chance once it learns to read evidence.
CASES_RAW = [
    {
        "case_id": "d01_count_gold_case_insensitive",
        "difficulty": "easy",
        "question": (
            "How many customers are in the Gold tier? Tier values were "
            "captured by multiple intake forms, so case may vary."
        ),
        "evidence": "tier comparison must be case-insensitive (e.g. LOWER(tier) = 'gold').",
        "gold_sql": (
            "SELECT COUNT(*) FROM customers WHERE LOWER(TRIM(tier)) = 'gold'"
        ),
    },
    {
        "case_id": "d02_customers_missing_email",
        "difficulty": "easy",
        "question": "How many customers have no email address on file?",
        "evidence": "missing email is stored as NULL, not empty string.",
        "gold_sql": "SELECT COUNT(*) FROM customers WHERE email IS NULL",
    },
    {
        "case_id": "d03_avg_completed_order_amount",
        "difficulty": "medium",
        "question": (
            "What is the average amount of completed orders? Round to 2 "
            "decimals. Status was entered by different systems and "
            "capitalisation is inconsistent."
        ),
        "evidence": "status uses inconsistent casing — 'completed' / 'Completed' / 'COMPLETE'.",
        "gold_sql": (
            "SELECT ROUND(AVG(amount), 2) FROM orders "
            "WHERE LOWER(TRIM(status)) IN ('completed', 'complete')"
        ),
    },
    {
        "case_id": "d04_total_spend_excluding_refunds",
        "difficulty": "medium",
        "question": (
            "What is each customer's total positive spend (excluding "
            "refunds)? Return the customer name (trimmed) and the total, "
            "skipping customers whose orders were all refunds or who do "
            "not appear in the orders table."
        ),
        "evidence": (
            "negative `orders.amount` rows are refunds — exclude them; "
            "customer names may carry leading/trailing whitespace."
        ),
        "gold_sql": (
            "SELECT TRIM(c.name), SUM(o.amount) "
            "FROM customers c JOIN orders o ON c.customer_id = o.customer_id "
            "WHERE o.amount > 0 "
            "GROUP BY c.customer_id"
        ),
    },
    {
        "case_id": "d05_orphan_orders",
        "difficulty": "hard",
        "question": (
            "How many orders reference a customer_id that does not exist "
            "in the customers table? Return that orphan count as a single "
            "integer."
        ),
        "evidence": (
            "the orders.customer_id FK is not enforced; some orders "
            "reference deleted or never-created customers."
        ),
        "gold_sql": (
            "SELECT COUNT(*) FROM orders o "
            "LEFT JOIN customers c ON o.customer_id = c.customer_id "
            "WHERE c.customer_id IS NULL"
        ),
    },
]


# ---------------------------------------------------------------------------


def _build_db() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?)", CUSTOMERS)
        conn.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?)", ORDERS)
        conn.commit()
    finally:
        conn.close()


def _write_cases() -> None:
    items = []
    db_rel = "./dirty.db"
    for raw in CASES_RAW:
        items.append({
            "case_id": raw["case_id"],
            "db_path": db_rel,
            "schema_ddl": SCHEMA_DDL_FOR_PROMPT,
            "question": raw["question"],
            "gold_sql": raw["gold_sql"],
            "evidence": raw.get("evidence", ""),
            "difficulty": raw["difficulty"],
            "expects_ordering": raw.get("expects_ordering", False),
        })
    CASES_PATH.write_text(
        json.dumps(items, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _sanity_check_gold() -> None:
    """Run every gold SQL against the freshly built DB — guards against typos."""
    conn = sqlite3.connect(DB_PATH)
    try:
        for raw in CASES_RAW:
            try:
                conn.execute(raw["gold_sql"]).fetchall()
            except Exception as exc:
                raise RuntimeError(
                    f"gold SQL for {raw['case_id']} failed: {exc}"
                ) from exc
    finally:
        conn.close()


def main() -> int:
    _build_db()
    _sanity_check_gold()
    _write_cases()
    print(f"  -> SQLite DB    : {DB_PATH}")
    print(f"  -> Cases JSON   : {CASES_PATH}   ({len(CASES_RAW)} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
