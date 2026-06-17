"""CLI entry point for the Reforge benchmark suite.

Usage:
    python -m reforge.benchmark              # run all DEFAULT_CASES
    python -m reforge.benchmark --category csv_basic
    python -m reforge.benchmark --out docs/benchmark_sample.md
    python -m reforge.benchmark --limit 3    # only first N cases

Hits the real LLM configured in .env. No mocks.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from reforge.benchmark.cases import DEFAULT_CASES, get_cases_by_category
from reforge.benchmark.reporter import render_markdown
from reforge.benchmark.runner import BenchmarkRunner
def main(argv: list[str] | None = None) -> int:
    # Force utf-8 on stdout so Chinese task descriptions and the report
    # render cleanly on Windows GBK consoles.
    import io
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

    parser = argparse.ArgumentParser(prog="reforge.benchmark")
    parser.add_argument("--category", help="Filter to one category", default=None)
    parser.add_argument("--case", help="Run a single case by id", default=None)
    parser.add_argument("--limit", type=int, help="Only run first N cases", default=None)
    parser.add_argument(
        "--out",
        type=Path,
        help="Markdown output path (default: print to stdout)",
        default=None,
    )
    parser.add_argument(
        "--title",
        default="Reforge Benchmark Report",
        help="Report title",
    )
    args = parser.parse_args(argv)

    if args.case:
        cases = [c for c in DEFAULT_CASES if c.id == args.case]
        if not cases:
            print(f"No case with id={args.case!r}.", file=sys.stderr)
            return 2
    else:
        cases = (
            get_cases_by_category(args.category)
            if args.category else list(DEFAULT_CASES)
        )
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        print("No cases selected.", file=sys.stderr)
        return 2

    print(f"Running {len(cases)} cases against real LLM...", file=sys.stderr)
    started = time.perf_counter()

    runner = BenchmarkRunner()
    runs = []
    for idx, case in enumerate(cases, 1):
        print(
            f"  [{idx}/{len(cases)}] {case.id} ({case.category}, {case.difficulty})...",
            file=sys.stderr,
            flush=True,
        )
        run = runner.run_case(case)
        runs.append(run)
        ok = "PASS" if run.passed else "FAIL"
        print(
            f"      -> {ok}  outcome={run.actual_outcome}  attempts={run.attempts}  "
            f"score={run.eval_score:.2f}  duration={run.duration_ms/1000:.1f}s",
            file=sys.stderr,
            flush=True,
        )

    from reforge.benchmark.models import BenchmarkReport
    report = BenchmarkReport(runs=runs)
    elapsed = time.perf_counter() - started
    print(f"\nAll cases done in {elapsed:.1f}s.\n", file=sys.stderr)

    markdown = render_markdown(report, title=args.title)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(markdown, encoding="utf-8")
        print(f"Report written to {args.out}", file=sys.stderr)
    else:
        print(markdown)

    return 0 if report.pass_rate >= 0.6 else 1


if __name__ == "__main__":
    raise SystemExit(main())
