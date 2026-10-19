"""Review / rollback CLI for an online-loop card bank.

Every action is an event appended to runs/online/<name>/loop_events.jsonl,
so it is replayed by the next loop run and visible in the report.

    python scripts/online_cards.py --name smoke list [--status active|quarantined|evicted]
    python scripts/online_cards.py --name smoke show <card_id>
    python scripts/online_cards.py --name smoke used-by <card_id>
    python scripts/online_cards.py --name smoke promote <card_id> --reason "checked by hand"
    python scripts/online_cards.py --name smoke evict <card_id> --reason "canary regression"
    python scripts/online_cards.py --name smoke review [--all]
    python scripts/online_cards.py --name smoke resolve <review_id> --decision dismiss --note "..."

Run it while the loop is stopped: the loop process does not reread the log.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from reforge.online import review  # noqa: E402
from reforge.online.card_bank import CardBank  # noqa: E402
from reforge.online.loop_log import LoopEventLog  # noqa: E402


def _card_line(c) -> str:
    return (f"{c.card_id}  {c.status:11s} minted_by={c.verifier_tier:9s} src={c.source_task_id:32s} "
            f"obs={len(c.observers)}  {c.body.get('title', '')}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", help="run under runs/online/")
    ap.add_argument("--root", help="explicit run directory (overrides --name)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list")
    p.add_argument("--status", choices=("active", "quarantined", "evicted"))
    for name in ("show", "used-by"):
        sub.add_parser(name).add_argument("card_id")
    for name in ("promote", "evict"):
        p = sub.add_parser(name)
        p.add_argument("card_id")
        p.add_argument("--reason", required=True)
    sub.add_parser("review").add_argument("--all", action="store_true", help="include resolved items")
    p = sub.add_parser("resolve")
    p.add_argument("review_id")
    p.add_argument("--decision", required=True)
    p.add_argument("--note", default="")
    args = ap.parse_args(argv)

    root = Path(args.root) if args.root else _ROOT / "runs" / "online" / (args.name or "")
    path = root / "loop_events.jsonl"
    if not path.exists():
        print(f"no loop log at {path}", file=sys.stderr)
        return 2
    log = LoopEventLog(path)
    bank = CardBank(log)

    if args.cmd == "list":
        for c in bank.cards().values():
            if not args.status or c.status == args.status:
                print(_card_line(c))
    elif args.cmd == "show":
        c = bank.get(args.card_id)
        if c is None:
            print(f"no card {args.card_id}", file=sys.stderr)
            return 1
        print(json.dumps({**c.__dict__, "used_by": bank.used_by(c.card_id)}, ensure_ascii=False, indent=1, default=list))
    elif args.cmd == "used-by":
        for u in bank.used_by(args.card_id):
            print(f"{u['task_id']:34s} attempt={u['attempt']} run={u['run_id']} score={u['score']} injected={u['injected']}")
    elif args.cmd in ("promote", "evict"):
        fn = review.promote_card if args.cmd == "promote" else review.evict_card
        try:
            c, closed = fn(bank, args.card_id, args.reason)
        except (KeyError, ValueError) as e:
            print(e, file=sys.stderr)
            return 1
        print(_card_line(c))
        if closed:
            print(f"resolved review items: {closed}")
        if args.cmd == "evict":
            print(f"was handed to: {sorted({u['task_id'] for u in bank.used_by(c.card_id)})}")
    elif args.cmd == "review":
        done = review.resolved_ids(log)
        items = log.events("REVIEW_ENQUEUED") if args.all else review.pending(log)
        for e in items:
            p = e.payload
            extra = f"card={p['card_id']}({p.get('card_status')}) observers={p.get('observers')}" if p.get("card_id") \
                else f"critic={p.get('critic_verdict')} conf={p.get('critic_conf')}"
            print(f"{e.event_id}  {'done' if e.event_id in done else 'OPEN'}  {e.task_id:34s} a{p.get('attempt')} "
                  f"{p['reason']:18s} {extra}")
    elif args.cmd == "resolve":
        try:
            review.resolve(log, args.review_id, args.decision, args.note)
        except (KeyError, ValueError) as e:
            print(e, file=sys.stderr)
            return 1
        print(f"resolved {args.review_id}: {args.decision}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
