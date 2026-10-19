"""Review queue + card CLI (online_cards.py) on a loop log built by the fakes."""

from __future__ import annotations

import pytest

from reforge.online import review
from reforge.online.card_bank import ACTIVE, EVICTED, QUARANTINED
from reforge.tests.test_online_loop import _loop, _stream


def _run_with_reobserved(tmp_path):
    loop = _loop(tmp_path)
    loop.run(_stream(["trap:footer", "trap:dupes", "infiagent:q2"]))
    return loop


def test_pending_and_resolve(tmp_path):
    loop = _run_with_reobserved(tmp_path)
    items = review.pending(loop.log)
    assert sorted(e.payload["reason"] for e in items) == ["card_reobserved", "critic_abstain"]
    abstain = next(e for e in items if e.payload["reason"] == "critic_abstain")
    review.resolve(loop.log, abstain.event_id, "dismiss", "looked fine")
    with pytest.raises(ValueError):
        review.resolve(loop.log, abstain.event_id, "dismiss")
    with pytest.raises(KeyError):
        review.resolve(loop.log, "nope", "dismiss")
    assert [e.payload["reason"] for e in review.pending(loop.log)] == ["card_reobserved"]


def test_promote_closes_card_items(tmp_path):
    loop = _run_with_reobserved(tmp_path)
    cid = next(iter(loop.bank.cards()))
    c, closed = review.promote_card(loop.bank, cid, "checked by hand")
    assert c.status == ACTIVE and len(closed) == 1
    assert loop.log.events("CARD_PROMOTED")[0].payload["reason"] == "manual: checked by hand"
    assert all(e.payload.get("card_id") != cid for e in review.pending(loop.log))


def test_cli_roundtrip(tmp_path, capsys):
    from scripts.online_cards import main

    loop = _run_with_reobserved(tmp_path)
    root = str(tmp_path / "run")
    cid = next(iter(loop.bank.cards()))
    assert main(["--root", root, "list", "--status", "quarantined"]) == 0
    assert cid in capsys.readouterr().out
    assert main(["--root", root, "used-by", cid]) == 0
    assert "trap:footer" in capsys.readouterr().out
    assert main(["--root", root, "review"]) == 0
    assert "card_reobserved" in capsys.readouterr().out
    assert main(["--root", root, "evict", cid, "--reason", "bad"]) == 0
    out = capsys.readouterr().out
    assert "evicted" in out and "resolved review items" in out and "trap:footer" in out
    assert main(["--root", root, "evict", cid, "--reason", "again"]) == 1
    from reforge.online.card_bank import CardBank
    from reforge.online.loop_log import LoopEventLog
    assert CardBank(LoopEventLog(tmp_path / "run" / "loop_events.jsonl")).get(cid).status == EVICTED
    assert main(["--root", str(tmp_path / "missing"), "list"]) == 2


def test_metrics_open_vs_enqueued(tmp_path):
    from reforge.online import metrics

    loop = _run_with_reobserved(tmp_path)
    s = metrics.summarize(loop.log, loop.bank)
    assert s["review_enqueued"] == 2 and s["review_open"] == 2
    review.resolve(loop.log, review.pending(loop.log)[0].event_id, "dismiss")
    assert metrics.summarize(loop.log, loop.bank)["review_open"] == 1
    assert QUARANTINED in {c.status for c in loop.bank.cards().values()}
