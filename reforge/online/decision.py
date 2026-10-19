"""Tiered, gold-free verdict + the confidence gate that acts on it.

Cost tiers, cheapest first; the critic is called only when needed:

    L0      runtime gave up (FAILED)                      → fail, conf 1.0
    L1b     metamorphic re-run: repairing the data's danger
            signals moves the printed answer              → fail, conf 0.95
            (repaired run crashed                         → fail, conf 0.8)
    critic  L1 found an unhandled signal that L1b could not
            confirm (residual)                            → always critic
    cheap   nothing above fired                           → "unverified",
            conf 0.4; critic only on an audit sample

Why a cheap pass is NOT a success: the cheap tiers only see the contaminants
they were written for. On InfiAgent's naturally occurring errors the
metamorphic re-run caught 0%, so "the cheap tiers let it through" is no
evidence of correctness. An unverified run is closed (no retry) but never
counts as success and never yields a card; audits send a share of them to
the critic and the shadow log measures the miss rate.

The confidence gate (τ = 0.7, pre-registered, UNCALIBRATED and provisional —
shadow pairs (self_conf, correct) from the first full run decide whether it
stands or k=3 consistency replaces it):

    fail  & conf >= τ   confident failure → distil card, retry once with it
    pass  & conf >= τ   confident success → done, no retry
    unverified          done, no card, not a success
    uncertain           (abstain / conf < τ / conf missing, malformed or out
                        of range / critic error) → review queue, no card,
                        no retry
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

TAU = 0.7                 # provisional, uncalibrated
BORDER = 0.8              # τ <= conf < BORDER on a critic fail → card goes to quarantine
UNVERIFIED_CONF = 0.4
L0_CONF, L1B_CONF, L1B_CRASH_CONF = 1.0, 0.95, 0.8


class CriticUnavailable(RuntimeError):
    """Critic budget exhausted — pause the loop instead of flooding review."""


def normalize_confidence(x) -> float | None:
    """A number in [0, 1], else None. None is treated as uncertain, never as high."""
    if isinstance(x, bool):
        return None
    if isinstance(x, str):
        try:
            x = float(x.strip())
        except ValueError:
            return None
    if isinstance(x, (int, float)) and math.isfinite(x) and 0.0 <= x <= 1.0:
        return float(x)
    return None


@dataclass(frozen=True)
class Verdict:
    label: str                       # fail | pass | unverified | uncertain
    confidence: float | None
    tier: str                        # L0 | L1b | cheap | critic | critic_k3
    reason: str
    evidence: str = ""               # gold-free diagnosis, the distiller's feedback
    signals: tuple[str, ...] = ()
    audited: bool = False
    critic_called: bool = False
    critic_verdict: str | None = None
    critic_conf: float | None = None
    critic_raw_conf: object = None
    critic_error: str = ""
    critic_tokens: int = 0
    critic_samples: tuple = field(default_factory=tuple)

    @property
    def confident_fail(self) -> bool:
        return self.label == "fail" and self.confidence is not None and self.confidence >= TAU

    @property
    def confident_pass(self) -> bool:
        return self.label == "pass" and self.confidence is not None and self.confidence >= TAU

    @property
    def borderline(self) -> bool:
        return self.confident_fail and self.tier.startswith("critic") and self.confidence < BORDER

    def payload(self) -> dict:
        d = asdict(self)
        d["signals"] = list(self.signals)
        d["critic_samples"] = list(self.critic_samples)
        d["critic_raw_conf"] = repr(self.critic_raw_conf) if self.critic_raw_conf is not None else None
        return d


def _critic_verdict(cv: dict, base: dict, tau: float) -> Verdict:
    if cv.get("budget_exhausted"):
        raise CriticUnavailable(cv.get("error", "critic budget exhausted"))
    v, conf = cv.get("verdict"), normalize_confidence(cv.get("confidence"))
    common = {**base, "critic_called": True, "critic_verdict": v, "critic_conf": conf,
              "critic_raw_conf": cv.get("confidence"), "critic_error": cv.get("error", ""),
              "critic_tokens": cv.get("total_tokens", 0), "critic_samples": tuple(cv.get("samples", ())),
              "tier": cv.get("tier", "critic")}
    if cv.get("error"):
        return Verdict(label="uncertain", confidence=None, reason="critic_error", **common)
    if v not in ("wrong", "ok"):
        return Verdict(label="uncertain", confidence=conf, reason="critic_abstain", **common)
    if conf is None:
        return Verdict(label="uncertain", confidence=None, reason="critic_conf_invalid", **common)
    if conf < tau:
        return Verdict(label="uncertain", confidence=conf, reason="critic_low_conf", **common)
    diag = (f"打印值 {cv.get('printed')}。独立审查给出的疑点:{cv.get('suspect', '') or '(无)'};"
            f"依据:{cv.get('evidence', '') or '(无)'}")
    return Verdict(label="fail" if v == "wrong" else "pass", confidence=conf,
                   reason=f"critic_{v}", evidence=diag if v == "wrong" else "", **common)


def decide(cheap: dict, critic: Callable[[], dict], *, audit: bool, tau: float = TAU) -> Verdict:
    """*cheap*: {l0_failed, l0_reason, signals, l1: {unhandled}, l1b: {fail, status, orig, repaired}}.
    *critic*: zero-arg callable, invoked only if a tier needs it."""
    signals = tuple(cheap.get("signals", ()))
    l1, l1b = cheap.get("l1") or {}, cheap.get("l1b") or {}
    if cheap.get("l0_failed"):
        return Verdict("fail", L0_CONF, "L0", "runtime_failed",
                       evidence=f"程序运行报错且重试耗尽:{cheap.get('l0_reason', '') or '(无详情)'}", signals=signals)
    if l1b.get("fail"):
        crashed = l1b.get("status") == "repaired_run_crashed"
        ev = (f"把数据中的可疑问题({', '.join(signals)})修复后,同一段代码"
              + ("运行崩溃" if crashed else f"的输出从 {l1b.get('orig')} 变为 {l1b.get('repaired')}")
              + "——结果对这些数据问题敏感,代码没有正确处理它们。")
        return Verdict("fail", L1B_CRASH_CONF if crashed else L1B_CONF, "L1b",
                       "metamorphic_crash" if crashed else "metamorphic_moved", evidence=ev, signals=signals)
    if l1.get("unhandled"):
        return _critic_verdict(critic(), {"signals": signals, "audited": False}, tau)
    if audit:
        v = _critic_verdict(critic(), {"signals": signals, "audited": True}, tau)
        return v
    return Verdict("unverified", UNVERIFIED_CONF, "cheap", "cheap_tiers_silent", signals=signals)
