"""Default EDA stage definitions.

Each stage is sent to the Reforge runtime as a discrete code-as-action
task. Keeping prompts terse + specific lets the LLM emit short, predictable
pandas snippets that the sandbox can verify and the governor can recover.

Prompts are written in English regardless of dataset language — DeepSeek
and OpenAI both handle the result, and reviewers reading the report only
need the rendered Markdown.
"""

from __future__ import annotations

from reforge.runtime.eda.models import EdaStage


_PRELUDE = (
    "You are an EDA assistant. Read the CSV at {csv} with pandas and "
    "produce a short, deterministic answer. Print ONLY the final result, "
    "no commentary. If a column choice is ambiguous, prefer the first "
    "column that fits the description."
)


DEFAULT_STAGES: list[EdaStage] = [
    EdaStage(
        id="overview",
        title="Dataset overview",
        description="Row count, column count, list of column names.",
        prompt_template=(
            _PRELUDE
            + " Print: row_count, column_count, and the full list of column "
              "names (one per line, prefixed with '- ')."
        ),
    ),
    EdaStage(
        id="dtypes",
        title="Column dtypes",
        description="Inferred pandas dtype for every column.",
        prompt_template=(
            _PRELUDE
            + " For each column print: '<column>: <dtype>' on its own line. "
              "Use df.dtypes."
        ),
    ),
    EdaStage(
        id="missing",
        title="Missing-value analysis",
        description="Count and percentage of NaN per column.",
        prompt_template=(
            _PRELUDE
            + " For each column print: '<column>: <nan_count> (<percent>%)'. "
              "Skip columns with zero missing values. If everything is "
              "complete, print 'No missing values.'"
        ),
    ),
    EdaStage(
        id="numeric_stats",
        title="Numeric summary statistics",
        description="count / mean / std / min / 25% / 50% / 75% / max per numeric column.",
        prompt_template=(
            _PRELUDE
            + " Print df.describe(include=[np.number]).round(3).to_string()."
              " Make sure numpy is imported as np."
        ),
    ),
    EdaStage(
        id="categorical_freq",
        title="Top categories per categorical column",
        description="Top-5 value counts for each non-numeric column.",
        prompt_template=(
            _PRELUDE
            + " For each non-numeric column print a header '## <column>' "
              "followed by its top-5 value_counts. If no categorical "
              "columns exist, print 'No categorical columns.'"
        ),
    ),
    EdaStage(
        id="correlation",
        title="Pairwise correlation",
        description="Pearson correlation between numeric columns (top absolute pairs).",
        prompt_template=(
            _PRELUDE
            + " Compute the Pearson correlation matrix over numeric columns, "
              "then print the top-5 pairs by absolute correlation (excluding "
              "the diagonal) in the format '<col_a> <-> <col_b>: <corr_rounded_3>'. "
              "If fewer than 2 numeric columns, print 'Not enough numeric columns.'"
        ),
    ),
    EdaStage(
        id="outliers",
        title="Outlier detection",
        description="Per numeric column: count of values > 3 standard deviations.",
        prompt_template=(
            _PRELUDE
            + " For each numeric column compute mean and std, then count "
              "values whose |x - mean| > 3*std. Print '<column>: "
              "<outlier_count> outliers'. Skip columns with zero outliers; "
              "if all columns are clean, print 'No 3-sigma outliers detected.'"
        ),
    ),
    EdaStage(
        id="quality_warnings",
        title="Data quality warnings",
        description="Heuristic alerts: high cardinality, constant columns, very imbalanced classes, etc.",
        prompt_template=(
            _PRELUDE
            + " Inspect the dataframe and print any of these warnings that "
              "apply, one per line, prefixed with '- ':\n"
              "  * Constant column: a column whose nunique == 1\n"
              "  * High-cardinality column: an object/string column with nunique > 0.5 * len(df)\n"
              "  * Imbalanced binary target: the last column has 2 unique values and the rarer class < 5 percent\n"
              "  * Heavy missingness: any column with > 50 percent NaN\n"
              "If none apply, print 'No quality warnings.'"
        ),
    ),
]
