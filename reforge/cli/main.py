"""CLI entry point for the Reforge runtime.

Usage:
    python -m reforge.cli.main                        # REPL mode
    python -m reforge.cli.main "task"                 # single-shot mode
    python -m reforge.cli.main --case <name>          # run a runtime case
    python -m reforge.cli.main --list                 # list available cases
    python -m reforge.cli.main --history              # show execution history
    python -m reforge.cli.main --replay <id>          # replay a saved session
    python -m reforge.cli.main --research-history          # show research session history
    python -m reforge.cli.main --export-research <id>      # export research result as Markdown
    python -m reforge.cli.main --events-list               # list all event log sessions
    python -m reforge.cli.main --events-show <session_id>  # show event timeline for a session
    python -m reforge.cli.main --events-summary            # aggregate event statistics
    python -m reforge.cli.main --serve                     # start HTTP observer (default port 8080)
    python -m reforge.cli.main --serve 9090                # start HTTP observer on port 9090
    python -m reforge.cli.main --memory-list               # list all memory records
    python -m reforge.cli.main --memory-list RECOVERY      # filter by type
    python -m reforge.cli.main --memory-show <id>          # show full detail for one record
    python -m reforge.cli.main --memory-stats              # aggregate memory statistics
"""

from __future__ import annotations

import os
import re
import sys
import uuid

from reforge.cli.commands.history import _eval_trend  # re-exported for test compat
from reforge.cli.commands.history import handle_history, handle_memory, handle_replay, handle_trace
from reforge.cli.commands.memory import handle_memory_list, handle_memory_show, handle_memory_stats
from reforge.cli.commands.run import handle_list, run_task
from reforge.cli.events import (
    handle_events_list,
    handle_events_show,
    handle_events_summary,
    handle_serve,
)
from reforge.cli.research import export_research, handle_research_history

# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------
_RST = "\033[0m"
_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _vlen(s: str) -> int:
    """Visual length of string, ignoring ANSI escape codes."""
    return len(_ANSI_RE.sub("", s))


def _rpad(s: str, w: int) -> str:
    """Right-pad string to visual width w."""
    return s + " " * max(0, w - _vlen(s))


# ---------------------------------------------------------------------------
# Pixel-art mascot — chibi runtime spirit, blue/orange split.
# Half-block technique: each char = 2 vertical pixels (▄/▀).
# Source canvas 28 cols × 26 rows; padded to 32 visual cols. None = transparent.
# ---------------------------------------------------------------------------
_PB, _PBL = 27, 33      # semantic blue (base / light)
_PO, _POD = 208, 202    # forge orange (base / deep)
_PW       = 231         # white crown / chassis
_PN       = 236         # dark navy (cog centre, panel, boot band)
_PG, _PGD = 245, 240    # gray ear-cups / chassis trim


def _mc(content: list) -> list:
    """Center *content* in a 28-col mascot canvas, then pad with 2 transparent
    cols on each side to reach the 32-col banner width. ``0`` entries inside
    *content* are also treated as transparent (gaps inside the silhouette)."""
    pad = 28 - len(content)
    left = pad // 2
    right = pad - left
    body = [None] * left + [c if c else None for c in content] + [None] * right
    return [None] * 2 + body + [None] * 2


def _me() -> list:
    return [None] * 32


_CAT_PIXELS: list[list] = [
    _me(),
    _me(),
    # crown
    _mc([_PB, _PW, _PW, _PW, _PW, _PW, _PW, _PO]),
    _mc([_PB, _PB, _PW, _PW, _PW, _PW, _PW, _PW, _PW, _PW, _PO, _PO]),
    _mc([_PW, _PB, _PB, _PB, _PB, _PB, _PB, _PO, _PO, _PO, _PO, _PO, _PO, _PW]),
    # face: blue semantic-graph half | orange cog half
    _mc([_PB, _PB, _PB, _PB, _PB, _PB, _PB, _PB,  _PO, _PO, _PW, _PW, _PW, _PW, _PO, _PO]),
    _mc([_PB, _PB, _PW, _PW, _PB, _PB, _PB, _PB,  _PO, _PW, _PW, _PN, _PN, _PW, _PW, _PO]),
    _mc([_PG] + [_PB, _PB, _PB, _PW, _PB, _PB, _PB, _PB,  _PW, _PW, _PN, _PN, _PN, _PN, _PW, _PW] + [_PG]),
    _mc([_PG] + [_PB, _PW, _PW, _PW, _PW, _PW, _PB, _PB,  _PW, _PW, _PN, _PN, _PN, _PN, _PW, _PW] + [_PG]),
    _mc([_PG] + [_PB, _PW, _PB, _PB, _PB, _PW, _PB, _PB,  _PO, _PW, _PW, _PN, _PN, _PW, _PW, _PO] + [_PG]),
    _mc([_PG] + [_PB, _PW, _PW, _PB, _PW, _PW, _PB, _PB,  _PO, _PO, _PW, _PW, _PW, _PW, _PO, _PO] + [_PG]),
    # lower head taper
    _mc([_PB, _PB, _PB, _PB, _PB, _PB, _PB, _PB,  _PO, _PO, _PO, _PO, _PO, _PO, _PO, _PO]),
    _mc([_PB, _PB, _PB, _PB, _PB, _PB, _PB,  _PO, _PO, _PO, _PO, _PO, _PO, _PO]),
    _mc([_PB, _PB, _PB, _PB, _PB, _PB,  _PO, _PO, _PO, _PO, _PO, _PO]),
    _mc([_PB, _PB, _PB, _PB, _PB,  _PO, _PO, _PO, _PO, _PO]),
    _mc([_PB, _PB, _PB, _PB,  _PO, _PO, _PO, _PO]),
    _mc([_PB, _PB, _PB,  _PO, _PO, _PO]),
    # shoulders
    _mc([0, 0, 0, 0, _PW, _PW, _PW, _PW, _PW, _PW, _PW, _PW, _PW, _PW, 0, 0, 0, 0]),
    # arms: gray stub from chassis out to a blue / orange mitt (2 rows tall)
    _mc([0, _PBL, _PGD, _PGD, _PW, _PN, _PN, _PN, _PN, _PN, _PN, _PN, _PN, _PW, _PGD, _PGD, _POD, 0]),
    _mc([0, _PBL, _PGD, _PGD, _PW, _PW, _PW, _PW, _PW, _PW, _PW, _PW, _PW, _PW, _PGD, _PGD, _POD, 0]),
    # lower chest + hips
    _mc([0, 0, 0, 0, _PGD, _PW, _PW, _PW, _PW, _PW, _PW, _PW, _PW, _PGD, 0, 0, 0, 0]),
    _mc([0, 0, 0, 0, 0, _PGD, _PW, _PW, _PW, _PW, _PW, _PW, _PGD, 0, 0, 0, 0, 0]),
    # striped boots
    _mc([0, 0, 0, 0, 0, _PW, _PW, _PW, 0, 0, _PW, _PW, _PW, 0, 0, 0, 0, 0]),
    _mc([0, 0, 0, 0, 0, _PN, _PN, _PN, 0, 0, _PN, _PN, _PN, 0, 0, 0, 0, 0]),
    _mc([0, 0, 0, 0, 0, _PW, _PW, _PW, 0, 0, _PW, _PW, _PW, 0, 0, 0, 0, 0]),
    _me(),
]


def _half_line(top: list, bot: list) -> str:
    """Pair two pixel rows → one terminal line via ▄ half-block characters."""
    out = ""
    for t, b in zip(top, bot):
        if t is None and b is None:
            out += " "
        elif t is None:
            out += f"\033[38;5;{b}m▄\033[0m"
        elif b is None:
            out += f"\033[38;5;{t}m▀\033[0m"
        else:
            out += f"\033[38;5;{b};48;5;{t}m▄\033[0m"
    return out


def _build_cat() -> list[str]:
    rows = _CAT_PIXELS
    lines = []
    for i in range(0, len(rows), 2):
        top = rows[i]
        bot = rows[i + 1] if i + 1 < len(rows) else [None] * len(top)
        lines.append(_half_line(top, bot))
    return lines


_PIXEL_CAT: list[str] = _build_cat()
_CAT_VW = 32  # visual width: 32 chars (1 per pixel, half-block packs 2 rows)

# ---------------------------------------------------------------------------
# Banner colors
# ---------------------------------------------------------------------------
_BOX_C = "\033[94m"       # bright blue box border
_TITLE = "\033[1;97m"     # bold white title
_DIM_C = "\033[38;5;243m" # muted gray labels
_ACC_C = "\033[36m"       # cyan accent

# Box inner = 1(margin) + 32(cat) + 2(gap) + 25(text) = 60
_BW = 60
_RIGHT_W = 25


def _banner(session_id: str) -> str:
    try:
        from reforge.config import config
        model: str = getattr(config, "llm_model", "") or ""
    except Exception:
        model = ""

    cwd = os.getcwd()
    if len(cwd) > 24:
        cwd = "…" + cwd[-23:]

    B, R = _BOX_C, _RST
    top    = f"{B}╭{'─' * _BW}╮{R}"
    bottom = f"{B}╰{'─' * _BW}╯{R}"

    right_col = [
        f"  {_TITLE}R E F O R G E{R}",
        "",
        f"  AI Runtime Console",
        f"  {_DIM_C}model{R} · {model[:15]}",
        f"  {_DIM_C}dir{R}   · {cwd[:17]}",
        f"  {_DIM_C}sess{R}  · {session_id}",
        "",
        f"  {_ACC_C}task · exit{R}",
    ]

    def row(cat: str = "", rhs: str = "") -> str:
        # left: 1 space + cat (32 vis) + 2 gap = 35 vis
        left = (" " + cat + "  ") if cat else " " * 35
        # right: pad to _RIGHT_W (25) vis
        right_padded = _rpad(rhs, _RIGHT_W)
        return f"{B}│{R}{left}{right_padded}{B}│{R}"

    lines: list[str] = [top, row()]
    for i, cat_row in enumerate(_PIXEL_CAT):
        rhs = right_col[i] if i < len(right_col) else ""
        lines.append(row(cat_row, rhs))
    for j in range(len(_PIXEL_CAT), len(right_col)):
        lines.append(row("", right_col[j]))
    lines += [row(), bottom]
    return "\n".join(lines)


def _read_input(prompt: str) -> str:
    """Read user input. Detects multi-line paste and reads all lines at once."""
    first = input(prompt).rstrip()
    if not first:
        return ""

    try:
        import msvcrt
    except ImportError:
        return first

    if not msvcrt.kbhit():
        return first

    lines = [first]
    while True:
        try:
            more = input(".. ").rstrip()
        except EOFError:
            break
        if more == "":
            break
        lines.append(more)
        if not msvcrt.kbhit():
            break
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if argv is None:
        argv = sys.argv

    if "--list" in argv:
        handle_list()
        return

    if "--history" in argv:
        handle_history()
        return

    if "--replay" in argv:
        idx = argv.index("--replay")
        if idx + 1 >= len(argv):
            print("Usage: python -m reforge.cli.main --replay <session_id>")
            sys.exit(1)
        handle_replay(argv[idx + 1])
        return

    if "--trace" in argv:
        idx = argv.index("--trace")
        if idx + 1 >= len(argv):
            print("Usage: python -m reforge.cli.main --trace <session_id>")
            sys.exit(1)
        handle_trace(argv[idx + 1])
        return

    if "--research-history" in argv:
        handle_research_history()
        return

    if "--export-research" in argv:
        idx = argv.index("--export-research")
        if idx + 1 >= len(argv):
            print("Usage: python -m reforge.cli.main --export-research <research_id>")
            sys.exit(1)
        export_research(argv[idx + 1])
        return

    if "--memory" in argv:
        idx = argv.index("--memory")
        if idx + 1 >= len(argv):
            print("Usage: python -m reforge.cli.main --memory <query>")
            sys.exit(1)
        handle_memory(argv[idx + 1])
        return

    if "--memory-list" in argv:
        idx = argv.index("--memory-list")
        next_arg = argv[idx + 1] if idx + 1 < len(argv) and not argv[idx + 1].startswith("--") else None
        handle_memory_list(next_arg)
        return

    if "--memory-show" in argv:
        idx = argv.index("--memory-show")
        if idx + 1 >= len(argv):
            print("Usage: python -m reforge.cli.main --memory-show <memory_id>")
            sys.exit(1)
        handle_memory_show(argv[idx + 1])
        return

    if "--memory-stats" in argv:
        handle_memory_stats()
        return

    if "--events-list" in argv:
        handle_events_list()
        return

    if "--events-show" in argv:
        idx = argv.index("--events-show")
        if idx + 1 >= len(argv):
            print("Usage: python -m reforge.cli.main --events-show <session_id>")
            sys.exit(1)
        handle_events_show(argv[idx + 1])
        return

    if "--events-summary" in argv:
        handle_events_summary()
        return

    if "--serve" in argv:
        idx = argv.index("--serve")
        next_arg = argv[idx + 1] if idx + 1 < len(argv) else ""
        port = int(next_arg) if next_arg.isdigit() else 8080
        handle_serve(port=port)
        return

    if "--case" in argv:
        from reforge.cli.case_loader import find_case
        idx = argv.index("--case")
        if idx + 1 >= len(argv):
            print("Usage: python -m reforge.cli.main --case <name>")
            sys.exit(1)
        case = find_case(argv[idx + 1])
        if case is None:
            print(f"Case not found: {argv[idx + 1]}")
            print("Use --list to see available cases.")
            sys.exit(1)
        run_task(case.request, case_meta=f"[{case.category}] {case.expected_behavior}")
        return

    if len(argv) >= 2:
        run_task(" ".join(argv[1:]))
        return

    # REPL mode — one conversation_id for the entire session
    session_id = uuid.uuid4().hex[:8]
    print(_banner(session_id))
    print()

    while True:
        try:
            user_input = _read_input("❯ ")
        except (EOFError, KeyboardInterrupt):
            print(f"\n  session {session_id} ended.")
            break

        if user_input == "":
            continue
        if user_input.lower() in ("exit", "quit"):
            print(f"  session {session_id} ended.")
            break

        run_task(user_input, conversation_id=session_id)
        print()


if __name__ == "__main__":
    main()
