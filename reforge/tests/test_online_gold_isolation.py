"""Gold must never enter the online loop's control path — static half.

Only tasks.py (which builds GoldRef) and shadow.py (which scores with it) may
touch the scorer or a gold value. Every other online module, and every script
driving the online loop, may hold and pass a `gold_ref` but never open it.
The runtime half (a poisoned GoldRef) lives in test_online_loop.py.
"""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_ALLOWED = {"tasks.py", "shadow.py"}
_FORBIDDEN_ATTRS = {"_score", "_fn", "gold", "tolerance", "gold_sql", "common_answers"}
_FORBIDDEN_NAMES = {"gold"}


def _control_files() -> list[Path]:
    pkg = [p for p in (_ROOT / "reforge" / "online").glob("*.py") if p.name not in _ALLOWED]
    scripts = sorted((_ROOT / "scripts").glob("online_*.py")) + sorted((_ROOT / "scripts").glob("run_online_loop.py"))
    return pkg + scripts


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_ATTRS:
            out.append(f"{path.name}:{node.lineno} .{node.attr}")
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            out.append(f"{path.name}:{node.lineno} {node.id}")
        elif isinstance(node, ast.arg) and node.arg in _FORBIDDEN_NAMES:
            out.append(f"{path.name}:{node.lineno} arg {node.arg}")
        elif isinstance(node, ast.Constant) and node.value in ("gold", "_score"):
            out.append(f"{path.name}:{node.lineno} '{node.value}'")   # getattr(x, "gold") etc.
    return out


def test_control_modules_never_open_gold():
    files = _control_files()
    assert files, "no control modules found"
    bad = [v for f in files for v in _violations(f)]
    assert not bad, f"gold reached the control path: {bad}"


def test_checker_catches_a_leak(tmp_path):
    leak = tmp_path / "leak.py"
    leak.write_text("def f(ref, case):\n    return ref._score('') or case.gold or getattr(case, 'gold')\n",
                    encoding="utf-8")
    assert len(_violations(leak)) == 3
