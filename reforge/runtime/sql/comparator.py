"""Execution-accuracy comparator for Text-to-SQL.

Compares the result set of a predicted SQL against the result of a gold
SQL. Mirrors the standard Spider / BIRD `exec_acc` semantics:

  - same number of rows
  - same number of columns
  - same row multiset (order-insensitive by default; order-sensitive when
    the gold SQL contains `ORDER BY` or the caller asks)
  - numeric values compared with a small tolerance to absorb int/float
    representation differences
  - None / "" / "NULL" all normalised to None
  - strings trimmed for whitespace before comparison
"""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_sql(db_path: str | Path, sql: str) -> list[tuple]:
    """Execute a SQL string and return rows as a list of tuples.

    Caller catches exceptions; we don't swallow them so the SqlBenchSession
    can record the error.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(sql)
        return cur.fetchall()
    finally:
        conn.close()


def compare_results(
    predicted: list[tuple],
    expected: list[tuple],
    *,
    order_sensitive: bool = False,
    numeric_tolerance: float = 1e-6,
) -> bool:
    """Return True iff the two result sets are equivalent.

    Equivalence rules (per BIRD / Spider exec_acc convention):
      - row count and column count must match
      - rows are compared as tuples after `normalise_row`
      - when `order_sensitive=False`, the two multisets must match;
        otherwise rows are compared in order
    """
    if len(predicted) != len(expected):
        return False
    if predicted and expected and len(predicted[0]) != len(expected[0]):
        return False

    p_norm = [normalise_row(r, numeric_tolerance) for r in predicted]
    e_norm = [normalise_row(r, numeric_tolerance) for r in expected]

    if order_sensitive:
        return p_norm == e_norm

    # Multiset compare — same rows in any order
    return sorted(p_norm) == sorted(e_norm)


def normalise_row(row: tuple, numeric_tolerance: float = 1e-6) -> tuple:
    """Normalise one row so two semantically-equal rows compare equal."""
    return tuple(_normalise_cell(c, numeric_tolerance) for c in row)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


_NULL_STRS = {"", "NULL", "null", "None"}


def _normalise_cell(value, numeric_tolerance: float):
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        if s in _NULL_STRS:
            return None
        # Numeric-looking string: collapse "1" and "1.0" by parsing
        try:
            f = float(s)
            return _quantise(f, numeric_tolerance)
        except ValueError:
            return s
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return _quantise(float(value), numeric_tolerance)
    return value


def _quantise(f: float, tol: float) -> float:
    """Quantise a float to the tolerance grid so 1.0 and 1.0000001 compare equal."""
    if math.isnan(f):
        return float("nan")
    if math.isinf(f):
        return f
    if tol <= 0:
        return f
    grid = round(f / tol) * tol
    # Float arithmetic introduces noise — round to a few extra digits.
    return round(grid, 9)
