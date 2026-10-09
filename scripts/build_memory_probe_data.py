"""Build the memory-transfer probe corpus data (encoded schema + legend).

Per docs/eval/MEMORY_TRANSFER_PROBE_PREREG.md §3: three fingerprint axes,
each a dataset whose semantic names are hidden behind a private encoding that
is resolvable only via a *legend* living in the same dataset. The prompts
(see reforge/benchmark/memory_probe_cases.py) never mention the legend, so a
cold run must discover it; a warm run may recall the "consult the legend"
strategy. Deterministic and re-runnable.

Answer keys (sum aggregates, chosen distinct and ≥3 digits to keep the
keyword match unambiguous):
  MP1  revenue=c_0001 sum=750    cost=c_0002 sum=188
  MP2  inventory=t_a1 qty sum=660   shipments=t_a2 qty sum=1500
  MP3  sales=mp_d001 amount sum=650   returns=mp_d002 amount sum=1010
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "benchmark_data"


def _write_csv(name: str, header: list[str], rows: list[list]) -> None:
    with (DATA / name).open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def build_mp1_columns() -> None:
    # Encoded column names; legend maps code -> semantic name.
    _write_csv(
        "mp_metrics.csv",
        ["c_0001", "c_0002", "c_0003"],
        [[120, 45, 5], [340, 80, 8], [290, 63, 6]],  # revenue sum 750, cost sum 188
    )
    _write_csv(
        "mp_metrics_legend.csv",
        ["code", "name"],
        [["c_0001", "revenue"], ["c_0002", "cost"], ["c_0003", "units"]],
    )


def build_mp2_tables() -> None:
    db = DATA / "mp_warehouse.db"
    if db.exists():
        db.unlink()
    con = sqlite3.connect(db)
    try:
        cur = con.cursor()
        cur.execute("CREATE TABLE t_a1 (item TEXT, qty INTEGER)")       # inventory
        cur.executemany("INSERT INTO t_a1 VALUES (?,?)",
                        [("a", 110), ("b", 220), ("c", 330)])           # sum 660
        cur.execute("CREATE TABLE t_a2 (dest TEXT, qty INTEGER)")       # shipments
        cur.executemany("INSERT INTO t_a2 VALUES (?,?)",
                        [("x", 400), ("y", 500), ("z", 600)])           # sum 1500
        cur.execute("CREATE TABLE _legend (code TEXT, name TEXT)")
        cur.executemany("INSERT INTO _legend VALUES (?,?)",
                        [("t_a1", "inventory"), ("t_a2", "shipments")])
        con.commit()
    finally:
        con.close()


def build_mp3_files() -> None:
    _write_csv("mp_d001.csv", ["amount"], [[100], [250], [300]])   # sales sum 650
    _write_csv("mp_d002.csv", ["amount"], [[210], [330], [470]])   # returns sum 1010
    _write_csv(
        "mp_manifest.csv",
        ["file", "name"],
        [["mp_d001.csv", "sales"], ["mp_d002.csv", "returns"]],
    )


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    build_mp1_columns()
    build_mp2_tables()
    build_mp3_files()
    print(f"built memory-probe data in {DATA}")


if __name__ == "__main__":
    main()
