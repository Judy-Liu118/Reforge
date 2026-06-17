"""CLI for the EDA application.

Examples:
    python -m reforge.runtime.eda data/eda_samples/iris.csv
    python -m reforge.runtime.eda data/eda_samples/wine_quality.csv --out docs/eda_wine.md
    python -m reforge.runtime.eda data/eda_samples/titanic.csv --stages overview,missing
"""

from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path

from reforge.runtime.eda.reporter import render_markdown
from reforge.runtime.eda.session import EdaSession
from reforge.runtime.eda.stages import DEFAULT_STAGES


def main(argv: list[str] | None = None) -> int:
    # utf-8 console so report previews render cleanly on Windows GBK.
    # Skip when already utf-8 (e.g. under pytest capture) to avoid closing
    # the captured streams.
    def _ensure_utf8(stream):
        enc = (getattr(stream, "encoding", "") or "").lower()
        if hasattr(stream, "buffer") and "utf" not in enc:
            return io.TextIOWrapper(stream.buffer, encoding="utf-8")
        return stream

    sys.stdout = _ensure_utf8(sys.stdout)
    sys.stderr = _ensure_utf8(sys.stderr)

    parser = argparse.ArgumentParser(prog="reforge.runtime.eda")
    parser.add_argument("csv", help="Path to CSV file")
    parser.add_argument(
        "--out",
        type=Path,
        help="Markdown report output path (default: print to stdout)",
        default=None,
    )
    parser.add_argument(
        "--stages",
        help="Comma-separated subset of stage ids to run (default: all).",
        default=None,
    )
    args = parser.parse_args(argv)

    selected = DEFAULT_STAGES
    if args.stages:
        wanted = {s.strip() for s in args.stages.split(",") if s.strip()}
        selected = [s for s in DEFAULT_STAGES if s.id in wanted]
        if not selected:
            print(f"No stages match {args.stages!r}.", file=sys.stderr)
            return 2

    print(
        f"EDA on {args.csv} — {len(selected)} stages, real LLM...",
        file=sys.stderr,
    )
    started = time.perf_counter()
    session = EdaSession(stages=selected)
    report = session.run(args.csv)
    elapsed = time.perf_counter() - started

    md = render_markdown(report)
    print(
        f"Done in {elapsed:.1f}s — ok={report.ok_count} "
        f"recovered={report.recovered_count} failed={report.failed_count}",
        file=sys.stderr,
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md, encoding="utf-8")
        print(f"Report written to {args.out}", file=sys.stderr)
    else:
        print(md)

    return 0 if report.failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
