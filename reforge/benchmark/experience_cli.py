"""CLI entry point for the Experience Memory Benchmark.

Usage:
    python -m reforge.benchmark.experience_cli                  # run all 5 pairs (single seed)
    python -m reforge.benchmark.experience_cli --pair P1        # run one pair
    python -m reforge.benchmark.experience_cli --seeds 5        # multi-seed run with stats
    python -m reforge.benchmark.experience_cli --out docs/exp_benchmark.md
    python -m reforge.benchmark.experience_cli --keep-tmp       # leave tmp dirs

Hits the real LLM configured in `.env`. No mocks.
"""

from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path

from reforge.benchmark.experience_cases import PAIRED_CASES, pair_by_id
from reforge.benchmark.experience_driver import ExperienceDriver
from reforge.benchmark.experience_multiseed import MultiSeedDriver
from reforge.benchmark.experience_multiseed_reporter import render_multiseed_markdown
from reforge.benchmark.experience_reporter import render_experience_markdown
from reforge.benchmark.models import BenchmarkRun


def _stream_progress(label: str, run: BenchmarkRun) -> None:
    """One-line stderr progress per run."""
    from reforge.benchmark.experience_driver import pair_passed
    ok = "PASS" if pair_passed(run) else "FAIL"
    print(
        f"  [{label}] {ok}  outcome={run.actual_outcome}  attempts={run.attempts}  "
        f"score={run.eval_score:.2f}  recalls={run.memory_recalls}  "
        f"duration={run.duration_ms / 1000:.1f}s",
        file=sys.stderr,
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    # Force UTF-8 on Windows GBK consoles so Chinese task descriptions
    # and the markdown report render correctly.
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

    parser = argparse.ArgumentParser(prog="reforge.benchmark.experience_cli")
    parser.add_argument(
        "--pair",
        help="Run a single pair by id (e.g. P1, P3). Default: all pairs.",
        default=None,
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Markdown output path (default: print to stdout)",
        default=None,
    )
    parser.add_argument(
        "--keep-tmp",
        action="store_true",
        help="Keep tmp substrate dirs after the run (forensics)",
    )
    parser.add_argument(
        "--seeds",
        type=int,
        default=1,
        help=(
            "Number of independent seeds to run per pair (default 1). "
            "≥2 unlocks mean ± std + 95%% CI per KPI via the multi-seed "
            "reporter. 5 is a reasonable starting point for small benchmarks; "
            "the runtime cost scales linearly."
        ),
    )
    parser.add_argument(
        "--title",
        default="Reforge Experience Memory Benchmark",
        help="Report title",
    )
    args = parser.parse_args(argv)
    if args.seeds < 1:
        print("--seeds must be ≥ 1", file=sys.stderr)
        return 2

    if args.pair:
        pair = pair_by_id(args.pair)
        if pair is None:
            print(f"No pair with id={args.pair!r}.", file=sys.stderr)
            return 2
        pairs = [pair]
    else:
        pairs = list(PAIRED_CASES)

    total_runs = len(pairs) * 4 * args.seeds
    print(
        f"Running {len(pairs)} pair(s) × 4 legs × {args.seeds} seed(s) "
        f"= {total_runs} LLM calls...",
        file=sys.stderr,
    )
    started = time.perf_counter()

    if args.seeds == 1:
        driver = ExperienceDriver(progress=_stream_progress)
        report = driver.run_all(pairs, keep_tmp=args.keep_tmp)
        markdown = render_experience_markdown(report, title=args.title)
        nonneg = report.transfer_success_rate >= 0
    else:
        ms_driver = MultiSeedDriver(progress=_stream_progress)
        ms_report = ms_driver.run_all(
            args.seeds, pairs, keep_tmp=args.keep_tmp
        )
        markdown = render_multiseed_markdown(ms_report, title=args.title)
        # Exit 0 if the multi-seed transfer mean isn't strictly negative.
        nonneg = ms_report.transfer_success_rate.mean >= 0

    elapsed = time.perf_counter() - started
    print(f"\nAll done in {elapsed:.1f}s.\n", file=sys.stderr)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(markdown, encoding="utf-8")
        print(f"Report written to {args.out}", file=sys.stderr)
    else:
        print(markdown)

    return 0 if nonneg else 1


if __name__ == "__main__":
    raise SystemExit(main())
