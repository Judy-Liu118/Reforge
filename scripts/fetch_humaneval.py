"""Fetch the official HumanEval release into benchmark_data/HumanEval.jsonl.

Source: openai/human-eval (MIT licence), 164 tasks, one JSON object per line
with keys task_id / prompt / entry_point / canonical_solution / test.
Deterministic and re-runnable; overwrites the local copy.
"""

from __future__ import annotations

import gzip
import json
import sys
import urllib.request
from pathlib import Path

URL = "https://raw.githubusercontent.com/openai/human-eval/master/data/HumanEval.jsonl.gz"
OUT = Path(__file__).resolve().parents[1] / "benchmark_data" / "HumanEval.jsonl"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raw = urllib.request.urlopen(URL, timeout=60).read()
    data = gzip.decompress(raw).decode("utf-8")
    rows = [line for line in data.splitlines() if line.strip()]
    # Validate every row carries the fields the task relies on.
    required = {"task_id", "prompt", "entry_point", "canonical_solution", "test"}
    for i, line in enumerate(rows, 1):
        missing = required - set(json.loads(line))
        if missing:
            raise RuntimeError(f"row {i}: missing keys {sorted(missing)}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"fetched {len(rows)} HumanEval tasks -> {OUT}")


if __name__ == "__main__":
    main()
