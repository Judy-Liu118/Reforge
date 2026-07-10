"""CLI entry: ``python -m reforge.benchmark.phase1``.

Default N_seeds = 5 (PHASE0_METRICS.md v4 headline-axis budget). Appends
raw records to docs/eval/phase1_records.jsonl (leg-granular resume) and
writes the report to docs/eval/PHASE1_BIRD_ABLATION.md.

``--report-only`` recomputes the report from an existing records file
without touching the runtime — the 200-run ablation is expensive; the
report never should be.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from reforge.benchmark.phase1.driver import PhaseOneDriver, load_records
from reforge.benchmark.phase1.report import write_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reforge.benchmark.phase1")
    parser.add_argument("--seeds", type=int, default=5, help="N seeds (default 5, locked)")
    parser.add_argument(
        "--records",
        type=Path,
        default=Path("docs/eval/phase1_records.jsonl"),
        help="JSONL raw-records path (default docs/eval/phase1_records.jsonl)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("docs/eval/PHASE1_BIRD_ABLATION.md"),
        help="Markdown report path (default docs/eval/PHASE1_BIRD_ABLATION.md)",
    )
    parser.add_argument(
        "--bird-root",
        type=str,
        default=None,
        help="BIRD data root (default: data/bird, set in bird_loader.py)",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Recompute the report from the existing records file; no runs.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Log per-case progress",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if args.report_only:
        records = load_records(args.records)
    else:
        driver = PhaseOneDriver(
            n_seeds=args.seeds, bird_root=args.bird_root, records_path=args.records,
        )
        records = driver.run()

    write_report(records, out_path=args.out, n_seeds=args.seeds)
    print(f"Phase 1 report written to {args.out} ({len(records)} records)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
