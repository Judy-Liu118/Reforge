"""Generate sales.csv fixture used by benchmark cases.

Produces 31 rows, revenue column sum == 237731 (matches benchmark expected keywords).
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

TARGET_SUM = 237731
N_ROWS = 31
OUT = Path(__file__).resolve().parent.parent / "sales.csv"


def main() -> None:
    random.seed(42)
    raw = [random.randint(4000, 11000) for _ in range(N_ROWS)]
    diff = TARGET_SUM - sum(raw)
    raw[-1] += diff
    assert sum(raw) == TARGET_SUM, sum(raw)
    assert len(raw) == N_ROWS

    products = ["Widget", "Gadget", "Sprocket", "Doohickey", "Thingamajig"]
    regions = ["North", "South", "East", "West"]

    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "product", "region", "units", "revenue"])
        for i, rev in enumerate(raw):
            w.writerow([
                f"2025-01-{(i % 28) + 1:02d}",
                products[i % len(products)],
                regions[i % len(regions)],
                random.randint(1, 50),
                rev,
            ])
    print(f"Wrote {OUT} — rows={N_ROWS}, revenue sum={sum(raw)}, mean={sum(raw)/N_ROWS:.2f}")


if __name__ == "__main__":
    main()
