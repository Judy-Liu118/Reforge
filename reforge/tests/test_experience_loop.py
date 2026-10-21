"""reforge.experience: flag, verifier-gated admit, canary-gated promote, eviction provenance, scope recall, probes."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from reforge.experience import (ACTIVE, EVICTED, QUARANTINED, TASK, CanaryReport, ExperienceConfig, ExperienceLoop,
                                TaskContext, VerifiedFailure)
from reforge.experience import probes
from reforge.experience.gate import check

APPS = ("spotify", "phone", "venmo", "simple_note")
PAGE_EV = ({"kind": "list API", "last_page_read": 0, "page_limit": 5, "items_on_that_page": 5,
            "next_page_items": 5, "agent_finished": True},)
GOOD = {"trigger": "When a paginated call returns exactly page_limit (default 5) items.",
        "guardrail": "Keep requesting page_index+1 until a call returns fewer than page_limit items, then answer."}


def _loop(tmp_path, enabled=True, **kw) -> ExperienceLoop:
    return ExperienceLoop(ExperienceConfig(log_path=tmp_path / "loop.jsonl", env="appworld", known_apps=APPS,
                                           enabled=enabled, banned_terms=frozenset({"search_songs", "spotify"}),
                                           required_anchors={"pagination": frozenset({"5", "page_index"})}, **kw))


def _failure(kind="pagination", ev=PAGE_EV):
    return VerifiedFailure(kind, "next_page_nonempty", ev, source_task="t-src", source_run="r0")


def _active(loop, body=GOOD, apps=None, level="convention", kind="pagination"):
    card = loop.mint(_failure(kind), lambda f: body, apps=apps, level=level).card
    return loop.promote(card.card_id, CanaryReport(card.card_id, n=4, control_success=1, treated_success=3))


def test_flag_is_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("REFORGE_EXPERIENCE_CARDS", raising=False)
    loop = ExperienceLoop(ExperienceConfig(log_path=tmp_path / "l.jsonl", env="appworld", known_apps=APPS))
    assert loop.config.enabled is False
    _active(loop)
    assert loop.recall(TaskContext("t1", "appworld", "Name the artist on Spotify.")) == []
    assert loop.store.log.events("CARD_RECALLED") == []          # off = no behaviour change, no recall events


def test_flag_env_turns_it_on(tmp_path, monkeypatch):
    monkeypatch.setenv("REFORGE_EXPERIENCE_CARDS", "1")
    assert ExperienceConfig(log_path=tmp_path / "l.jsonl", env="appworld").enabled is True


def test_verified_failure_requires_evidence():
    with pytest.raises(ValueError):
        VerifiedFailure("pagination", "v", (), source_task="t")


def test_mint_admits_to_quarantine_and_quarantine_is_not_recalled(tmp_path):
    loop = _loop(tmp_path)
    res = loop.mint(_failure(), lambda f: GOOD)
    assert res.card.status == QUARANTINED and res.attempts == 1
    assert loop.recall(TaskContext("t1", "appworld", "Name the artist on Spotify.")) == []


def test_gate_rejects_banned_and_missing_anchor_then_gives_up(tmp_path):
    loop = _loop(tmp_path)
    bodies = iter([{"trigger": "When search_songs returns 5 items", "guardrail": "use page_index"},
                   {"trigger": "When results are paged", "guardrail": "read all pages"}])
    res = loop.mint(_failure(), lambda f: next(bodies))
    assert res.card is None and "missing:5" in res.reasons
    rejected = loop.store.log.events("CARD_REJECTED")
    assert len(rejected) == 2 and "banned:search_songs" in rejected[0].payload["reasons"]


def test_gate_catches_concrete_values():
    assert "concrete_value" in check("login", "use username=joe@x.com", required={"username"}).reasons
    assert check("t", "g", has_evidence=False).reasons[0] == "no_verifier_evidence"


def test_promote_needs_a_passing_canary(tmp_path):
    loop = _loop(tmp_path)
    card = loop.mint(_failure(), lambda f: GOOD).card
    with pytest.raises(ValueError):
        loop.promote(card.card_id, CanaryReport(card.card_id, n=4, control_success=2, treated_success=3,
                                                regressions=("c3",)))
    assert loop.store.get(card.card_id).status == QUARANTINED
    assert loop.store.log.events("CANARY_RESULT")[0].payload["passed"] is False
    assert loop.promote(card.card_id, CanaryReport(card.card_id, 4, 2, 3)).status == ACTIVE


def test_scope_recall_env_wide_and_app_scoped(tmp_path):
    loop = _loop(tmp_path)
    env_wide = _active(loop)
    venmo = _active(loop, {"trigger": "When paying a contact.", "guardrail": "Look up the payee first."},
                    apps=("venmo",), kind="payee")
    got = loop.recall(TaskContext("t1", "appworld", "Name the artist most recommended to me on Spotify.", "run1"))
    assert [c.card_id for c in got] == [env_wide.card_id]           # venmo card not injected on a spotify task
    got = loop.recall(TaskContext("t2", "appworld", "Pay my roommate on Venmo."))
    assert {c.card_id for c in got} == {env_wide.card_id, venmo.card_id}
    assert loop.recall(TaskContext("t3", "other_env", "anything")) == []


def test_task_level_cards_are_not_recalled_by_default(tmp_path):
    loop = _loop(tmp_path)
    _active(loop, level=TASK)
    assert loop.recall(TaskContext("t1", "appworld", "x on Spotify")) == []
    assert loop.recall_on_step(TaskContext("t1", "appworld", "x"), {"code": "", "output": ""}, []) == []


def test_plugged_task_retriever_is_used(tmp_path):
    def retriever(task, cards, *, stage, step=None):
        return [c for c in cards if c.scope.level == TASK and stage == "step" and "login(" in (step or {}).get("code", "")]
    loop = _loop(tmp_path, task_retriever=retriever)
    c = _active(loop, level=TASK)
    t = TaskContext("t1", "appworld", "x")
    assert loop.recall(t) == []
    assert loop.recall_on_step(t, {"code": "apis.phone.login(email=1)"}, []) == [c]


def test_evict_snapshots_used_by_and_replays_from_log(tmp_path):
    loop = _loop(tmp_path)
    card = _active(loop)
    loop.recall(TaskContext("t1", "appworld", "on Spotify", "run-a"))
    loop.recall(TaskContext("t2", "appworld", "on phone", "run-b"))
    ev = loop.evict(card.card_id, "regressed in production canary")
    assert ev.status == EVICTED
    snap = loop.store.log.events("CARD_EVICTED")[0].payload["used_by"]
    assert [u["run_id"] for u in snap] == ["run-a", "run-b"]
    reopened = _loop(tmp_path)                                       # projection is rebuilt from the log alone
    assert reopened.store.get(card.card_id).status == EVICTED
    assert reopened.recall(TaskContext("t3", "appworld", "on Spotify")) == []


def test_render_is_guidance_block():
    assert ExperienceLoop.render([]) == ""


def test_pagination_probe_and_verifier():
    eps = [probes.Endpoint("spotify", "search_songs", re.compile(r"^/spotify/songs$"))]
    calls = [{"method": "get", "url": "/spotify/songs", "data": {"artist_id": 9}},
             {"method": "get", "url": "/spotify/songs", "data": {"artist_id": 3, "page_index": 1, "access_token": "x"}}]
    reads = probes.paginated_reads(calls, eps)
    sizes = {(9, 0): 5, (9, 1): 5, (3, 1): 2, (3, 2): 0}
    f = probes.confirm_incomplete_reads(reads, lambda r, p, lim: sizes[(r.params["artist_id"], p)],
                                        agent_finished=True, source_task="t")
    assert f is not None and len(f.evidence) == 1 and f.evidence[0]["next_page_items"] == 5
    assert "artist_id" not in str(f.evidence)                         # evidence is redacted
    assert probes.confirm_incomplete_reads(reads, lambda r, p, lim: 5, agent_finished=False, source_task="t") is None


def test_login_probe_and_verifier():
    steps = [{"step": 3, "code": "apis.phone.login(email=e, password=p)", "output": "Execution failed. ..."},
             {"step": 4, "code": "apis.phone.login(username=n, password=p)", "output": "{...}"}]
    assert probes.login_failures(steps) == [{"step": 3, "app": "phone", "param": "email"}]
    f = probes.confirm_login_identity("phone", {"email=<email>": "error 422", "username=<phone>": "ok"},
                                      source_task="t")
    assert f.kind == "login_identity"
    assert probes.confirm_login_identity("phone", {"a": "ok"}, source_task="t") is None


def test_experience_package_never_touches_gold():
    pkg = Path(__file__).resolve().parents[1] / "experience"
    bad = []
    for f in pkg.glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Name) and node.id == "gold":
                bad.append(f"{f.name}:{node.lineno}")
            elif isinstance(node, ast.Attribute) and node.attr in {"gold", "evaluate", "_score"}:
                bad.append(f"{f.name}:{node.lineno}")
            elif isinstance(node, (ast.Import, ast.ImportFrom)) and any(
                    "gold" in (a.name or "") or "shadow" in (a.name or "") for a in node.names):
                bad.append(f"{f.name}:{node.lineno} import")
            elif isinstance(node, ast.ImportFrom) and node.module and ("shadow" in node.module or "gold" in node.module):
                bad.append(f"{f.name}:{node.lineno} import")
    assert not bad, bad


def test_runtime_probes_swallow_and_missing_return():
    from reforge.experience import runtime_probes as rp

    code = "try:\n    x = {}['k']\nexcept Exception:\n    pass\nprint(1)"
    assert rp.swallowed_exception_probe(code, 0) == ["blanket_swallow"]
    assert rp.swallowed_exception_probe(code, 1) == []                     # crashed runs are L0's business
    assert "raise" in rp.unswallow(code) and "pass" not in rp.unswallow(code)
    f = rp.confirm_swallowed_exception(code, 0, lambda c: (1, "Traceback (most recent call last):\nKeyError: 'k'"),
                                       source_task="t")
    assert f.evidence[0]["exception_type"] == "KeyError"
    assert rp.confirm_swallowed_exception(code, 0, lambda c: (0, ""), source_task="t") is None
    ok_fn = "def f():\n    return None if False else 3\nprint(f())"
    assert rp.confirm_missing_return(ok_fn, "None\n", source_task="t") is None
    assert rp.confirm_missing_return("def f():\n    y = 1\nprint(f())", "None\n", source_task="t") is not None
    assert [s.kind for s in rp.probe_only_suspicions("q", "", 0)] == ["empty_output"]


def test_critic_profile_paths_and_cross_model(tmp_path):
    from reforge.experience import critic as c
    f = tmp_path / "a.csv"
    f.write_text('amount\n"1,200"\n35\nN/A\n', encoding="utf-8")
    assert c.data_paths(f"sum {f} please") == [f]
    prof = c.profile_text([f])
    assert "numeric_parseable=1/3" in prof and "non_numeric_examples" in prof
    with pytest.raises(ValueError):
        c.Critic("m", "u", "k", generator_model="m")
    assert len(c.PROMPT_SHA) == 12


def test_coerce_probe():
    from reforge.experience import runtime_probes as rp
    assert rp.coerces_to_missing("import pandas as pd\npd.to_numeric(s, errors='coerce')")
    assert not rp.coerces_to_missing("pd.to_numeric(s)")
    kinds = [s.kind for s in rp.probe_only_suspicions("q", "25", 0, "pd.to_numeric(x, errors='coerce')")]
    assert kinds == ["coerce_drop"]
