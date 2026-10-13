"""Learning loop with the in-loop judge swapped: gold vs the FROZEN critic.

Question: the cold→warm lift (fail on a seed task → distil a lesson → inject
it → do better on a same-trap task over a different real table) was measured
with a perfect gold judge deciding the card's polarity. How much of it
survives when that judge is the frozen LLM critic, which misses and misfires?

Per replicate (case order shuffled; all arms share it). Every case is both a
transfer target and, for the next table of the same trap, the seed:

    cold            case, empty memory                   (also the seed run)
    warm_gold       ONE card from the seed's cold run; polarity from gold,
                    feedback = "printed X / expected Y" only     (poor feedback)
    warm_critic     same seed run; polarity from the frozen critic,
                    feedback = the critic's diagnosis
    warm_gold_diag  same seed run; polarity from gold,
                    feedback = that same critic diagnosis text

    judge-noise cost = warm_gold_diag − warm_critic   (same feedback, judge differs)
    diagnosis value  = warm_gold_diag − warm_gold     (same judge, feedback differs)

Judge vs score are separate: the in-loop judge only picks the card's polarity
and feedback; every transfer run is SCORED against gold. Gold never enters
the warm_critic loop — its card is distilled without the gold value even when
the two judges agree, so the two arms always carry different cards.
Critic `abstain` → no card at all: warm_critic then runs on empty memory
(`card_polarity == ""`), and abstentions are counted separately.

--screen runs each case cold once, with no judge and no distillation, into a
separate file: admission evidence ("cold fails silently") that is kept out of
the analysed replicates so selection does not inflate the lift.

Experiment code only: the runtime is used as-is; the verifier is imported
frozen (sha stamped on every row); writer.distill_strategy is called
unmodified, just twice, from here.

    python scripts/run_learning_loop.py --traps thousands --tables 2 --replicates 1 \
        --out runs/learning_loop_smoke.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--traps", default="", help="comma list; empty = all")
    ap.add_argument("--tables", type=int, default=0, help="tables per trap; 0 = all")
    ap.add_argument("--replicates", type=int, default=3)
    ap.add_argument("--agent-model", default="qwen3.8-2.4t-a95b")
    ap.add_argument("--critic-model", default="qwen3.8-max-0902")
    ap.add_argument("--thinking-budget", type=int, default=4000, help="agent-only; 0 = uncapped")
    ap.add_argument("--agent-token-stop", type=int, default=700_000)
    ap.add_argument("--screen", action="store_true", help="cold-only admission check (see docstring)")
    ap.add_argument("--out", default="runs/learning_loop.jsonl")
    args = ap.parse_args()

    import run_distill_ablation as D
    D._configure_models(args.agent_model)

    # Driver-only cap on the AGENT's thinking + a call timeout (see
    # run_verifier_infiagent.py). The distiller (other endpoint) is untouched.
    import reforge.models.adapters.llm_client as _lc
    _RealOpenAI = _lc.OpenAI
    agent_base = os.environ["LLM_BASE_URL"]

    def _capped(*a, **k):
        if k.get("base_url") != agent_base or not args.thinking_budget:
            return _RealOpenAI(*a, **k)
        client = _RealOpenAI(*a, **{**k, "timeout": 300.0, "max_retries": 1})
        orig = client.chat.completions.create

        def create(*ca, **ck):
            eb = dict(ck.pop("extra_body", None) or {})
            eb.update(enable_thinking=True, thinking_budget=args.thinking_budget)
            return orig(*ca, extra_body=eb, **ck)
        client.chat.completions.create = create
        return client
    _lc.OpenAI = _capped

    import reforge.memory.writer as W
    from reforge.observability.llm_events import token_accounting
    from verifier_apply import VERIFIER_SHA, V
    from learning_loop_cases import TRAPS

    # Capture the seed trajectory instead of distilling inside the run: the
    # same trajectory is then distilled twice below, once per judge.
    real_distill = W.distill_strategy
    captured: dict = {}

    def _capture(state, label):
        captured["state"], captured["label"] = state, label
        return {}
    W.distill_strategy = _capture

    traps = {t: cs[:args.tables or None] for t, cs in TRAPS.items()
             if not args.traps or t in args.traps.split(",")}
    seed_of = {cs[i].id: cs[i - 1] for cs in traps.values() for i in range(len(cs))}
    cases = [c for cs in traps.values() for c in cs]
    for c in cases:
        v = c.validate()
        if not v["admitted"]:
            sys.exit(f"not admitted: {v}")

    out = _ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    critic = V.Critic(args.critic_model, out.with_suffix(".critic.jsonl"))
    done = {}
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if not r["error"]:
                done[(r["replicate"], r["arm"], r["case_id"])] = r
    spent = sum(r["tokens_prompt"] + r["tokens_completion"] for r in done.values())
    root = Path(tempfile.mkdtemp(prefix="reforge_loop_"))
    print(f"agent={os.environ['LLM_MODEL']} (thinking≤{args.thinking_budget}) "
          f"distiller={os.environ['REFORGE_DISTILL_MODEL']} critic={args.critic_model} "
          f"verifier_sha={VERIFIER_SHA}\ncases={[c.id for c in cases]}", flush=True)

    def distil(label) -> tuple[dict, int]:
        with token_accounting(case_id="distill", seed=0) as led:
            card = real_distill(captured["state"], label)
        return card, led.prompt_tokens + led.completion_tokens

    def preload(card: dict, seed, tag: str) -> list:
        return [{"memory_id": f"card_{tag}",
                 "memory_type": "SUCCESS_PATTERN" if card.get("polarity") == "success" else "FAILURE",
                 "user_request": seed.request, "distilled_strategy": card}] if card else []

    try:
        with open(out, "a", encoding="utf-8") as fh:
            def emit(r: dict) -> None:
                r.update(verifier_sha=VERIFIER_SHA, agent_model=args.agent_model,
                         thinking_budget=args.thinking_budget)
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                fh.flush()
                print(f"  [rep{r['replicate']} {r['arm']:11s} {r['case_id']:26s}] {'PASS' if r['passed'] else 'FAIL'} "
                      f"printed={r['printed']} gold={r['gold']} {r.get('card_polarity', '')} "
                      f"{r.get('critic_verdict', '')} {r['error'][:120]}", flush=True)
                if not r["error"]:
                    done[(r["replicate"], r["arm"], r["case_id"])] = r

            for rep in range(args.replicates):
                order = list(cases)
                random.Random(3000 + rep).shuffle(order)
                print(f"\nreplicate {rep}: {' → '.join(c.id for c in order)}", flush=True)
                rdir = root / f"rep{rep}"

                # phase 1 — cold run of every case; judge it twice; distil two cards
                for i, c in enumerate(order):
                    if (rep, "cold", c.id) in done:
                        continue
                    if spent >= args.agent_token_stop:
                        sys.exit(f"TOKEN STOP: agent tokens {spent}")
                    captured.clear()
                    rec, _ = D.run_one(c, pair_id=c.trap, role="seed+cold", arm="cold", replicate=rep,
                                       order_index=i, run_dir=rdir / f"cold_{c.id}", preload=None,
                                       distill=not args.screen)
                    r = asdict(rec)
                    spent += r["tokens_prompt"] + r["tokens_completion"]
                    if args.screen:
                        emit(r)
                        continue
                    if not rec.error and "state" not in captured:
                        r["error"] = "seed trajectory not captured"
                    if not r["error"]:
                        tables = V.load_tables(rdir / f"cold_{c.id}" / "data")
                        row = {**r, "run": "loop"}
                        # same dedupe convention as the frozen ladder: run|case|printed
                        cv = critic.judge(f"loop|{c.id}|{rec.printed}", c.request, V.profile_text(tables), row)
                        gold_label = captured["label"]
                        flagged, abstained = cv["verdict"] == "wrong", cv["verdict"] == "abstain"
                        # One diagnosis text, shared by warm_critic and warm_gold_diag: those two
                        # arms then differ ONLY in who sets the polarity. No gold value in it.
                        diag = (f"打印值 {rec.printed}。独立审查给出的疑点:{cv.get('suspect', '') or '(无)'};"
                                f"依据:{cv.get('evidence', '') or '(无)'}")
                        terms = gold_label.forbidden_terms
                        card_g, tok_g = distil(gold_label)                    # gold polarity, "printed X / expected Y"
                        card_c, tok_c = ({}, 0) if abstained else distil(
                            W.DistillLabel(correct=not flagged, feedback=diag, forbidden_terms=terms))
                        card_d, tok_d = distil(W.DistillLabel(correct=gold_label.correct, feedback=diag,
                                                              forbidden_terms=terms))
                        r.update(critic_verdict=cv["verdict"], critic_error=cv.get("error", ""),
                                 critic_suspect=cv.get("suspect", ""), critic_evidence=cv.get("evidence", ""),
                                 critic_tokens=cv.get("total_tokens", 0),
                                 judge_agree=None if abstained else (not flagged) == rec.passed,
                                 card_gold=card_g, card_critic=card_c, card_gold_diag=card_d,
                                 diag_feedback=diag, distill_tokens=tok_g + tok_c + tok_d)
                        if cv.get("error"):
                            r["error"] = f"critic: {cv['error']}"
                        elif not card_g or not card_d or not (card_c or abstained):
                            r["error"] = "card missing"
                    emit(r)

                if args.screen:
                    continue
                # phase 2 — transfer with the seed's card, one arm per judge
                for i, c in enumerate(order):
                    seed = seed_of[c.id]
                    src = done.get((rep, "cold", seed.id))
                    if not src:
                        print(f"  !! rep{rep} {c.id}: seed {seed.id} unavailable, warm arms skipped", flush=True)
                        continue
                    for arm, key in (("warm_gold", "card_gold"), ("warm_critic", "card_critic"),
                                     ("warm_gold_diag", "card_gold_diag")):
                        if (rep, arm, c.id) in done:
                            continue
                        if spent >= args.agent_token_stop:
                            sys.exit(f"TOKEN STOP: agent tokens {spent}")
                        rec, _ = D.run_one(c, pair_id=c.trap, role="transfer", arm=arm, replicate=rep,
                                           order_index=i, run_dir=rdir / f"{arm}_{c.id}",
                                           preload=preload(src[key], seed, f"{rep}_{arm}_{seed.id}"),
                                           distill=False, card_source=seed.id)
                        r = asdict(rec)
                        spent += r["tokens_prompt"] + r["tokens_completion"]
                        r.update(seed_passed=src["passed"], seed_critic_verdict=src["critic_verdict"],
                                 seed_judge_agree=src["judge_agree"])
                        emit(r)
        print(f"\nagent tokens={spent} critic tokens={critic.spent} → {out}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
