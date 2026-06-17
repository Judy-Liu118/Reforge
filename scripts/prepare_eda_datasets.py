"""Materialise UCI / OpenML datasets to data/eda_samples/*.csv.

Run once: `python scripts/prepare_eda_datasets.py`.

Why we ship a script and not the CSV files themselves:
  - keeps the repo small (titanic + adult are several MB)
  - sklearn is already a dep, so fetch_openml is free
  - makes the dataset provenance explicit (which OpenML id, which version)
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from sklearn.datasets import fetch_openml, load_iris, load_wine

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "eda_samples"


def _iris() -> pd.DataFrame:
    """Iris — 150 rows, 4 numeric features + species label. Clean baseline."""
    ds = load_iris(as_frame=True)
    df = ds.frame
    df = df.rename(columns={"target": "species"})
    df["species"] = df["species"].map({0: "setosa", 1: "versicolor", 2: "virginica"})
    return df


def _wine_quality() -> pd.DataFrame:
    """Wine quality (red) — 1599 rows, 11 chemistry features + quality score.

    Has skewed distributions and outliers — good for testing distribution
    detection and outlier heuristics.
    """
    ds = fetch_openml(name="wine-quality-red", version=1, as_frame=True, parser="auto")
    df = ds.frame.copy()
    if "class" in df.columns:
        df = df.rename(columns={"class": "quality"})
    return df


def _titanic() -> pd.DataFrame:
    """Titanic — 891 rows; mixed dtypes + real missing values.

    The classic EDA tutorial dataset. Age has ~20% NaN, Cabin ~77% NaN —
    forces missing-value handling.
    """
    ds = fetch_openml(name="titanic", version=1, as_frame=True, parser="auto")
    df = ds.frame.copy()
    return df


def _adult() -> pd.DataFrame:
    """Adult income — 48k rows; mixed numeric + categorical; class imbalance."""
    ds = fetch_openml(name="adult", version=2, as_frame=True, parser="auto")
    df = ds.frame.copy()
    return df


_DATASETS = {
    "iris.csv": _iris,
    "wine_quality.csv": _wine_quality,
    "titanic.csv": _titanic,
    "adult.csv": _adult,
}


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for filename, loader in _DATASETS.items():
        path = OUT_DIR / filename
        if path.exists():
            print(f"  [skip] {filename} already exists")
            continue
        print(f"  [fetch] {filename} ...", flush=True)
        df = loader()
        df.to_csv(path, index=False)
        print(f"      -> {len(df)} rows x {len(df.columns)} cols -> {path}")
    print(f"\nDone. Datasets are in {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
