"""Loop event log (commit semantics) + CardBank projection and lifecycle."""

from __future__ import annotations

import pytest

from reforge.online.card_bank import ACTIVE, EVICTED, QUARANTINED, Card, CardBank, provenance_hash
from reforge.online.loop_log import LoopEventLog


def _card(cid, task="t1", request="读取 insurance.csv,计算 charges 列的总和", status=ACTIVE, signals=("footer",)):
    return Card(card_id=cid, body={"title": "x", "description": "d", "content": "- 检查汇总行", "polarity": "failure"},
                source_task_id=task, source_run_id=f"{task}/a1", source_request=request, created_at="now",
                verifier_tier="L1b", critic_confidence=None, signals=signals, provenance=provenance_hash(cid),
                status=status)


def _commit(log, task, tr):
    log.append("TASK_COMMITTED", task_id=task, try_id=tr)


def test_unknown_kind_rejected(tmp_path):
    with pytest.raises(ValueError):
        LoopEventLog(tmp_path / "e.jsonl").append("NOPE")


def test_uncommitted_try_is_invisible_and_survives_reload(tmp_path):
    log = LoopEventLog(tmp_path / "e.jsonl")
    bank = CardBank(log)
    log.append("TASK_STARTED", task_id="t1", try_id="A")
    bank.admit(_card("c1"), task_id="t1", try_id="A")
    assert bank.get("c1", pending_try="A") is not None      # the running try sees its own card
    assert bank.get("c1") is None                           # nobody else does
    # crash: reload, card still invisible, try listed as abandoned
    log2 = LoopEventLog(tmp_path / "e.jsonl")
    assert CardBank(log2).cards() == {} and log2.abandoned_tries() == ["A"]
    _commit(log2, "t1", "A")
    assert CardBank(log2).get("c1").status == ACTIVE
    assert log2.committed_tasks() == {"t1": "A"}


def test_torn_line_skipped(tmp_path):
    p = tmp_path / "e.jsonl"
    log = LoopEventLog(p)
    log.append("LOOP_STARTED")
    with open(p, "a", encoding="utf-8") as fh:
        fh.write('{"kind": "TASK_STA')                      # killed mid-write
    assert len(LoopEventLog(p)) == 1


def test_recall_filters_status_and_threshold(tmp_path):
    log = LoopEventLog(tmp_path / "e.jsonl")
    bank = CardBank(log)
    bank.admit(_card("on"), task_id="t1", try_id="A")
    bank.admit(_card("q", status=QUARANTINED), task_id="t1", try_id="A")
    _commit(log, "t1", "A")
    hits = bank.recall("读取 insurance.csv,计算 bmi 列的平均值")
    assert [c.card_id for c, _ in hits] == ["on"]           # quarantined never recalled
    assert bank.recall("完全无关的问题 zzz", min_score=0.3) == []
    # signal overlap lifts a weak lexical match over the threshold
    weak = "Read the file x.csv and answer"
    assert bank.recall(weak, min_score=0.3) == []
    assert [c.card_id for c, _ in bank.recall(weak, ["footer"], min_score=0.3)] == ["on"]


def test_observation_accumulates_never_promotes(tmp_path):
    log = LoopEventLog(tmp_path / "e.jsonl")
    bank = CardBank(log)
    bank.admit(_card("q", status=QUARANTINED), task_id="t1", try_id="A")
    assert bank.observe("q", task_id="t1", try_id="A").observers == ("t1",)   # same task: no new observer
    _commit(log, "t1", "A")
    for i, tr in ((2, "B"), (3, "C")):
        bank.observe("q", task_id=f"t{i}", try_id=tr)
        _commit(log, f"t{i}", tr)
    c = bank.get("q")
    assert c.status == QUARANTINED and c.observers == ("t1", "t2", "t3")
    assert log.events("CARD_PROMOTED") == []


def test_manual_promote_and_evict(tmp_path):
    log = LoopEventLog(tmp_path / "e.jsonl")
    bank = CardBank(log)
    bank.admit(_card("q", status=QUARANTINED), task_id="t1", try_id="A")
    _commit(log, "t1", "A")
    assert bank.promote("q").status == ACTIVE
    with pytest.raises(ValueError):
        bank.promote("q")
    v_before = bank.version()
    c = bank.evict("q", "canary regression")
    assert c.status == EVICTED and c.history[-1]["reason"] == "canary regression"
    assert log.events("CARD_EVICTED")[0].payload["used_by"] == []
    assert bank.recall("读取 insurance.csv,计算 charges 列的总和") == []
    assert bank.version() != v_before
    with pytest.raises(ValueError):
        bank.evict("q", "again")
    with pytest.raises(KeyError):
        bank.evict("missing", "x")


def test_used_by_traces_committed_runs_only(tmp_path):
    log = LoopEventLog(tmp_path / "e.jsonl")
    bank = CardBank(log)
    bank.admit(_card("c1"), task_id="t1", try_id="A")
    _commit(log, "t1", "A")
    c = bank.get("c1")
    bank.record_recall(task_id="t2", try_id="B", run_id="t2/a1", attempt=1, hits=[(c, 0.5)], injected=True)
    _commit(log, "t2", "B")
    bank.record_recall(task_id="t3", try_id="C", run_id="t3/a1", attempt=1, hits=[(c, 0.4)], injected=True)
    # t3 crashed before commit
    assert bank.used_by("c1") == [{"task_id": "t2", "try_id": "B", "attempt": 1, "run_id": "t2/a1",
                                   "injected": True, "score": 0.5}]


def test_memory_record_shape():
    from reforge.memory.models import MemoryRecord

    r = MemoryRecord.model_validate(_card("c1").to_memory_record())
    assert r.memory_id == "c1" and r.distilled_strategy["content"] == "- 检查汇总行"


def test_recall_overlap_rule_needs_signal_and_similarity(tmp_path):
    log = LoopEventLog(tmp_path / "e.jsonl")
    bank = CardBank(log)
    bank.admit(_card("sig"), task_id="t1", try_id="A")
    bank.admit(_card("nosig", task="t2", signals=()), task_id="t2", try_id="B")
    log.append("TASK_COMMITTED", task_id="t1", try_id="A")
    log.append("TASK_COMMITTED", task_id="t2", try_id="B")
    req = "读取 insurance.csv,计算 charges 列的总和"
    kw = {"min_score": 0.3, "require_signal_overlap": True}
    # no detected signal on the task → nothing, however similar the request
    assert bank.recall(req, [], **kw) == []
    hits = bank.recall(req, ["footer"], limit=5, **kw)
    assert [c.card_id for c, _ in hits] == ["sig"] and hits[0][1] <= 1.0      # score = cosine, no bonus
    # signal overlap alone is not enough
    assert bank.recall("完全无关的问题 zzz", ["footer"], **kw) == []
    # the legacy rule would let the bonus carry a dissimilar request over the line
    assert bank.recall("完全无关的问题 zzz", ["footer"], min_score=0.3)
