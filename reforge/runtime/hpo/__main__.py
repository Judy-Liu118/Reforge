"""CLI for the Reforge HPO benchmark.

Examples:
    python -m reforge.runtime.hpo --cases data/hpo_bench/toy_cases.json
    python -m reforge.runtime.hpo --cases data/hpo_bench/toy_cases.json --only iris
    python -m reforge.runtime.hpo --cases data/hpo_bench/toy_cases.json --workers 2 --out docs/hpo_toy.md
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path

from reforge.runtime.hpo.models import HpoCase
from reforge.runtime.hpo.reporter import render_markdown
from reforge.runtime.hpo.session import HpoSession


def _ensure_utf8(stream):
    enc = (getattr(stream, "encoding", "") or "").lower()
    if hasattr(stream, "buffer") and "utf" not in enc:
        return io.TextIOWrapper(stream.buffer, encoding="utf-8")
    return stream


def _load_cases(path: Path) -> list[HpoCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        HpoCase(
            case_id=item["case_id"],
            dataset_loader=item["dataset_loader"],
            task=item["task"],
            n_samples=int(item["n_samples"]),
            n_features=int(item["n_features"]),
            target_summary=item.get("target_summary", ""),
            scoring=item.get("scoring", "accuracy"),
            max_trials=int(item.get("max_trials", 5)),
            plateau_patience=int(item.get("plateau_patience", 3)),
            baseline_score=item.get("baseline_score"),
        )
        for item in raw
    ]


def main(argv: list[str] | None = None) -> int:
    sys.stdout = _ensure_utf8(sys.stdout)
    sys.stderr = _ensure_utf8(sys.stderr)

    parser = argparse.ArgumentParser(prog="reforge.runtime.hpo")
    parser.add_argument("--cases", required=True, type=Path, help="JSON file with HpoCase entries")
    parser.add_argument("--only", default=None, help="Run only the case with this case_id")
    parser.add_argument("--limit", type=int, default=None, help="Only run the first N cases")
    parser.add_argument("--out", type=Path, default=None, help="Markdown output path")
    parser.add_argument("--title", default="Reforge HPO benchmark report")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Case-level parallelism (default 1). >1 dispatches via ThreadPoolExecutor.",
    )
    args = parser.parse_args(argv)

    cases = _load_cases(args.cases)
    if args.only:
        cases = [c for c in cases if c.case_id == args.only]
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        print("No cases to run.", file=sys.stderr)
        return 2

    workers = max(1, args.workers)
    mode = "sequential" if workers == 1 else f"parallel x{workers}"
    print(
        f"Running {len(cases)} HPO cases against real LLM ({mode})...",
        file=sys.stderr,
    )

    started = time.perf_counter()
    session = HpoSession(max_workers=workers)
    report = session.run(cases)
    elapsed = time.perf_counter() - started

    successes = sum(1 for r in report.runs if r.best_cv_score is not None)
    print(
        f"Done in {elapsed:.1f}s — {successes}/{report.total_cases} cases got a score, "
        f"trial success rate {report.trial_success_rate*100:.1f}%",
        file=sys.stderr,
    )

    md = render_markdown(report, title=args.title)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md, encoding="utf-8")
        print(f"Report written to {args.out}", file=sys.stderr)
    else:
        print(md)

    return 0 if successes == report.total_cases else 1


if __name__ == "__main__":
    raise SystemExit(main())
