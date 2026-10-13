"""Real-table silent-trap cases for the learning-loop experiment.

Each case = one data-quality defect injected into a COPY of a real public
table (InfiAgent-DABench dev tables) + one simple aggregate question.

Rules this file enforces:
  * the source table is read-only; dirty copies go to
    runs/_external/infiagent_injected/<trap>/<table>/ (never in place);
  * gold is computed from the CLEAN values;
  * `validate()` runs a naive and a careful reference solution on the dirty
    copy as read back from disk: a case is admitted only if naive runs without
    raising, lands far from gold (not a near-miss) and careful hits gold.

Only defects whose naive path never raises are kept. Format violations
(thousands separators, unit suffixes, decimal commas) were dropped after the
smoke: the column loads as text, the first attempt crashes, and the traceback
gives the trap away — crash recovery, not a silent error.

Data is CC BY-NC 4.0 and is not vendored — neither source nor injected copy.
"""

from __future__ import annotations

import hashlib
import random
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
SRC = _ROOT / "runs" / "_external" / "infiagent" / "da-dev-tables"
DST = _ROOT / "runs" / "_external" / "infiagent_injected"
NEAR_MISS = 0.05          # naive must be off by more than 5% of |gold|

Frames = dict[str, pd.DataFrame]


def _rng(key: str) -> random.Random:
    return random.Random(int(hashlib.md5(key.encode()).hexdigest()[:8], 16))


@dataclass
class LoopCase:
    """Duck-types SilentTrapCase for run_distill_ablation.run_one."""

    trap: str
    table: str                                        # source file under SRC
    request: str
    make: Callable[[pd.DataFrame, random.Random], tuple[Frames, float]]   # clean → (dirty files, gold)
    naive: Callable[[Frames], float]                  # on the dirty files as read back from disk
    careful: Callable[[Frames], float]
    tolerance: float = 0.011                          # answers are asked to 2 decimals
    gold: float = 0.0

    @property
    def id(self) -> str:
        return f"{self.trap}__{Path(self.table).stem.replace(' ', '_')[:14]}"

    @property
    def dirty_dir(self) -> Path:
        return DST / self.trap / Path(self.table).stem

    def materialise(self) -> Path:
        frames, gold = self.make(pd.read_csv(SRC / self.table), _rng(self.id))
        self.gold = round(float(gold), 2)
        if self.dirty_dir.exists():
            shutil.rmtree(self.dirty_dir)
        self.dirty_dir.mkdir(parents=True)
        for name, df in frames.items():
            df.to_csv(self.dirty_dir / name, index=False)
        return self.dirty_dir

    def build(self, data_dir: str) -> str:
        src = self.materialise()
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        for f in src.iterdir():
            shutil.copy(f, Path(data_dir) / f.name)
        return data_dir

    def validate(self) -> dict:
        frames = {f.name: pd.read_csv(f) for f in self.materialise().iterdir()}
        out = {"case": self.id, "gold": self.gold}
        for name, fn in (("naive", self.naive), ("careful", self.careful)):
            try:
                out[name] = round(float(fn({k: v.copy() for k, v in frames.items()})), 2)
            except Exception as e:  # noqa: BLE001 — a raising naive path is not a silent trap
                out[name] = None
                out[f"{name}_error"] = f"{type(e).__name__}: {e}"
        n, c = out["naive"], out["careful"]
        out["naive_rel_err"] = None if n is None else round(abs(n - self.gold) / max(abs(self.gold), 1e-9), 4)
        out["bites"] = n is not None and n == n and out["naive_rel_err"] > NEAR_MISS
        out["fixable"] = c is not None and abs(c - self.gold) <= self.tolerance
        out["admitted"] = out["bites"] and out["fixable"]
        return out


def _pick(n: int, frac: float, rng: random.Random) -> list[int]:
    return sorted(rng.sample(range(n), max(1, round(n * frac))))


# --- footer: a trailing summary row carrying the column total ---------------

def _footer(table: str, col: str, label_col: str) -> LoopCase:
    def make(df, rng):
        foot = {c: None for c in df.columns}
        foot[label_col], foot[col] = "Total", df[col].sum()
        return {table: pd.concat([df, pd.DataFrame([foot])], ignore_index=True)}, df[col].sum()

    return LoopCase("footer", table, f"读取 {table},计算 {col} 列的总和,打印最终结果(保留两位小数)。", make,
                    naive=lambda fr: fr[table][col].sum(),
                    careful=lambda fr: fr[table][fr[table][label_col].astype(str) != "Total"][col].sum())


# --- duplicates: exact duplicate records of entities that have an id --------

def _dups(table: str, col: str, id_col: str) -> LoopCase:
    def make(df, rng):
        assert df[id_col].is_unique and not df.duplicated().any()
        extra = df.iloc[_pick(len(df), 0.12, rng)]
        return {table: pd.concat([df, extra]).sort_index(kind="stable").reset_index(drop=True)}, df[col].sum()

    return LoopCase("duplicates", table, f"读取 {table},计算 {col} 列的总和,打印最终结果(保留两位小数)。", make,
                    naive=lambda fr: fr[table][col].sum(),
                    careful=lambda fr: fr[table].drop_duplicates()[col].sum())


# --- sentinel: -1 standing in for "missing" in a strictly positive column ---

def _sentinel(table: str, col: str) -> LoopCase:
    def make(df, rng):
        assert (df[col].dropna() > 0).all()
        hit = _pick(len(df), 0.15, rng)
        dirty = df.copy()
        dirty[col] = dirty[col].astype(float)
        dirty.iloc[hit, dirty.columns.get_loc(col)] = -1
        return {table: dirty}, df[col].drop(df.index[hit]).mean()

    return LoopCase("sentinel", table, f"读取 {table},计算 {col} 列的平均值,打印最终结果(保留两位小数)。", make,
                    naive=lambda fr: fr[table][col].mean(),
                    careful=lambda fr: fr[table][col][fr[table][col] >= 0].mean())


# --- key variants: case / whitespace variants of one category value ---------

_VARIANTS = (str.upper, str.title, lambda s: s + " ", lambda s: " " + s, str.lower)


def _key_variants(table: str, key: str, value: str, col: str) -> LoopCase:
    def make(df, rng):
        rows = df.index[df[key] == value].tolist()
        dirty = df.copy()
        for i in rng.sample(rows, round(len(rows) * 0.35)):
            alt = [f(value) for f in _VARIANTS if f(value) != value]
            dirty.loc[i, key] = rng.choice(alt)
        return {table: dirty}, df.loc[df[key] == value, col].sum()

    return LoopCase("key_variants", table,
                    f"读取 {table},计算 {key} 为 {value} 的记录的 {col} 列总和,打印最终结果(保留两位小数)。", make,
                    naive=lambda fr: fr[table].loc[fr[table][key] == value, col].sum(),
                    careful=lambda fr: fr[table].loc[fr[table][key].astype(str).str.strip().str.lower()
                                                     == value.lower(), col].sum())


# --- synonyms: the same category written as different words ----------------

def _synonyms(table: str, key: str, value: str, alts: tuple[str, ...], col: str, frac: float = 0.35) -> LoopCase:
    def make(df, rng):
        rows = df.index[df[key] == value].tolist()
        dirty = df.copy()
        for i in rng.sample(rows, max(2, round(len(rows) * frac))):
            dirty.loc[i, key] = rng.choice(alts)
        return {table: dirty}, df.loc[df[key] == value, col].sum()

    return LoopCase("synonyms", table,
                    f"读取 {table},计算 {key} 为 {value} 的记录的 {col} 列总和,打印最终结果(保留两位小数)。", make,
                    naive=lambda fr: fr[table].loc[fr[table][key] == value, col].sum(),
                    careful=lambda fr: fr[table].loc[fr[table][key].isin((value, *alts)), col].sum())


# --- groupby dropna: rows whose group key is missing vanish from the result -

def _groupby_nan(table: str, key: str, col: str) -> LoopCase:
    def make(df, rng):
        assert df[key].notna().all()
        dirty = df.copy()
        dirty.iloc[_pick(len(df), 0.15, rng), dirty.columns.get_loc(key)] = None
        return {table: dirty}, df[col].sum()

    return LoopCase("groupby_nan", table,
                    f"读取 {table},按 {key} 分组汇总 {col},打印所有分组小计之和(保留两位小数)。", make,
                    naive=lambda fr: fr[table].groupby(key)[col].sum().sum(),
                    careful=lambda fr: fr[table].groupby(key, dropna=False)[col].sum().sum())


# --- joins: the real table split into fact + lookup, defect on the lookup ---

def _split(df, key, attr):
    return df.drop(columns=[attr]), df[[key, attr]].drop_duplicates().reset_index(drop=True)


def _join(trap: str, table: str, key: str, attr: str, value: str, col: str, lookup: str) -> LoopCase:
    norm = lambda s: s.astype(str).str.strip().str.lower()          # noqa: E731

    def make(df, rng):
        fact, dim = _split(df, key, attr)
        assert dim[key].is_unique
        hit = _pick(len(dim), 0.3 if trap != "join_fanout" else 0.2, rng)
        if trap == "join_key":
            for i in hit:
                k = dim.loc[i, key]
                dim.loc[i, key] = rng.choice([f(k) for f in (str.upper, str.title, lambda s: s + " ") if f(k) != k])
            gold = df.loc[df[attr] == value, col].sum()
        elif trap == "join_fanout":
            dim = pd.concat([dim, dim.iloc[hit]]).sort_index(kind="stable").reset_index(drop=True)
            gold = df.loc[df[attr] == value, col].sum()
        else:                                   # join_loss
            dim = dim.drop(index=hit).reset_index(drop=True)
            gold = df[col].sum()
        return {table: fact, lookup: dim}, gold

    def naive(fr):
        m = fr[table].merge(fr[lookup], on=key)
        return m[col].sum() if trap == "join_loss" else m.loc[m[attr] == value, col].sum()

    def careful(fr):
        fact, dim = fr[table], fr[lookup]
        if trap == "join_loss":
            return fact.merge(dim, on=key, how="left")[col].sum()
        fact, dim = fact.assign(_k=norm(fact[key])), dim.assign(_k=norm(dim[key])).drop(columns=[key])
        m = fact.merge(dim.drop_duplicates(), on="_k", how="left")
        return m.loc[m[attr] == value, col].sum()

    if trap == "join_loss":
        req = (f"读取 {table} 和 {lookup},按 {key} 为 {table} 的每条记录关联上 {attr},"
               f"然后计算全部记录的 {col} 列总和,打印最终结果(保留两位小数)。")
    else:
        req = (f"读取 {table} 和 {lookup},按 {key} 关联两表,计算 {attr} 为 {value} 的记录的 {col} 列总和,"
               f"打印最终结果(保留两位小数)。")
    return LoopCase(trap, table, req, make, naive, careful)


_GAP = ("gapminder_cleaned.csv", "country", "continent", "asia", "pop", "country_continent.csv")
_MAL = ("estimated_numbers.csv", "Country", "WHO Region", "Africa", "No. of cases_median", "country_region.csv")

TRAPS: dict[str, list[LoopCase]] = {
    "footer": [_footer("insurance.csv", "charges", "age"), _footer("Credit.csv", "Balance", "Unnamed: 0"),
               _footer("hotel_data.csv", "review_count", "hotel_name")],
    "duplicates": [_dups("Credit.csv", "Limit", "Unnamed: 0"), _dups("hotel_data.csv", "review_count", "hotel_id"),
                   _dups("titanic.csv", "Fare", "PassengerId")],
    "sentinel": [_sentinel("insurance.csv", "bmi"), _sentinel("gapminder_cleaned.csv", "lifeexp"),
                 _sentinel("auto-mpg.csv", "horsepower")],
    "key_variants": [_key_variants("insurance.csv", "region", "southeast", "charges"),
                     _key_variants("Credit.csv", "Ethnicity", "Asian", "Balance"),
                     _key_variants("hotel_data.csv", "city_name", "Brooklyn", "review_count")],
    # gapminder goes last (unused at --tables 2): its admission run printed 0 because the agent
    # re-capitalised the lowercase value in the request — wrong for a reason that is not the trap.
    "synonyms": [_synonyms("hotel_data.csv", "city_name", "New York City", ("NYC", "New York"), "review_count"),
                 _synonyms("titanic.csv", "Embarked", "S", ("Southampton",), "Fare"),
                 _synonyms("gapminder_cleaned.csv", "country", "united states", ("usa", "u.s.a."), "pop", 0.5)],
    "groupby_nan": [_groupby_nan("insurance.csv", "region", "charges"),
                    _groupby_nan("Credit.csv", "Ethnicity", "Balance"),
                    _groupby_nan("hotel_data.csv", "city_name", "review_count")],
    "join_key": [_join("join_key", *_GAP), _join("join_key", *_MAL)],
    "join_fanout": [_join("join_fanout", *_GAP), _join("join_fanout", *_MAL)],
    "join_loss": [_join("join_loss", *_GAP), _join("join_loss", *_MAL)],
}


if __name__ == "__main__":
    for cs in TRAPS.values():
        for c in cs:
            v = c.validate()
            print(f"{'ADMIT' if v['admitted'] else 'DROP ':5s} {v['case']:34s} gold={v['gold']:<16} naive={v['naive']!s:<16} "
                  f"rel_err={v['naive_rel_err']} careful={v['careful']} "
                  f"{v.get('naive_error', '')}{v.get('careful_error', '')}")
