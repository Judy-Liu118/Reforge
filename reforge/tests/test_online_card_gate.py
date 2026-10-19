"""Card quality gate."""

from __future__ import annotations

from types import SimpleNamespace

from reforge.online.card_gate import actionable_steps, check, find_duplicate

GOOD = {"title": "聚合前排除汇总行", "description": "对数值列求和或求均值的任务。",
        "content": "- 求和前检查文本列末尾是否有合计/总计行,有则剔除。\n"
                   "- 对比剔除前后的行数,确认只去掉了汇总行。\n- 打印结果前再核对一次过滤条件。",
        "polarity": "failure", "violations": []}


def test_good_card_passes():
    r = check(GOOD, forbidden_terms=("insurance.csv", "charges"))
    assert r.ok and not r.hard_reject and r.reasons == ()


def test_empty_field_is_hard_reject():
    r = check({**GOOD, "content": "  "})
    assert r.hard_reject and r.reasons == ("empty:content",)


def test_leak_is_soft_fail():
    r = check({**GOOD, "content": GOOD["content"] + "\n- 检查 charges 列是否超过 1000"},
              forbidden_terms=("charges",))
    assert not r.ok and not r.hard_reject
    assert r.reasons[0].startswith("leak:") and "charges" in r.reasons[0] and "<digit>" in r.reasons[0]


def test_distiller_reported_violation_kept():
    assert not check({**GOOD, "violations": ["<digit>"]}).ok


def test_not_actionable():
    r = check({**GOOD, "content": "数据质量很重要。\n要有耐心。"})
    assert any(x.startswith("not_actionable:") for x in r.reasons)
    assert actionable_steps(GOOD["content"]) == 3


def test_placebo_is_generic():
    placebo = {"title": "做数据任务时保持仔细", "description": "适用于各类读取文件并计算结果的数据处理任务。",
               "content": "- 动手前认真阅读任务要求。\n- 编写代码时保持仔细、有条理。\n"
                          "- 计算完成后核对一下结果再输出。\n- 确保输出格式符合任务要求。"}
    assert any(x.startswith("generic:") for x in check(placebo).reasons)


def test_find_duplicate():
    cards = [SimpleNamespace(card_id="a", body=GOOD),
             SimpleNamespace(card_id="b", body={"title": "关联前统一键格式", "description": "两表关联。",
                                                "content": "- 关联前去掉键两端空格并统一大小写。"})]
    near = {**GOOD, "title": "聚合前排除汇总行!"}
    assert find_duplicate(near, cards).card_id == "a"
    assert find_duplicate({"title": "完全不同", "description": "无关", "content": "- 画图前设置字体"}, cards) is None
