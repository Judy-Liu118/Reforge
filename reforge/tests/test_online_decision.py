"""Tiered decision + confidence gate (pure), and the frozen-ladder adapter."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from reforge.online.decision import TAU, CriticUnavailable, decide, normalize_confidence

NO_CRITIC = lambda: pytest.fail("critic must not be called")  # noqa: E731


def _critic(verdict="wrong", conf=0.9, **kw):
    calls = []

    def f():
        calls.append(1)
        return {"verdict": verdict, "confidence": conf, "suspect": "s", "evidence": "e", "printed": 3.0,
                "total_tokens": 10, **kw}
    f.calls = calls
    return f


@pytest.mark.parametrize("raw,want", [
    (0.9, 0.9), (1, 1.0), (0, 0.0), ("0.75", 0.75), (" 0.8 ", 0.8),
    (None, None), (True, None), (1.2, None), (-0.1, None), ("80%", None), ("high", None),
    (float("nan"), None), ([0.9], None),
])
def test_normalize_confidence(raw, want):
    assert normalize_confidence(raw) == want


def test_l0_fail_skips_critic():
    v = decide({"l0_failed": True, "l0_reason": "KeyError"}, NO_CRITIC, audit=True)
    assert v.label == "fail" and v.confident_fail and v.tier == "L0" and "KeyError" in v.evidence


def test_l1b_moved_is_confident_fail_without_critic():
    v = decide({"signals": ["footer"], "l1": {"unhandled": ["footer"]},
                "l1b": {"fail": True, "status": "ran", "orig": 10.0, "repaired": 5.0}}, NO_CRITIC, audit=True)
    assert v.confident_fail and v.tier == "L1b" and not v.borderline and "footer" in v.evidence


def test_residual_always_goes_to_critic():
    c = _critic("wrong", 0.9)
    v = decide({"signals": ["footer"], "l1": {"unhandled": ["footer"]}, "l1b": {"fail": False, "status": "not_reproduced"}},
               c, audit=False)
    assert c.calls and v.confident_fail and v.tier == "critic" and "疑点" in v.evidence and not v.audited


def test_cheap_pass_is_unverified_not_success():
    v = decide({"signals": [], "l1": {"unhandled": []}, "l1b": {"status": "no_signal"}}, NO_CRITIC, audit=False)
    assert v.label == "unverified" and v.confidence <= 0.4
    assert not v.confident_pass and not v.confident_fail


def test_audit_calls_critic_and_its_verdict_applies():
    v = decide({"signals": []}, _critic("ok", 0.9), audit=True)
    assert v.confident_pass and v.audited
    v = decide({"signals": []}, _critic("wrong", 0.85), audit=True)
    assert v.confident_fail and v.audited


@pytest.mark.parametrize("verdict,conf,reason", [
    ("abstain", 0.9, "critic_abstain"),
    ("wrong", 0.5, "critic_low_conf"),
    ("wrong", None, "critic_conf_invalid"),
    ("ok", "very sure", "critic_conf_invalid"),
    ("ok", 7, "critic_conf_invalid"),
])
def test_uncertain_never_high_confidence(verdict, conf, reason):
    v = decide({"signals": [], "l1": {"unhandled": ["x"]}}, _critic(verdict, conf), audit=False)
    assert v.label == "uncertain" and v.reason == reason
    assert not v.confident_fail and not v.confident_pass


def test_critic_error_is_uncertain_and_budget_pauses():
    v = decide({"l1": {"unhandled": ["x"]}}, _critic(error="Timeout"), audit=False)
    assert v.label == "uncertain" and v.reason == "critic_error"
    with pytest.raises(CriticUnavailable):
        decide({"l1": {"unhandled": ["x"]}}, _critic(error="token budget reached", budget_exhausted=True), audit=False)


def test_borderline_band():
    assert decide({"l1": {"unhandled": ["x"]}}, _critic("wrong", TAU), audit=False).borderline
    assert not decide({"l1": {"unhandled": ["x"]}}, _critic("wrong", 0.8), audit=False).borderline


def test_payload_is_json_safe():
    import json
    json.dumps(decide({"l1": {"unhandled": ["x"]}}, _critic("ok", "0.9"), audit=False).payload())


# ---------------------------------------------------------------------------
# Adapter over the frozen ladder (no LLM: cheap tiers + k=3 aggregation only)
# ---------------------------------------------------------------------------

def _footer_data(d):
    d.mkdir(parents=True)
    (d / "t.csv").write_text("name,amount\na,1\nb,2\nc,3\nd,4\nTotal,10\n", encoding="utf-8")
    return d


def test_adapter_cheap_tiers_on_footer(tmp_path):
    from scripts.online_verifier import cheap_tiers, data_signals

    d = _footer_data(tmp_path / "case_data")
    assert data_signals(d) == ["footer"]
    task = SimpleNamespace(id="t")
    naive = SimpleNamespace(code_tail="import pandas as pd\nprint(pd.read_csv('t.csv')['amount'].sum())\n",
                            stdout_tail="20\n", printed=20.0, outcome="SUCCESS", error_hint="")
    c = cheap_tiers(task, d, naive, 1, tmp_path / "w1")
    v = decide(c, NO_CRITIC, audit=False)
    assert c["l1"]["unhandled"] == ["footer"] and v.tier == "L1b" and v.confident_fail
    careful = SimpleNamespace(
        code_tail="import pandas as pd\ndf=pd.read_csv('t.csv')\nprint(df[df['name']!='Total']['amount'].sum())\n",
        stdout_tail="10\n", printed=10.0, outcome="SUCCESS", error_hint="")
    c = cheap_tiers(task, d, careful, 1, tmp_path / "w2")
    assert c["l1"]["unhandled"] == [] and decide(c, NO_CRITIC, audit=False).label == "unverified"


def test_k3_consistency():
    from scripts.online_verifier import consistency

    s = lambda v, c=0.9: {"verdict": v, "confidence": c}  # noqa: E731
    assert consistency([s("wrong"), s("wrong"), s("wrong")])["confidence"] == 1.0
    two = consistency([s("wrong"), s("wrong"), s("ok")])
    assert two["verdict"] == "wrong" and two["confidence"] < TAU            # 2/3 stays uncertain
    assert consistency([s("wrong"), s("ok"), s("abstain")])["verdict"] == "abstain"
    assert consistency([s("abstain"), s("abstain"), s("ok")])["confidence"] is None
