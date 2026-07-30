"""Fetch the sanitized MBPP set into benchmark_data/sanitized-mbpp.json.

Source: google-research/google-research/mbpp (sanitized-mbpp.json), 427
hand-verified tasks, each with task_id / prompt / code / test_imports /
test_list. Deterministic and re-runnable; overwrites the local copy.

We use the *sanitized* set (hand-checked) rather than the full 974-task file:
the descriptions are clean enough to prompt from, and the smaller size keeps a
full governor×naive×seed sweep affordable.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

URL = "https://raw.githubusercontent.com/google-research/google-research/master/mbpp/sanitized-mbpp.json"
OUT = Path(__file__).resolve().parents[1] / "benchmark_data" / "sanitized-mbpp.json"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raw = urllib.request.urlopen(URL, timeout=60).read().decode("utf-8")
    data = json.loads(raw)
    required = {"task_id", "prompt", "code", "test_imports", "test_list"}
    for i, row in enumerate(data, 1):
        missing = required - set(row)
        if missing:
            raise RuntimeError(f"task {i}: missing keys {sorted(missing)}")
        if not row["test_list"]:
            raise RuntimeError(f"task {row['task_id']}: empty test_list")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"fetched {len(data)} MBPP (sanitized) tasks -> {OUT}")


if __name__ == "__main__":
    main()
