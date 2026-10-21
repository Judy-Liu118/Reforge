"""AppWorld control side never touches gold: only appworld_gold.py may call /evaluate,
and the agent / card / client modules never import it (the runners may, after the agent stops)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
_CONTROL = ["appworld_agent.py", "appworld_cards.py", "appworld_client.py", "appworld_experience.py"]


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
            if any("appworld_gold" in n for n in names):
                out.append(f"{path.name}:{node.lineno} imports appworld_gold")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and "/evaluate" in node.value:
            out.append(f"{path.name}:{node.lineno} '{node.value}'")
        elif isinstance(node, ast.Attribute) and node.attr == "evaluate":
            out.append(f"{path.name}:{node.lineno} .evaluate")
    return out


def test_control_modules_never_reach_gold():
    bad = [v for f in _CONTROL for v in _violations(_SCRIPTS / f)]
    assert not bad, bad


def test_checker_catches_a_leak(tmp_path):
    leak = tmp_path / "leak.py"
    leak.write_text("import appworld_gold\nx = c._post('/evaluate', {})\ny = appworld_gold.evaluate(c, t)\n",
                    encoding="utf-8")
    assert len(_violations(leak)) == 3


def test_list_reads_groups_pages_and_ignores_token():
    sys.path.insert(0, str(_SCRIPTS))
    import re

    import appworld_cards as ac

    eps = [{"app": "spotify", "api": "search_songs", "rx": re.compile(r"^/spotify/songs$"), "default_limit": 5}]
    calls = [{"method": "get", "url": "/spotify/songs", "data": {"artist_id": 25}},
             {"method": "get", "url": "/spotify/songs", "data": {"artist_id": 25, "page_index": 1, "access_token": "t"}},
             {"method": "get", "url": "/spotify/songs", "data": {"artist_id": 9, "page_limit": 20}},
             {"method": "post", "url": "/spotify/songs", "data": {}}]
    reads = sorted(ac.list_reads(calls, eps), key=lambda r: r["params"]["artist_id"])
    assert [(r["pages"], r["limits"]) for r in reads] == [([0], [20]), ([0, 1], [5])]
    m = ac.behaviour_markers(reads)
    assert m["read_beyond_page0"] and not m["used_sort_by"]


def test_bank_refuses_unverified_card(tmp_path):
    sys.path.insert(0, str(_SCRIPTS))
    import pytest

    import appworld_cards as ac

    bank = ac.CardBank(tmp_path / "cards.json")
    with pytest.raises(ValueError):
        bank.admit({"trigger": "t", "guardrail": "g"}, evidence=[], source_task="x")
    assert bank.admit({"trigger": "t", "guardrail": "g"}, evidence=[{"k": 1}], source_task="x")["id"] == "card1"
    assert len(ac.CardBank(tmp_path / "cards.json").recall()) == 1
