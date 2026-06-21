"""Generate fixture data files for the Experience Memory Benchmark.

Five paired tasks (A, A') exercise the same FailureFingerprint axis with
different surface keywords. Files live in ./benchmark_data/ relative to
the repo root, so the sandbox (cwd = workspace = repo root by default)
can open them with simple relative paths.

Generated artefacts:
  benchmark_data/orders.csv             # P1.A: KeyError 'profit' -> 'gross_profit'
  benchmark_data/customers.csv          # P1.A': KeyError 'margin' -> 'profit_margin'
  benchmark_data/sales-2024.csv         # P3.A: FileNotFoundError 'sales_2024.csv'
  benchmark_data/orders-2024.csv        # P3.A': FileNotFoundError 'orders_2024.csv'
  benchmark_data/transactions.db        # P4.A/A': sqlite tbl_sales + tbl_users
  (P2 uses existing sales.csv; P5 uses existing sales.csv + orders.csv)
"""

from __future__ import annotations

import csv
import random
import sqlite3
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "benchmark_data"


def _write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def gen_orders() -> None:
    """P1.A — column called gross_profit (LLM is told to use 'profit')."""
    random.seed(1)
    rows = [
        [f"O-{i:04d}", f"C{(i % 17) + 1:03d}", f"2025-02-{(i % 27) + 1:02d}",
         random.randint(50, 800), random.randint(100, 5000)]
        for i in range(40)
    ]
    _write_csv(
        OUT_DIR / "orders.csv",
        ["order_id", "customer_id", "date", "gross_profit", "amount"],
        rows,
    )


def gen_customers() -> None:
    """P1.A' — column called profit_margin (LLM is told to use 'margin')."""
    random.seed(2)
    names = ["Alice", "Bob", "Carol", "Dan", "Eve", "Frank", "Grace", "Henry"]
    rows = [
        [f"C{i:03d}", names[i % len(names)], round(random.uniform(0.05, 0.45), 3)]
        for i in range(1, 21)
    ]
    _write_csv(
        OUT_DIR / "customers.csv",
        ["customer_id", "name", "profit_margin"],
        rows,
    )


def gen_dash_csvs() -> None:
    """P3.A / P3.A' — files exist with '-' but LLM is told the name with '_'."""
    random.seed(3)
    # sales-2024.csv
    sales_rows = [
        [f"2024-{(i // 30) + 1:02d}-{(i % 30) + 1:02d}", "Widget",
         random.randint(1, 50), random.randint(2000, 9000)]
        for i in range(30)
    ]
    _write_csv(
        OUT_DIR / "sales-2024.csv",
        ["date", "product", "units", "revenue"],
        sales_rows,
    )
    # orders-2024.csv
    orders_rows = [
        [f"O24-{i:04d}", f"2024-{(i // 25) + 1:02d}-{(i % 25) + 1:02d}",
         random.randint(50, 1500)]
        for i in range(25)
    ]
    _write_csv(
        OUT_DIR / "orders-2024.csv",
        ["order_id", "date", "amount"],
        orders_rows,
    )


def gen_sqlite() -> None:
    """P4.A / P4.A' — sqlite db where tables are tbl_sales / tbl_users
    but the user request asks LLM to query 'sales' / 'users'.
    """
    path = OUT_DIR / "transactions.db"
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute("CREATE TABLE tbl_sales (id INTEGER PRIMARY KEY, "
                "product TEXT, revenue INTEGER)")
    cur.execute("CREATE TABLE tbl_users (id INTEGER PRIMARY KEY, "
                "name TEXT, country TEXT)")
    random.seed(4)
    sales_rows = [
        (i, f"P{(i % 5) + 1}", random.randint(100, 9000))
        for i in range(1, 51)
    ]
    user_rows = [
        (i, f"user_{i}", ["US", "DE", "CN", "JP"][i % 4])
        for i in range(1, 31)
    ]
    cur.executemany("INSERT INTO tbl_sales VALUES (?, ?, ?)", sales_rows)
    cur.executemany("INSERT INTO tbl_users VALUES (?, ?, ?)", user_rows)
    conn.commit()
    conn.close()


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gen_orders()
    gen_customers()
    gen_dash_csvs()
    gen_sqlite()
    print(f"Experience-memory fixtures written to {OUT_DIR}")
    for p in sorted(OUT_DIR.iterdir()):
        print(f"  - {p.name} ({p.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
