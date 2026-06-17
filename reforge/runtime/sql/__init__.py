"""Text-to-SQL benchmark application.

Built on Reforge runtime: each question becomes a code-as-action task
where the LLM is asked to write Python that opens the SQLite DB via
sqlite3 and prints the result. The sandbox runs it, the comparator
compares to the gold result set, eval drives RETRY when wrong, and the
governor stops at retry budget.

Single-shot baseline = prompt the LLM once and accept whatever SQL it
writes. The Reforge angle is: when the first SQL is wrong (column name
typo, wrong join, missing GROUP BY), the result comparator marks it
wrong, reflection sees the error or the mismatched result, and the
LLM gets a second shot informed by what went wrong.
"""

from reforge.runtime.sql.comparator import compare_results, normalise_row
from reforge.runtime.sql.models import (
    SqlBenchReport,
    SqlCase,
    SqlRun,
    SqlRunStatus,
)
from reforge.runtime.sql.reporter import render_markdown
from reforge.runtime.sql.session import SqlBenchSession

__all__ = [
    "SqlBenchReport",
    "SqlBenchSession",
    "SqlCase",
    "SqlRun",
    "SqlRunStatus",
    "compare_results",
    "normalise_row",
    "render_markdown",
]
