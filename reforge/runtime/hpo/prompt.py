"""Prompt assembly + CV-score parsing for the HPO benchmark."""

from __future__ import annotations

import re

from reforge.runtime.hpo.models import HpoCase, HpoTrial

_CV_SCORE_PATTERN = re.compile(
    r"CV_SCORE\s*=\s*([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)"
)


def build_prompt(
    case: HpoCase,
    history: list[HpoTrial],
    *,
    trial_index: int,
) -> str:
    """Build the LLM prompt for one trial.

    The prompt embeds the dataset metadata + previous trial outcomes so
    the LLM can avoid re-proposing identical pipelines. We deliberately
    keep the contract narrow:

      * write a single Python script
      * load X, y via `case.dataset_loader`
      * build a sklearn Pipeline of your choice
      * cross_val_score it (5-fold) with `scoring="<case.scoring>"`
      * print exactly one line ending in `CV_SCORE=<float>` (mean)
    """
    history_block = _format_history(history) if history else "(none yet — this is trial #1)"
    return _TEMPLATE.format(
        case_id=case.case_id,
        task=case.task,
        dataset_loader=case.dataset_loader,
        n_samples=case.n_samples,
        n_features=case.n_features,
        target_summary=case.target_summary,
        scoring=case.scoring,
        trial_index=trial_index,
        max_trials=case.max_trials,
        history_block=history_block,
    )


def parse_cv_score(stdout: str) -> float | None:
    """Return the LAST CV_SCORE value found in stdout, or None.

    We use the last match so a pipeline that prints intermediate
    `CV_SCORE=...` lines during debug doesn't trick us.
    """
    matches = _CV_SCORE_PATTERN.findall(stdout or "")
    if not matches:
        return None
    try:
        return float(matches[-1])
    except ValueError:
        return None


def summarise_pipeline(stdout: str, *, fallback: str = "") -> str:
    """Pluck a short pipeline summary out of the LLM's printed code.

    We don't actually re-parse Python; we look for a line containing
    'PIPELINE=' (if the LLM was nice enough to print it) else fall back
    to whatever the caller passes (typically the final_answer head).
    """
    for line in (stdout or "").splitlines():
        s = line.strip()
        if s.upper().startswith("PIPELINE="):
            return s.split("=", 1)[1].strip()[:240]
    return fallback.strip()[:240]


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------

_TEMPLATE = """You are an automated machine-learning agent. Task: pick a good
sklearn pipeline for the dataset described below and report its
cross-validated score.

# Dataset
- case_id: {case_id}
- task: {task}
- loader (eval inside sandbox): `{dataset_loader}`
- n_samples: {n_samples}
- n_features: {n_features}
- target: {target_summary}
- scoring metric: `{scoring}` (sklearn convention; higher is better)

# Budget
- this is trial {trial_index} of at most {max_trials}.

# Previous trials in this case
{history_block}

# Required output contract
Write **one Python script** that, when executed, does ALL of:

1. Loads data with the loader above.
2. Builds a sklearn Pipeline (feel free to include preprocessing, e.g.
   StandardScaler, ColumnTransformer, etc.).
3. Runs `cross_val_score(pipeline, X, y, cv=5, scoring="{scoring}")`.
4. Prints `PIPELINE=<one-line description>` (so the human report can
   read it).
5. Prints exactly one final line:
   `CV_SCORE=<mean of the 5-fold scores, formatted as a plain float>`

Hard constraints:
- DO NOT call train_test_split — use cross_val_score only.
- DO NOT print anything *after* the CV_SCORE line.
- Use only sklearn / numpy / pandas (sandbox does not have xgboost,
  lightgbm, catboost installed).

Choose a pipeline that is meaningfully different from those already
tried above. If a previous trial errored, fix it instead of repeating
the same approach.
"""


def _format_history(history: list[HpoTrial]) -> str:
    lines = []
    for t in history:
        if t.status == "ok" and t.cv_score is not None:
            lines.append(
                f"- trial {t.trial_index}: score={t.cv_score:.4f}, "
                f"pipeline={t.pipeline_summary or '?'}"
            )
        elif t.status == "parse_error":
            lines.append(
                f"- trial {t.trial_index}: NO CV_SCORE printed (output format violation)"
            )
        else:
            lines.append(
                f"- trial {t.trial_index}: runtime error — {t.error[:120] or 'unknown'}"
            )
    return "\n".join(lines)
