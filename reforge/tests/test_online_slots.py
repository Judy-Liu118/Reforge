"""Task slot parsing — the gold-free recall key for signal-free cards (pre-registration v2)."""

from __future__ import annotations

from reforge.online.slots import operation, slot_match, table_family, task_slots

INFI = ("Read the file test_ave.csv in the current directory and answer:\nIdentify and remove any outliers in the "
        "\"Fare\" column. Provide the number of removed outliers.\nConstraints: compute the mean first.")


def test_operation_priority_and_tokens():
    assert operation("读取 hotel_data.csv,计算 review_count 列的总和,打印最终结果") == "sum"   # not "count"
    assert operation("读取 a.csv,按 city 分组汇总 x,打印所有分组小计之和") == "group"
    assert operation("读取 a.csv,计算 city 为 NYC 的记录的 x 列总和") == "filter_agg"
    assert operation("读取 a.csv,计算 horsepower 列的平均值") == "mean"
    assert operation(INFI) == "outlier"                          # question part only, plural ok
    assert operation("Read x.csv and answer:\nWhat is the vibe?") == "other"


def test_table_family():
    assert table_family("hotel_data.csv") == "hotel"
    assert table_family("gapminder_clean.csv") == "gapminder"
    assert table_family("Credit.csv") == "credit"


def test_columns_from_header(tmp_path):
    (tmp_path / "test_ave.csv").write_text("PassengerId,Fare,Age\n1,2.5,3\n", encoding="utf-8")
    s = task_slots(INFI, ("test_ave.csv",), tmp_path)
    assert s == {"op": "outlier", "columns": ["fare"], "tables": ["test_ave.csv"], "families": ["test_ave"]}
    assert task_slots(INFI, ("missing.csv",), tmp_path)["columns"] == []


def test_slot_match():
    a = {"op": "sum", "columns": ["balance"], "families": ["credit"]}
    assert slot_match(a, {"op": "sum", "columns": [], "families": ["credit"]})
    assert slot_match(a, {"op": "sum", "columns": ["balance"], "families": ["x"]})
    assert not slot_match(a, {"op": "mean", "columns": ["balance"], "families": ["credit"]})
    assert not slot_match(a, {"op": "sum", "columns": ["limit"], "families": ["x"]})
    assert not slot_match({**a, "op": "other"}, {**a, "op": "other"})
    assert not slot_match({}, a) and not slot_match(a, None)
