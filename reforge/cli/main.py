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
from reforge.cli.mascot import MASCOT_LINES
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
# Banner colors
# ---------------------------------------------------------------------------
_BOX_C = "\033[94m"       # bright blue box border
_TITLE = "\033[1;97m"     # bold white title
_DIM_C = "\033[38;5;243m" # muted gray labels
_ACC_C = "\033[36m"       # cyan accent

# Box inner: 1 (left margin) + 28 (mascot) + 2 (gap) + 35 (text) = 66
_BW = 66
_RIGHT_W = 35


def _banner(session_id: str) -> str:
    try:
        from reforge.config import config
        model: str = getattr(config, "llm_model", "") or ""
    except Exception:
        model = ""

    cwd = os.getcwd()
    if len(cwd) > 25:
        cwd = "…" + cwd[-24:]

    B, R = _BOX_C, _RST
    top    = f"{B}╭{'─' * _BW}╮{R}"
    bottom = f"{B}╰{'─' * _BW}╯{R}"

    right_col = [
        f"  {_TITLE}R E F O R G E{R}",
        "",
        "  AI Runtime Console",
        f"  {_DIM_C}model{R} · {model[:25]}",
        f"  {_DIM_C}dir{R}   · {cwd}",
        f"  {_DIM_C}sess{R}  · {session_id}",
        "",
        f"  {_ACC_C}task · exit{R}",
    ]

    def row(mascot: str = "", rhs: str = "") -> str:
        # left: 1 space + mascot (28 vis) + 2 gap = 31 vis
        left = (" " + mascot + "  ") if mascot else " " * 31
        # right: pad to _RIGHT_W (35) vis
        right_padded = _rpad(rhs, _RIGHT_W)
        return f"{B}│{R}{left}{right_padded}{B}│{R}"

    lines: list[str] = [top, row()]
    for i, mascot_row in enumerate(MASCOT_LINES):
        rhs = right_col[i] if i < len(right_col) else ""
        lines.append(row(mascot_row, rhs))
    for j in range(len(MASCOT_LINES), len(right_col)):
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
