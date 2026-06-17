"""CLI for the Reforge SQL benchmark.

Examples:
    python -m reforge.runtime.sql --cases data/sql_bench/toy_cases.json
    python -m reforge.runtime.sql --cases data/sql_bench/toy_cases.json --limit 5 --out docs/sql_toy.md
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path

from reforge.runtime.sql.models import SqlCase
from reforge.runtime.sql.reporter import render_markdown
from reforge.runtime.sql.session import SqlBenchSession


def _ensure_utf8(stream):
    enc = (getattr(stream, "encoding", "") or "").lower()
    if hasattr(stream, "buffer") and "utf" not in enc:
        return io.TextIOWrapper(stream.buffer, encoding="utf-8")
    return stream


def _load_cases(path: Path) -> list[SqlCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    cases: list[SqlCase] = []
    for item in raw:
        db_path = item["db_path"]
        # Allow relative db paths in the JSON for portability.
        if not Path(db_path).is_absolute():
            db_path = str((path.parent / db_path).resolve())
        cases.append(
            SqlCase(
                case_id=item["case_id"],
                db_path=db_path,
                schema_ddl=item["schema_ddl"],
                question=item["question"],
                gold_sql=item["gold_sql"],
                evidence=item.get("evidence", ""),
                difficulty=item.get("difficulty", "easy"),
                expects_ordering=item.get("expects_ordering", False),
            )
        )
    return cases


def main(argv: list[str] | None = None) -> int:
    sys.stdout = _ensure_utf8(sys.stdout)
    sys.stderr = _ensure_utf8(sys.stderr)

    parser = argparse.ArgumentParser(prog="reforge.runtime.sql")
    parser.add_argument("--cases", required=True, type=Path, help="JSON file with SqlCase entries")
    parser.add_argument("--limit", type=int, default=None, help="Only run first N cases")
    parser.add_argument("--out", type=Path, default=None, help="Markdown output path")
    parser.add_argument("--title", default="Reforge SQL benchmark report")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Case-level parallelism (default 1 = sequential). LLM calls are I/O-bound so threads scale near-linearly until the API rate-limits you.",
    )
    args = parser.parse_args(argv)

    cases = _load_cases(args.cases)
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        print("No cases to run.", file=sys.stderr)
        return 2

    workers = max(1, args.workers)
    mode = "sequential" if workers == 1 else f"parallel x{workers}"
    print(
        f"Running {len(cases)} SQL cases against real LLM ({mode})...",
        file=sys.stderr,
    )
    started = time.perf_counter()
    session = SqlBenchSession(max_workers=workers)
    report = session.run(cases)
    elapsed = time.perf_counter() - started

    print(
        f"Done in {elapsed:.1f}s — "
        f"exec_acc={report.execution_accuracy*100:.1f}% "
        f"(correct={report.correct_count} recovered={report.recovered_count} "
        f"wrong={report.wrong_count} error={report.error_count})",
        file=sys.stderr,
    )

    md = render_markdown(report, title=args.title)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md, encoding="utf-8")
        print(f"Report written to {args.out}", file=sys.stderr)
    else:
        print(md)

    return 0 if report.execution_accuracy >= 0.5 else 1


if __name__ == "__main__":
    raise SystemExit(main())
