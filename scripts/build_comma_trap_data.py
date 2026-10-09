"""Generate the comma-trap corpus data under benchmark_data/comma_trap/.

One directory per task, one CSV inside it, plus a gold.json recording the
oracle sums. Rows come from `reforge.benchmark.comma_trap_cases.task_rows`, so
the data and the oracle are generated from one source and cannot drift.

    python scripts/build_comma_trap_data.py            # write all 20 tasks
    python scripts/build_comma_trap_data.py --tasks 2  # first 2 only (smoke)

Re-running overwrites in place and is deterministic — the same command on
another machine produces byte-identical files.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from reforge.benchmark.comma_trap_cases import (
    DATA_ROOT,
    SPECS,
    gold_value,
    task_rows,
)


def write_task(spec, root: Path) -> dict:
    task_dir = root / spec.dir_name
    task_dir.mkdir(parents=True, exist_ok=True)
    path = task_dir / f"{spec.file_stem}.csv"

    rows = task_rows(spec)
    # newline="" per csv module contract — without it Windows writes \r\r\n.
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([spec.label_column, spec.amount_column])
        for label, raw_cell, _value in rows:
            writer.writerow([label, raw_cell])

    return {
        "task_id": spec.task_id,
        "csv": spec.csv_path,
        "column": spec.amount_column,
        "rows": len(rows),
        "gold": gold_value(spec),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=int, default=len(SPECS),
                        help="how many tasks to write (default: all)")
    parser.add_argument("--root", default=DATA_ROOT,
                        help=f"output directory (default: {DATA_ROOT})")
    args = parser.parse_args()

    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)

    manifest = [write_task(spec, root) for spec in SPECS[: args.tasks]]
    gold_path = root / "gold.json"
    gold_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"wrote {len(manifest)} task(s) under {root}/")
    for entry in manifest:
        print(f"  {entry['task_id']}  {entry['csv']}  column={entry['column']}  "
              f"gold={entry['gold']}")
    print(f"gold manifest: {gold_path}")


if __name__ == "__main__":
    main()
