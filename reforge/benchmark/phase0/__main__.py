"""CLI entry: ``python -m reforge.benchmark.phase0``.

Default N_seeds = 3 (PHASE0_METRICS.md v2 secondary-axis budget;
calibration is instrument verification, not headline). Writes the
report to docs/eval/PHASE0_CALIBRATION.md by default.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from reforge.benchmark.phase0.driver import PhaseZeroDriver
from reforge.benchmark.phase0.report import write_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reforge.benchmark.phase0")
    parser.add_argument("--seeds", type=int, default=3, help="N seeds (default 3)")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("docs/eval/PHASE0_CALIBRATION.md"),
        help="Markdown output path (default docs/eval/PHASE0_CALIBRATION.md)",
    )
    parser.add_argument(
        "--bird-root",
        type=str,
        default=None,
        help="BIRD data root (default: data/bird, set in bird_loader.py)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Log per-case progress",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    driver = PhaseZeroDriver(n_seeds=args.seeds, bird_root=args.bird_root)
    records = driver.run()
    all_pass = write_report(records, out_path=args.out, n_seeds=args.seeds)
    print(f"Calibration written to {args.out} — verdict: {'GO' if all_pass else 'NO-GO'}",
          file=sys.stderr)
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
