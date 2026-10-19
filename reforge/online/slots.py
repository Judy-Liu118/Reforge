"""Task slots — what a request asks for, parsed without gold or an LLM.

Recall key for cards whose source task had no detectable data signal
(pre-registration v2, 2026-10-19). Character similarity was dominated by the
shared Chinese request templates and over-recalled; signal overlap alone
locked critic-minted cards (almost all signal-free) out of cross-task recall.
So a signal-free card is recalled only into a task with the same operation
AND (an overlapping target column OR the same table family).

    op        first matching keyword class, in priority order; "other" when
              nothing matches — "other" never matches anything
    columns   header names of the task's own tables that the request names
              (case-insensitive, whole-token)
    tables    the task's table file names
    families  table stems with common suffixes stripped (hotel_data → hotel)
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

# Priority order matters: a groupby request also says 汇总/之和, a correlation one may say "mean".
OPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("group", ("分组", "group by", "groupby", "grouped by", "for each")),
    ("filter_agg", ("的记录",)),
    ("corr", ("相关", "correlation")),
    ("outlier", ("异常值", "outlier", "z-score")),
    ("model", ("回归", "预测", "regression", "predict", "machine learning", "classifier", "train ")),
    ("median", ("中位数", "median")),
    ("std", ("标准差", "standard deviation", "variance")),
    ("count", ("计数", "多少", "number of", "how many", "count")),
    ("mean", ("平均", "mean", "average")),
    ("sum", ("总和", "求和", "sum", "total")),
)
_SUFFIX = re.compile(r"(_(clean|cleaned|data|dataset|new|final|v\d+)|\d+)+$")


def _question(request: str) -> str:
    """InfiAgent requests: the question sits between 'answer:' and 'Constraints:'."""
    m = re.search(r"answer:\s*(.*?)(?:\nConstraints:|$)", request, flags=re.S)
    return m.group(1) if m else request


def operation(request: str) -> str:
    q = _question(request).lower()
    for op, keys in OPS:
        for k in keys:
            # ASCII keywords whole-token only: "count" must not fire inside a column named review_count
            hit = re.search(rf"(?<![a-z0-9_]){re.escape(k.strip())}(?:e?s)?(?![a-z0-9_])", q) if k.isascii() else k in q
            if hit:
                return op
    return "other"


def table_family(name: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "_", Path(name).stem.lower()).strip("_")
    return _SUFFIX.sub("", stem) or stem


def _header(path: Path) -> list[str]:
    try:
        with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
            return [h.strip() for h in next(csv.reader(fh), []) if h.strip()]
    except (OSError, csv.Error):
        return []


def columns(request: str, tables, data_dir: Path | None) -> list[str]:
    if data_dir is None:
        return []
    text = request.lower()
    found = set()
    for t in tables:
        for h in _header(Path(data_dir) / t):
            k = h.lower()
            if len(k) < 2:
                continue
            if re.search(rf"(?<![a-z0-9_]){re.escape(k)}(?![a-z0-9_])", text):
                found.add(k)
    return sorted(found)


def task_slots(request: str, tables, data_dir: Path | None) -> dict:
    tables = list(tables)
    return {"op": operation(request), "columns": columns(request, tables, data_dir), "tables": tables,
            "families": sorted({table_family(t) for t in tables})}


def slot_match(card: dict | None, task: dict | None) -> bool:
    if not card or not task or card.get("op") in (None, "other") or card.get("op") != task.get("op"):
        return False
    return bool(set(card.get("columns", ())) & set(task.get("columns", ()))
                or set(card.get("families", ())) & set(task.get("families", ())))
