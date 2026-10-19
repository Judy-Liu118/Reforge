"""OnlineLoop end to end with fake agent / verifier / distiller (no LLM).

Includes the runtime half of the gold-isolation guarantee: a GoldRef that
explodes on use must leave every control decision unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from reforge.online.card_bank import ACTIVE, QUARANTINED
from reforge.online.decision import CriticUnavailable
from reforge.online.loop import LoopConfig, OnlineLoop, Trajectory
from reforge.online.loop_log import LoopEventLog
from reforge.online.tasks import GoldRef, TaskSpec, numeric_gold

GOOD_CARD = {"title": "聚合前排除汇总行", "description": "对数值列求和的任务。",
             "content": "- 求和前检查文本列末尾是否有合计行,有则剔除。\n- 对比剔除前后行数,确认只去掉了汇总行。",
             "polarity": "failure", "violations": []}

VAGUE_CARD = {**GOOD_CARD, "content": "要仔细。\n要认真。"}      # gate soft-fails: not actionable

# scenario per task: printed without / with a card, and what the fake verifier says
SCEN = {
    "trap:footer": {"cold": 20.0, "warm": 10.0, "verdict": "l1b_fail"},     # learns, retry fixes
    "trap:dupes": {"cold": 7.0, "warm": 7.0, "verdict": "l1b_fail"},
    "infiagent:q1": {"cold": 1.5, "warm": 1.5, "verdict": "cheap"},
    "infiagent:q2": {"cold": 3.0, "warm": 3.0, "verdict": "residual_abstain"},
    "csv:c1": {"cold": 5.0, "warm": 5.0, "verdict": "residual_ok"},
    "trap:crit1": {"cold": 9.0, "warm": 4.0, "verdict": "residual_wrong"},  # critic confirms → active card
    "trap:crit2": {"cold": 8.0, "warm": 3.0, "verdict": "residual_wrong"},
}


def _spec(tid):
    src = tid.split(":")[0]

    def build(d: Path) -> Path:
        d.mkdir(parents=True, exist_ok=True)
        return d
    return TaskSpec(id=tid, source=src, request=f"读取 data.csv,计算 {tid} 的总和", tables=(f"{tid}.csv",), build=build)


class FakeAgent:
    def __init__(self, explode_on=None):
        self.calls, self.explode_on = [], explode_on

    def run(self, task, data_dir, run_dir, cards):
        self.calls.append((task.id, [c["memory_id"] for c in cards]))
        if self.explode_on and self.explode_on(task, cards, len(self.calls)):
            raise KeyboardInterrupt("killed")
        s = SCEN[task.id]
        v = s["warm"] if cards else s["cold"]
        return Trajectory(stdout_tail=f"{v}\n", code_tail=f"code for {task.id}", printed=v, outcome="SUCCESS",
                          card_injected=bool(cards), tokens_prompt=100, tokens_completion=10)


class FakeVerifier:
    sha = "fake"

    def __init__(self, budget_out=False):
        self.critic_calls, self.budget_out = 0, budget_out

    def data_signals(self, data_dir):
        return ["footer"]

    def cheap(self, task, data_dir, traj, attempt, work):
        kind = SCEN[task.id]["verdict"]
        if kind == "l1b_fail" and traj.printed == SCEN[task.id]["cold"]:
            return {"signals": ["footer"], "l1": {"unhandled": ["footer"]},
                    "l1b": {"fail": True, "status": "ran", "orig": traj.printed, "repaired": 1.0}}
        if kind.startswith("residual"):
            return {"signals": ["footer"], "l1": {"unhandled": ["footer"]}, "l1b": {"fail": False, "status": "ran"}}
        return {"signals": [], "l1": {"unhandled": []}, "l1b": {"status": "no_signal"}}

    def critic(self, task, data_dir, traj, attempt):
        self.critic_calls += 1
        if self.budget_out:
            return {"verdict": "abstain", "error": "token budget reached", "budget_exhausted": True}
        kind = SCEN[task.id]["verdict"]
        v = {"residual_abstain": "abstain", "residual_ok": "ok"}.get(kind, "ok")
        if kind == "residual_wrong" and traj.printed == SCEN[task.id]["cold"]:
            v = "wrong"
        return {"verdict": v, "confidence": 0.9, "suspect": "", "evidence": "", "total_tokens": 50,
                "printed": traj.printed}


class FakeDistiller:
    model = "fake-distiller"

    def __init__(self, body=None):
        self.body, self.calls = body if body is not None else GOOD_CARD, 0

    def distill(self, traj, feedback, forbidden_terms):
        self.calls += 1
        assert "修复" in feedback or "疑点" in feedback     # verifier diagnosis, never gold
        return dict(self.body), 30


def _loop(tmp_path, agent=None, verifier=None, distiller=None, **cfg):
    return OnlineLoop(tmp_path / "run", agent=agent or FakeAgent(), verifier=verifier or FakeVerifier(),
                      distiller=distiller or FakeDistiller(), config=LoopConfig(**cfg))


def _gold(v):
    return numeric_gold(lambda: v, 0.01)


GOLDS = {"trap:footer": 10.0, "trap:dupes": 6.0, "infiagent:q1": 1.5, "infiagent:q2": 3.0, "csv:c1": 5.0,
         "trap:crit1": 4.0, "trap:crit2": 3.0}


def _stream(ids, poison=False):
    def boom(_):
        raise AssertionError("gold opened")
    return [(_spec(t), GoldRef(boom) if poison else _gold(GOLDS[t])) for t in ids]


def test_cheap_tier_failure_mints_quarantined_card_scoped_to_its_task(tmp_path):
    loop = _loop(tmp_path)
    s = loop.run(_stream(["trap:footer"]))
    v = [e.payload for e in loop.log.events("VERDICT")]
    # trap stream audits at 1.0, so attempt 2's cheap pass goes to the critic
    assert [(x["attempt"], x["label"], x["tier"], x["audited"]) for x in v] == [
        (1, "fail", "L1b", False), (2, "pass", "critic", True)]
    assert loop.bank.by_status(ACTIVE) == []
    card, = loop.bank.by_status(QUARANTINED)
    assert card.source_task_id == "trap:footer" and card.verifier_tier == "L1b" and card.signals == ("footer",)
    assert loop.log.events("CARD_ADMITTED")[0].payload["reasons"] == ["cheap_tier_only:L1b"]
    assert card.provenance and v[1]["card_ids"] == [card.card_id]
    assert loop.agent.calls == [("trap:footer", []), ("trap:footer", [card.card_id])]   # own retry only
    assert s["retries"] == 1 and s["cards_admitted_quarantined"] == 1 and s["critic_calls"] == 1
    rows = loop.shadow.rows()
    assert [(r["attempt"], r["answer_correct"], r["label_correct"]) for r in rows] == [(1, False, True), (2, True, True)]
    assert loop.bank.used_by(card.card_id)[0]["attempt"] == 2


def test_critic_confirmed_failure_mints_active_card(tmp_path):
    loop = _loop(tmp_path)
    loop.run(_stream(["trap:crit1"]))
    card, = loop.bank.by_status(ACTIVE)
    assert card.verifier_tier == "critic" and card.critic_confidence == 0.9
    assert loop.log.events("CARD_ADMITTED")[0].payload["reasons"] == []


def test_uncertain_goes_to_review_without_card_or_retry(tmp_path):
    loop = _loop(tmp_path)
    loop.run(_stream(["infiagent:q2"]))
    assert [e.payload["reason"] for e in loop.log.events("REVIEW_ENQUEUED")] == ["critic_abstain"]
    assert loop.distiller.calls == 0 and len(loop.agent.calls) == 1 and loop.bank.cards() == {}


def test_unverified_closes_without_card(tmp_path):
    loop = _loop(tmp_path, audit_rates={})
    s = loop.run(_stream(["infiagent:q1"]))
    assert s["final_label"] == {"unverified": 1} and s["critic_calls"] == 0 and loop.distiller.calls == 0


def test_confident_pass_no_retry(tmp_path):
    loop = _loop(tmp_path)
    s = loop.run(_stream(["csv:c1"]))
    assert s["final_label"] == {"pass": 1} and s["retries"] == 0


JOIN_CARD = {"title": "关联前统一键格式", "description": "两表按键关联后再聚合的任务。",
             "content": "- 关联前去掉键两端空格并统一大小写。\n- 关联后对比行数,确认没有丢行或翻倍。",
             "polarity": "failure", "violations": []}


class PerTaskDistiller(FakeDistiller):
    def distill(self, traj, feedback, forbidden_terms):
        body, tok = super().distill(traj, feedback, forbidden_terms)
        return (dict(JOIN_CARD) if "crit" in traj.code_tail else body), tok


def test_active_card_recalled_by_next_task_quarantined_one_not(tmp_path):
    loop = _loop(tmp_path, distiller=PerTaskDistiller())
    loop.run(_stream(["trap:footer", "trap:crit1", "trap:crit2"]))
    q, = loop.bank.by_status(QUARANTINED)
    act, = loop.bank.by_status(ACTIVE)
    assert q.source_task_id == "trap:footer" and act.source_task_id == "trap:crit1"
    by_task = [c for t, c in loop.agent.calls if t == "trap:crit1"]
    assert by_task[0] == []                                  # footer's quarantined card never reached crit1
    assert [c for t, c in loop.agent.calls if t == "trap:crit2"][0] == [act.card_id]


def test_reobservation_counts_but_never_promotes(tmp_path):
    loop = _loop(tmp_path)
    loop.run(_stream(["trap:footer", "trap:dupes"]))
    q = next(iter(loop.bank.cards()))
    c = loop.bank.get(q)
    assert c.status == QUARANTINED and c.observers == ("trap:footer", "trap:dupes")
    assert loop.log.events("CARD_PROMOTED") == []
    rev = [e.payload for e in loop.log.events("REVIEW_ENQUEUED")]
    assert [(r["reason"], r["card_id"]) for r in rev] == [("card_reobserved", q)]
    # dupes may not borrow footer's quarantined card for its retry → no retry, reason logged
    assert [c for t, c in loop.agent.calls if t == "trap:dupes"] == [[]]
    commit = [e for e in loop.log.events("TASK_COMMITTED") if e.task_id == "trap:dupes"][0].payload
    assert commit["retry_skipped"] == f"quarantined_card_of_other_task:{q}"
    assert loop.bank.promote(q).status == ACTIVE                       # manual is the only way up


def test_poisoned_gold_changes_no_decision(tmp_path):
    ids = list(SCEN)

    def decisions(root, poison):
        loop = OnlineLoop(root, agent=FakeAgent(), verifier=FakeVerifier(), distiller=FakeDistiller())
        loop.run(_stream(ids, poison=poison))
        keep = ("label", "tier", "reason", "attempt", "confidence")
        return ([(e.kind, e.task_id, {k: e.payload.get(k) for k in keep}) for e in loop.log.events()
                 if e.kind in ("VERDICT", "REVIEW_ENQUEUED", "TASK_COMMITTED", "CARD_ADMITTED", "CARD_REJECTED")],
                loop.shadow.rows())

    clean, _ = decisions(tmp_path / "clean", False)
    poisoned, rows = decisions(tmp_path / "poison", True)
    assert clean == poisoned
    assert rows and all("gold opened" in r["score_error"] for r in rows)    # only shadow ever opened it


def test_kill_mid_task_then_resume(tmp_path):
    # kill during the retry of the first task: its card is admitted but uncommitted
    agent = FakeAgent(explode_on=lambda task, cards, n: n == 2)
    loop = _loop(tmp_path, agent=agent)
    with pytest.raises(KeyboardInterrupt):
        loop.run(_stream(["trap:footer", "csv:c1"]))
    log = LoopEventLog(tmp_path / "run" / "loop_events.jsonl")
    assert log.committed_tasks() == {} and len(log.abandoned_tries()) == 1
    assert len(log.events("CARD_ADMITTED", committed_only=False)) == 1 and log.events("CARD_ADMITTED") == []

    loop2 = _loop(tmp_path)
    loop2.run(_stream(["trap:footer", "csv:c1"]))
    assert set(loop2.log.committed_tasks()) == {"trap:footer", "csv:c1"}
    assert len(loop2.bank.cards()) == 1                     # the orphan card never surfaced
    assert loop2.agent.calls[0] == ("trap:footer", [])      # rerun from scratch, nothing recalled
    assert {(r["task_id"], r["attempt"]) for r in loop2.shadow.rows()} == {("trap:footer", 1), ("trap:footer", 2),
                                                                            ("csv:c1", 1)}
    # third start: everything committed → nothing reruns
    loop3 = _loop(tmp_path)
    loop3.run(_stream(["trap:footer", "csv:c1"]))
    assert loop3.agent.calls == []


def test_shadow_backfilled_after_kill_between_commit_and_shadow(tmp_path):
    loop = _loop(tmp_path)
    loop._shadow = lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        loop.run(_stream(["csv:c1"]))
    assert loop.shadow.rows() == [] and "csv:c1" in loop.log.committed_tasks()
    loop2 = _loop(tmp_path)
    loop2.run(_stream(["csv:c1"]))
    assert len(loop2.shadow.rows()) == 1 and loop2.agent.calls == []


def test_harness_error_leaves_task_for_resume(tmp_path):
    class Flaky(FakeAgent):
        def run(self, task, *a):
            t = super().run(task, *a)
            if task.id == "csv:c1":
                t.error = "APITimeoutError"
            return t
    loop = _loop(tmp_path, agent=Flaky())
    loop.run(_stream(["csv:c1", "infiagent:q1"]))
    assert set(loop.log.committed_tasks()) == {"infiagent:q1"}
    loop2 = _loop(tmp_path)
    loop2.run(_stream(["csv:c1", "infiagent:q1"]))
    assert set(loop2.log.committed_tasks()) == {"infiagent:q1", "csv:c1"}


def test_evicted_card_is_not_relearned(tmp_path):
    loop = _loop(tmp_path)
    loop.run(_stream(["trap:footer"]))
    cid = next(iter(loop.bank.cards()))
    loop.bank.evict(cid, "bad card")
    loop.run(_stream(["trap:dupes"]))
    rej = [e.payload["reasons"] for e in loop.log.events("CARD_REJECTED")]
    assert rej == [[f"duplicate_of_evicted:{cid}"]]
    assert [c for t, c in loop.agent.calls if t == "trap:dupes"] == [[]]      # nothing recalled, no retry


def test_hard_rejected_card_means_no_retry(tmp_path):
    loop = _loop(tmp_path, distiller=FakeDistiller(body={**GOOD_CARD, "content": ""}))
    s = loop.run(_stream(["trap:footer"]))
    assert s["cards_rejected"] == 1 and s["retries"] == 0


def test_gate_soft_fail_quarantines_even_critic_confirmed(tmp_path):
    loop = _loop(tmp_path, distiller=FakeDistiller(body=VAGUE_CARD))
    s = loop.run(_stream(["trap:crit1", "csv:c1"]))
    assert s["cards_admitted_quarantined"] == 1 and s["bank_active"] == 0
    q = next(iter(loop.bank.cards()))
    assert loop.agent.calls == [("trap:crit1", []), ("trap:crit1", [q]),      # retry of the source task only
                                ("csv:c1", [])]                               # never injected elsewhere


def test_critic_budget_exhaustion_stops_without_commit(tmp_path):
    loop = _loop(tmp_path, verifier=FakeVerifier(budget_out=True))
    with pytest.raises(CriticUnavailable):
        loop.run(_stream(["csv:c1"]))
    assert loop.log.committed_tasks() == {} and loop.log.events("REVIEW_ENQUEUED", committed_only=False) == []


def test_max_tasks_and_metrics_file(tmp_path):
    loop = _loop(tmp_path)
    loop.run(_stream(list(SCEN)), max_tasks=2)
    assert len(loop.log.committed_tasks()) == 2
    lines = (tmp_path / "run" / "metrics.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and (tmp_path / "run" / "summary.md").exists()


def test_verifier_sees_pristine_inputs(tmp_path):
    seen = []

    class Messy(FakeAgent):
        def run(self, task, data_dir, run_dir, cards):
            (data_dir / "agent_output.csv").write_text("x\n1\n", encoding="utf-8")
            return super().run(task, data_dir, run_dir, cards)

    class Watching(FakeVerifier):
        def cheap(self, task, data_dir, traj, attempt, work):
            seen.append(sorted(p.name for p in data_dir.iterdir()))
            return super().cheap(task, data_dir, traj, attempt, work)

    loop = _loop(tmp_path, agent=Messy(), verifier=Watching(), audit_rates={})
    loop.run(_stream(["infiagent:q1"]))
    assert seen == [[]]
