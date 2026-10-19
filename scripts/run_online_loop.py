"""Online, gold-free learning loop over the data-analysis agent — CLI.

Streams tasks one at a time (trap-injected + InfiAgent natural + optional
local CSV / BIRD), decides every outcome with the frozen verifier ladder
only, distils cards on confident failures, and logs everything to
runs/online/<name>/loop_events.jsonl. Rerunning the same command resumes:
committed tasks are skipped, an interrupted task is rerun from scratch.

Gold reaches only runs/online/<name>/shadow.jsonl, after each commit.

run_config.json carries a `preregistration` block (τ, recall rule, audit
rates, canary schedule, CI methods, stop) written before the first task;
a rerun whose settings differ from it refuses to start.

    # smoke
    python scripts/run_online_loop.py --name smoke --max-tasks 4
    # BIRD adapter smoke
    python scripts/run_online_loop.py --name bird_smoke --sources bird --bird-limit 2
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _kv(s: str) -> dict[str, float]:
    return {k.strip(): float(v) for k, v in (x.split("=") for x in s.split(",") if x.strip())}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True)
    ap.add_argument("--sources", default="trap,infiagent")
    ap.add_argument("--weights", default="trap=1,infiagent=1,csv=1,bird=1",
                    help="mixing weights; trap tasks are mixed into the stream at this ratio")
    ap.add_argument("--audit", default="trap=1.0,infiagent=0.2,csv=0.2,bird=0.2",
                    help="share of cheap-tier passes sent to the critic, per source")
    ap.add_argument("--infiagent-dir", default="runs/_external/infiagent")
    ap.add_argument("--csv-dir", default="")
    ap.add_argument("--bird-dir", default="data/bird")
    ap.add_argument("--bird-limit", type=int, default=3)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-tasks", type=int, default=0, help="per invocation; 0 = whole stream")
    ap.add_argument("--agent-model", default="qwen3.8-2.4t-a95b")
    ap.add_argument("--critic-model", default="qwen3.8-max-0902")
    ap.add_argument("--critic-k", type=int, default=1, help="1 = self-reported confidence; 3 = consistency")
    ap.add_argument("--thinking-budget", type=int, default=4000, help="agent-only; 0 = uncapped")
    ap.add_argument("--critic-budget", type=int, default=900_000)
    ap.add_argument("--agent-token-stop", type=int, default=700_000)
    ap.add_argument("--recall-min-score", type=float, default=0.3)
    ap.add_argument("--recall-rule", choices=("signal_or_slot", "overlap_and_sim", "sim_plus_bonus"),
                    default="signal_or_slot", help="see CardBank.recall; signal_or_slot = pre-registration v2")
    ap.add_argument("--supersedes", default="", help="run name whose pre-registration this one replaces")
    ap.add_argument("--change-reason", default="", help="why the superseded pre-registration was replaced")
    ap.add_argument("--tasks", default="", help="comma list: keep only these stream task ids, in this order (smoke)")
    ap.add_argument("--no-retry", action="store_true")
    ap.add_argument("--canary-every", type=int, default=20, help="committed tasks between canary checkpoints")
    ap.add_argument("--no-canary", action="store_true")
    ap.add_argument("--keep-work", action="store_true")
    args = ap.parse_args()

    from online_adapters import RuntimeAgent, RuntimeDistiller, canary_and_stream_sources, configure
    configure(args.agent_model, args.thinking_budget)

    from online_verifier import LadderVerifier

    from reforge.online.canary import AGREE_TOL, BOOT_B, BOOT_SEED
    from reforge.online.decision import TAU, CriticUnavailable
    from reforge.online.loop import LoopConfig, OnlineLoop
    from reforge.online.tasks import mix

    root = _ROOT / "runs" / "online" / args.name
    root.mkdir(parents=True, exist_ok=True)
    want = [s.strip() for s in args.sources.split(",") if s.strip()]
    canary, sources, holdout = canary_and_stream_sources(
        _ROOT / args.infiagent_dir, csv_dir=(_ROOT / args.csv_dir) if args.csv_dir else None,
        bird_dir=(_ROOT / args.bird_dir) if "bird" in want else None, bird_limit=args.bird_limit)
    sources = {k: v for k, v in sources.items() if k in want}
    stream = list(mix(sources, _kv(args.weights), seed=args.seed))
    if args.tasks:
        by_id = {s.id: (s, g) for s, g in stream}
        stream = [by_id[t.strip()] for t in args.tasks.split(",") if t.strip()]
    canary_ids = {s.id for s, _ in canary}
    assert not canary_ids & {s.id for s, _ in stream} and not holdout & {t for s, _ in stream for t in s.tables}

    verifier = LadderVerifier(args.critic_model, root, budget=args.critic_budget, k=args.critic_k)
    cfg = LoopConfig(audit_rates=_kv(args.audit), audit_seed=args.seed, recall_min_score=args.recall_min_score,
                     recall_rule=args.recall_rule,
                     retry_on_confident_fail=not args.no_retry, agent_token_stop=args.agent_token_stop,
                     keep_work=args.keep_work, verbose=True, canary_every=args.canary_every)
    history = []
    if args.supersedes:
        old_cfg = json.loads((_ROOT / "runs" / "online" / args.supersedes / "run_config.json").read_text(encoding="utf-8"))
        old = old_cfg["preregistration"]
        history = old.get("history", []) + [{"version": old.get("version", 1), "run": args.supersedes,
                                             "block": {k: v for k, v in old.items() if k != "history"},
                                             "superseded_because": args.change_reason}]
    prereg = {
        "version": len(history) + 1,
        "tau": TAU, "tau_status": "uncalibrated, provisional; not tuned on smoke critic results",
        "recall_rule": args.recall_rule, "recall_min_score": args.recall_min_score,
        "recall_rule_text": {
            "signal_or_slot": "card with signals: signal overlap AND request cosine >= min score; card without "
                              "signals: same parsed op AND (overlapping target column OR same table family); "
                              "raw character similarity never used alone",
            "overlap_and_sim": "signal overlap AND request cosine >= min score",
            "sim_plus_bonus": "request cosine + signal bonus >= min score"}[args.recall_rule],
        "recall_known_blind_spot": "a card is never recalled cross-task when it neither shares a signal nor a "
                                   "slot (op + column/table family) with the task; it can still drive its own "
                                   "source task's retry or be promoted by hand",
        "audit_rates": cfg.audit_rates, "audit_seed": args.seed, "critic_k": args.critic_k,
        "minting": "active only from critic-confirmed fail (conf>=tau) or manual promote; cheap-tier-only "
                   "fail -> quarantined, injected only into its own source task's retry",
        "canary": sorted(canary_ids), "canary_every": args.canary_every, "canary_enabled": not args.no_canary,
        "lift_ci": {"primary": "Newcombe 1998 method 10, corrected phi",
                    "cross_check": f"paired percentile bootstrap B={BOOT_B} seed={BOOT_SEED}",
                    "agree": f"same zero-inclusion and both endpoints within {AGREE_TOL}"},
        "agent_token_stop": args.agent_token_stop, "critic_budget": args.critic_budget,
        "stream": [s.id for s, _ in stream], "verifier_sha": verifier.sha,
        "history": history,
    }
    cfg_path = root / "run_config.json"
    if cfg_path.exists():
        locked = json.loads(cfg_path.read_text(encoding="utf-8")).get("preregistration")
        if locked is not None and locked != json.loads(json.dumps(prereg)):
            diff = sorted(k for k in set(locked) | set(prereg) if locked.get(k) != json.loads(json.dumps(prereg)).get(k))
            sys.exit(f"refusing to run: settings differ from the pre-registration in {cfg_path}: {diff}")
    else:
        cfg_path.write_text(json.dumps({**vars(args), "verifier_sha": verifier.sha,
                                        "stream": [s.id for s, _ in stream], "canary": sorted(canary_ids),
                                        "holdout_tables": sorted(holdout), "preregistration": prereg},
                                       ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"agent={args.agent_model} critic={args.critic_model} k={args.critic_k} verifier_sha={verifier.sha}\n"
          f"stream={len(stream)} ({ {k: len(v) for k, v in sources.items()} }) canary={len(canary)} "
          f"holdout_tables={sorted(holdout)}\n→ {root}", flush=True)

    loop = OnlineLoop(root, agent=RuntimeAgent(), verifier=verifier, distiller=RuntimeDistiller(), config=cfg,
                      canary_pairs=None if args.no_canary else canary)
    try:
        s = loop.run(stream, max_tasks=args.max_tasks)
    except CriticUnavailable as e:
        sys.exit(f"PAUSED: critic unavailable ({e}); rerun to resume once budget allows")
    print(json.dumps(s, ensure_ascii=False, indent=1))
    print(f"critic tokens (cumulative, cache incl.)={verifier.spent}")
    print(f"→ report: python scripts/report_online_loop.py --name {args.name}")


if __name__ == "__main__":
    main()
