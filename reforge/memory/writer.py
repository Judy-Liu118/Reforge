"""Memory write helper — extract MemoryRecord from a completed RuntimeState.

Shared by RuntimeRunner (automatic write-back) and CLI (display tag).
Returns None when the run is not worth persisting (intentional demo, etc.).
"""
from __future__ import annotations

import json
import os
import re
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable

from reforge.memory.models import MemoryRecord, should_persist_memory

# ---------------------------------------------------------------------------
# Distilled-strategy memory — EXPERIMENT ONLY, off by default.
#
# With REFORGE_EXP_DISTILLED_MEMORY=1 every finished trajectory is distilled
# into a de-specified strategy card (Title / Description / Content) stored on
# MemoryRecord.distilled_strategy. Polarity (success vs failure prompt) comes
# from an external oracle the caller installs via `distill_label_oracle` —
# the runtime's own outcome label cannot be trusted here, because the tasks
# this targets exit 0 with a wrong number and the evaluator passes them.
# No oracle installed → no card: an unlabelled card would carry the
# evaluator's blind spot straight into memory.
# ---------------------------------------------------------------------------

DISTILL_FLAG_ENV = "REFORGE_EXP_DISTILLED_MEMORY"


@dataclass(frozen=True)
class DistillLabel:
    """Ground-truth verdict for one trajectory, supplied by the caller."""

    correct: bool
    feedback: str = ""      # e.g. "printed X, expected Y" — distiller-only
    forbidden_terms: tuple[str, ...] = ()   # surface tokens the card must not contain


distill_label_oracle: ContextVar[Callable[[object], DistillLabel | None] | None] = (
    ContextVar("distill_label_oracle", default=None)
)


def distilled_memory_enabled() -> bool:
    return os.environ.get(DISTILL_FLAG_ENV, "").strip() == "1"


_DISTILL_SYSTEM = """你是经验蒸馏器。输入是一个数据处理 agent 的一次完整轨迹，以及这次结果对错的外部判定。
你的任务是把它蒸馏成一张**可迁移到其他任务**的推理策略卡。

硬性要求:
- 卡片必须去具体化:禁止出现任何具体的文件名、列名、字段名、取值、类别名、数字。
  用抽象说法代替,如"数值列""关联键""维度表""分组键""汇总行"。
- 写的是做事的习惯/检查步骤,不是这道题的答案。
- 只输出一个 JSON 对象,键为 title、description、content,不要输出其他文字。
  title: 不超过 20 字;description: 一句话说明适用场景;content: 3-5 条具体可执行的检查/做法。"""

_SUCCESS_PROMPT = """这次轨迹的结果被外部判定为【正确】。
总结它为什么能做对:哪些检查或处理步骤保证了结果正确,提炼成可迁移的策略。"""

_FAILURE_PROMPT = """这次轨迹的结果被外部判定为【错误】(程序正常运行、没有报错,但打印的数是错的)。
反思它为什么会错:最可能是哪一步静默地引入了偏差,以及今后如何预防,提炼成可迁移的预防策略。
下面的判定反馈只用于帮你推理错因,不得把其中的数值写进卡片。"""

_DIGIT_RE = re.compile(r"\d")


def _card_violations(card: dict, forbidden: tuple[str, ...]) -> list[str]:
    text = " ".join(str(card.get(k, "")) for k in ("title", "description", "content"))
    hits = [t for t in forbidden if t and t.lower() in text.lower()]
    if _DIGIT_RE.search(text):
        hits.append("<digit>")
    return hits


def _parse_card(raw: str) -> dict:
    match = re.search(r"\{.*\}", raw or "", re.S)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    card = {k: data.get(k, "") for k in ("title", "description", "content")}
    if isinstance(card["content"], list):
        card["content"] = "\n".join(f"- {c}" for c in card["content"])
    card = {k: str(v).strip() for k, v in card.items()}
    return card if all(card.values()) else {}


def _distiller_client():
    from reforge.models.adapters.llm_client import LLMClient

    # Optional dedicated endpoint so the distiller can be stronger than the
    # agent; falls through to the default LLM_* config when unset.
    return LLMClient(
        base_url=os.environ.get("REFORGE_DISTILL_BASE_URL") or None,
        api_key=os.environ.get("REFORGE_DISTILL_API_KEY") or None,
        model=os.environ.get("REFORGE_DISTILL_MODEL") or None,
    )


def distill_strategy(state: object, label: DistillLabel) -> dict:
    """Distil *state*'s trajectory into a de-specified strategy card.

    One retry when the card leaks surface detail (digits / forbidden terms);
    the second result is kept either way and `violations` records any leak,
    so the experiment can report it rather than silently drop the card.
    """
    ss = getattr(state, "semantic_state", None)
    es = getattr(state, "exec_state", None)
    trajectory = (
        f"## 任务\n{getattr(state, 'user_request', '')}\n\n"
        f"## 计划\n{(getattr(ss, 'plan', None) or '')[:2000]}\n\n"
        f"## 最终代码\n{(getattr(state, 'generated_code', '') or '')[:4000]}\n\n"
        f"## stdout(末尾)\n{(getattr(es, 'stdout', '') or '')[-1500:]}\n\n"
        f"## 尝试次数\n{len(getattr(state, 'attempts', []) or [])}"
    )
    polarity_prompt = _SUCCESS_PROMPT if label.correct else _FAILURE_PROMPT
    user_msg = f"{polarity_prompt}\n\n## 判定反馈\n{label.feedback or '(无)'}\n\n{trajectory}"

    client = _distiller_client()
    card: dict = {}
    violations: list[str] = []
    for _ in range(2):
        card = _parse_card(client.chat(_DISTILL_SYSTEM, user_msg))
        violations = _card_violations(card, label.forbidden_terms) if card else ["<unparseable>"]
        if not violations:
            break
        user_msg += (
            f"\n\n上一版卡片不合格(含具体信息:{violations})。"
            "重新输出,彻底去掉具体名称和数字。"
        )
    if not card:
        return {}
    return {
        **card,
        "polarity": "success" if label.correct else "failure",
        "violations": violations,
    }


def record_from_final_state(state: object, session_id: str) -> MemoryRecord | None:
    """Build a MemoryRecord from *state* after a run completes.

    Returns None when the outcome does not qualify for persistence
    (e.g. intentional failure accepted without recovery).

    *state* is typed as object to avoid a hard import of RuntimeState here;
    the function accesses only well-known attributes via getattr so it
    remains importable without pulling in the full runtime dependency tree.
    """
    ss = getattr(state, "semantic_state", None)
    cs = getattr(state, "control_state", None)
    os_ = getattr(state, "outcome_state", None)
    clf = getattr(state, "classification_result", None)
    attempts = getattr(state, "attempts", [])

    outcome = (os_.task_outcome if os_ else None) or "UNKNOWN"

    # Extract primary error type from attempts (first occurrence)
    primary_error = ""
    for a in attempts:
        if getattr(a, "error_type", ""):
            primary_error = a.error_type
            break

    # Reflection text and recovery actions from SemanticState
    reflection_text = (ss.reflection_summary if ss else None) or ""
    recovery_actions: list[str] = []
    rr = ss.reflection_result if ss else None
    if rr:
        reflection_text = reflection_text or getattr(rr, "error_summary", "")
        fix = getattr(rr, "suggested_fix", "")
        if fix:
            recovery_actions.append(fix)

    is_intentional = bool(clf.is_expected_failure) if clf else False
    requires_recovery = (bool(clf.retryable) if clf else False) and is_intentional
    retry_count = cs.retry_count if cs else 0
    decision_reason = (os_.outcome_reason if os_ else None) or ""
    traceback = getattr(state, "traceback", "")

    # Experiment: every labelled trajectory yields a card, including the
    # clean-exit wrong answers should_persist_memory() would keep or drop
    # on the runtime's (blind) outcome label.
    oracle = distill_label_oracle.get() if distilled_memory_enabled() else None
    label = oracle(state) if oracle is not None else None

    if label is None and not should_persist_memory(
        outcome=outcome,
        decision_reason=decision_reason,
        error_type=primary_error,
        retry_count=retry_count,
        is_intentional=is_intentional,
        requires_recovery=requires_recovery,
    ):
        return None

    record = MemoryRecord.from_session(
        session_id=session_id,
        user_request=getattr(state, "user_request", ""),
        outcome=outcome,
        retry_count=retry_count,
        error_type=primary_error,
        reflection_summary=reflection_text,
        recovery_action="; ".join(recovery_actions),
        traceback=traceback,
    )
    if label is not None:
        record.distilled_strategy = distill_strategy(state, label)
    return record


def repair_record_from_final_state(state: object) -> dict | None:
    """Build ExecutionMemory.record() kwargs for a *proven repair*.

    This is the write side of the governor's repair recall: ClassifyStage
    calls ExecutionMemory.recall_similar() with the current failure's
    fingerprint, so something has to persist (signature → repair that
    worked) pairs. Only RECOVERED sessions carrying a concrete fix qualify
    — hence "repair record" rather than "execution record": a record
    without a proven repair carries no hint value and would shadow useful
    records in the top-3 recall window.

    Returns None (the common case) when the session doesn't qualify: no
    failure snapshot, not recovered, or reflection produced no concrete fix.
    """
    ss = getattr(state, "semantic_state", None)
    os_ = getattr(state, "outcome_state", None)
    snapshot = getattr(ss, "last_failure", None) if ss else None

    outcome = (os_.task_outcome if os_ else None) or ""
    if outcome != "RECOVERED" or snapshot is None:
        return None
    suggested_fix = getattr(snapshot, "suggested_fix", "")
    if not suggested_fix:
        return None

    return {
        "request": getattr(state, "user_request", ""),
        "outcome": outcome,
        "failure_mode": getattr(snapshot, "failure_mode", "") or "execution_error",
        "retryable": True,
        "repair_strategy": suggested_fix,
        "task_intent": (ss.task_intent if ss else None) or "",
        # May be {} when the reflection node had no structural signal;
        # ExecutionMemory.record() treats that as "no signature" and
        # derives a coarse fingerprint from error_type instead.
        "problem_signature": getattr(snapshot, "problem_signature", {}),
        "error_type": getattr(snapshot, "error_type", ""),
    }
