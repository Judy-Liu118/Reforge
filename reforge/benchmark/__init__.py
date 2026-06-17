"""Reforge benchmark suite — quantitative proof of runtime self-healing.

Measures the metrics that differentiate Reforge from plain LLM tool-use:
  - first-shot success rate (no retry needed)
  - recovery rate (succeeded after at least one retry)
  - hard failure rate (gave up)
  - average attempts to outcome
  - cross-session learning curve (does the same case get faster as memory accumulates?)

Modules:
  cases     — predefined BenchmarkCase set, grouped by category
  models    — BenchmarkCase / BenchmarkRun / BenchmarkReport dataclasses
  runner    — orchestrates one or many cases through a RuntimeRunner
  reporter  — Markdown + summary table rendering
"""

from reforge.benchmark.cases import DEFAULT_CASES, get_cases_by_category
from reforge.benchmark.models import (
    BenchmarkCase,
    BenchmarkReport,
    BenchmarkRun,
)
from reforge.benchmark.reporter import render_markdown
from reforge.benchmark.runner import BenchmarkRunner

__all__ = [
    "BenchmarkCase",
    "BenchmarkReport",
    "BenchmarkRun",
    "BenchmarkRunner",
    "DEFAULT_CASES",
    "get_cases_by_category",
    "render_markdown",
]
