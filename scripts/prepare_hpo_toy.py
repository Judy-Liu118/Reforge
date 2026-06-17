"""Emit data/hpo_bench/toy_cases.json — 4 sklearn built-in datasets covering
classification and regression at small/medium feature counts.

Each case includes a `baseline_score` computed locally so the report
table can frame the LLM's best CV score against a trivial baseline:

    classification → DummyClassifier(strategy="most_frequent") accuracy
    regression     → DummyRegressor(strategy="mean") R^2 (typically 0)

The actual benchmark runs the agent against this same dataset without
ever seeing the baseline number.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from sklearn.datasets import (
    load_breast_cancer,
    load_diabetes,
    load_iris,
    load_wine,
)
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.model_selection import cross_val_score


def main() -> None:
    np.random.seed(0)
    cases = []
    cases.append(_classification_case(
        case_id="iris",
        loader_expr="sklearn.datasets.load_iris(return_X_y=True)",
        loader_callable=load_iris,
        target_summary="3 species (50 each)",
    ))
    cases.append(_classification_case(
        case_id="wine",
        loader_expr="sklearn.datasets.load_wine(return_X_y=True)",
        loader_callable=load_wine,
        target_summary="3 cultivars (59/71/48)",
    ))
    cases.append(_classification_case(
        case_id="breast_cancer",
        loader_expr="sklearn.datasets.load_breast_cancer(return_X_y=True)",
        loader_callable=load_breast_cancer,
        target_summary="binary malignant/benign (212/357)",
    ))
    cases.append(_regression_case(
        case_id="diabetes",
        loader_expr="sklearn.datasets.load_diabetes(return_X_y=True)",
        loader_callable=load_diabetes,
        target_summary="real-valued disease progression",
    ))

    out_path = Path("data") / "hpo_bench" / "toy_cases.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(cases, indent=2), encoding="utf-8")
    print(f"wrote {len(cases)} cases → {out_path}")
    for c in cases:
        print(
            f"  - {c['case_id']:<14} task={c['task']:<14} "
            f"n_samples={c['n_samples']:<5} n_features={c['n_features']:<3} "
            f"baseline={c['baseline_score']:.4f} ({c['scoring']})"
        )


def _classification_case(*, case_id, loader_expr, loader_callable, target_summary):
    X, y = loader_callable(return_X_y=True)
    dummy = DummyClassifier(strategy="most_frequent")
    base = float(cross_val_score(dummy, X, y, cv=5, scoring="accuracy").mean())
    return {
        "case_id": case_id,
        "dataset_loader": loader_expr,
        "task": "classification",
        "n_samples": int(X.shape[0]),
        "n_features": int(X.shape[1]),
        "target_summary": target_summary,
        "scoring": "accuracy",
        "max_trials": 5,
        "plateau_patience": 3,
        "baseline_score": round(base, 4),
    }


def _regression_case(*, case_id, loader_expr, loader_callable, target_summary):
    X, y = loader_callable(return_X_y=True)
    dummy = DummyRegressor(strategy="mean")
    base = float(cross_val_score(dummy, X, y, cv=5, scoring="r2").mean())
    return {
        "case_id": case_id,
        "dataset_loader": loader_expr,
        "task": "regression",
        "n_samples": int(X.shape[0]),
        "n_features": int(X.shape[1]),
        "target_summary": target_summary,
        "scoring": "r2",
        "max_trials": 5,
        "plateau_patience": 3,
        "baseline_score": round(base, 4),
    }


if __name__ == "__main__":
    main()
