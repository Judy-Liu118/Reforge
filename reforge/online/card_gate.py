"""Card quality gate — cheap, deterministic checks before a card enters the bank.

    hard reject   a field is empty → nothing worth keeping
    soft fail     surface leak (digits / file or column names), fewer than two
                  actionable steps, too close to the pre-registered placebo
                  card (generic diligence, no strategy), or overlong
                  → the card is kept in quarantine, never injected

The placebo text is the PLACEBO_CARD of scripts/run_distill_ablation.py,
copied verbatim: in that ablation it moved nothing (placebo = 0), which makes
it a fair negative anchor for "says nothing actionable".
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from reforge.memory.substrate import _char_ngrams, _cosine
from reforge.memory.writer import _card_violations

PLACEBO_TEXT = ("做数据任务时保持仔细 适用于各类读取文件并计算结果的数据处理任务。 "
                "动手前认真阅读任务要求。 编写代码时保持仔细、有条理。 "
                "计算完成后核对一下结果再输出。 确保输出格式符合任务要求。")

MIN_ACTIONABLE = 2
GENERIC_SIM = 0.6
DUPLICATE_SIM = 0.8
MAX_CONTENT = 1200

_BULLET = re.compile(r"^\s*(?:[-*•·]|\d+[.、)]|[（(]?\d+[)）])\s*")
_ACTION = re.compile(
    r"检查|确认|核对|校验|验证|排除|剔除|过滤|去重|删除|去掉|统一|标准化|规范|转换|识别|区分|对比|比较|统计|计数|"
    r"打印|输出前|先|再|避免|不要|切勿|确保|改用|使用|按|对齐|映射|合并前|关联前|分组前|"
    r"check|verify|drop|filter|dedup|normali[sz]e|strip|compare|count|ensure|avoid",
    re.I)


@dataclass(frozen=True)
class GateResult:
    ok: bool
    hard_reject: bool
    reasons: tuple[str, ...]


def actionable_steps(content: str) -> int:
    n = 0
    for line in (content or "").splitlines():
        text = _BULLET.sub("", line).strip()
        if len(text) >= 6 and _ACTION.search(text):
            n += 1
    return n


def _text(body: dict) -> str:
    return " ".join(str(body.get(k, "")) for k in ("title", "description", "content"))


def check(body: dict, *, forbidden_terms: tuple[str, ...] = ()) -> GateResult:
    empty = [k for k in ("title", "description", "content") if not str(body.get(k, "")).strip()]
    if empty:
        return GateResult(False, True, tuple(f"empty:{k}" for k in empty))
    reasons: list[str] = []
    leaks = sorted(set(body.get("violations") or []) | set(_card_violations(body, forbidden_terms)))
    if leaks:
        reasons.append(f"leak:{','.join(leaks)}")
    n = actionable_steps(str(body["content"]))
    if n < MIN_ACTIONABLE:
        reasons.append(f"not_actionable:{n}")
    sim = _cosine(_char_ngrams(_text(body)), _char_ngrams(PLACEBO_TEXT))
    if sim >= GENERIC_SIM:
        reasons.append(f"generic:{sim:.2f}")
    if len(str(body["content"])) > MAX_CONTENT:
        reasons.append("too_long")
    return GateResult(not reasons, False, tuple(reasons))


def find_duplicate(body: dict, cards: Iterable, threshold: float = DUPLICATE_SIM):
    """Most similar existing card (any status, evicted included) above *threshold*."""
    q = _char_ngrams(_text(body))
    best, best_s = None, threshold
    for c in cards:
        s = _cosine(q, _char_ngrams(_text(c.body)))
        if s >= best_s:
            best, best_s = c, s
    return best
