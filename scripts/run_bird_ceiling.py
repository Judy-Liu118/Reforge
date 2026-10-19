"""Phase 0 for BIRD library-fact memory (Mode 1 variant B): ceiling + go/no-go. Offline.

Same questions, three arms, paired by question (one run per arm; the API
gives no seed control, so "paired" = same question, same prompt otherwise):

    none      evidence removed — no hint at all
    given     the question's own BIRD evidence restored (upper bound)
    recalled  "facts" = evidence clauses of OTHER questions on the same
              database, top-k by bge-m3 cosine(question, clause) — what a
              perfect library-keyed fact memory could hand over

Pre-registered before any run (also written to run_config.json):
    databases  the 6 with the highest same-db evidence-clause reuse in dev
               (toxicology, thrombosis_prediction, superhero,
               debit_card_specializing, financial, student_club)
    questions  5 per database, uniform random (seed 0) among questions that
               HAVE evidence — not conditioned on reuse; 30 total
    recall     clause pool = every other dev question's evidence on the same
               db, split on ';' / sentence ends, deduplicated; top-5 by
               cosine, no threshold; the question's own evidence is never in
               its pool
    grading    BIRD execution accuracy (comparator.compare_results) on the
               runtime's final answer; gold SQL is executed here, after the
               run, and never reaches a prompt
    go/no-go   go iff recalled − none ≥ +0.10 AND its paired 95% CI excludes 0
               (Newcombe, cross-checked by bootstrap)

    python scripts/run_bird_ceiling.py --smoke 1      # 1 question × 3 arms
    python scripts/run_bird_ceiling.py                # full; resumable
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from dataclasses import replace
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

DBS = ("toxicology", "thrombosis_prediction", "superhero", "debit_card_specializing", "financial", "student_club")
PER_DB, SEED, TOP_K = 5, 0, 5
ARMS = ("none", "given", "recalled")
FACTS_HEADER = "Known facts about this database (learned from earlier questions):"


def clauses(evidence: str) -> list[str]:
    parts = re.split(r";\s*|\.\s+(?=[A-Z])", evidence or "")
    return [p.strip().rstrip(".") for p in parts if len(p.strip()) > 8]


def select(cases_by_db: dict) -> list:
    rng = random.Random(SEED)
    out = []
    for db in DBS:
        pool = sorted((c for c in cases_by_db[db] if c.evidence.strip()), key=lambda c: c.case_id)
        out += rng.sample(pool, PER_DB)
    return out


def recalled_facts(case, pool, emb) -> list[tuple[str, float]]:
    from reforge.online.embed import cosine

    q = emb(case.question)
    vecs = emb.encode(pool)
    scored = sorted(((cosine(q, v), f) for f, v in zip(pool, vecs)), reverse=True)[:TOP_K]
    return [(f, round(s, 4)) for s, f in scored]


def arm_case(case, arm, facts):
    if arm == "none":
        return replace(case, evidence="")
    if arm == "given":
        return case
    return replace(case, evidence=FACTS_HEADER + "\n" + "\n".join(f"- {f}" for f, _ in facts))


def run_one(case) -> dict:
    """Agent run on the arm's case; the gold is read only after the run returns."""
    from reforge.benchmark.phase0.driver import _isolated_memory_scope
    from reforge.observability.llm_events import token_accounting
    from reforge.runtime.orchestration.engine.runner import RuntimeRunner
    from reforge.runtime.sql.prompt import build_prompt

    t0 = time.perf_counter()
    with token_accounting(case_id=case.case_id, seed=0) as ledger, _isolated_memory_scope():
        try:
            state = RuntimeRunner().run(build_prompt(case))
            final = state.outcome_state.final_answer or ""
            outcome = str(state.outcome_state.task_outcome)
            retries = int(getattr(state.control_state, "retry_count", 0) or 0)
            err = ""
        except Exception as e:  # noqa: BLE001
            final, outcome, retries, err = "", "HARNESS_ERROR", 0, f"{type(e).__name__}: {e}"
    return {"final_answer": final, "answer_chars": len(final), "outcome": outcome, "attempts": retries + 1,
            "error": err,
            "tokens": ledger.total_tokens, "seconds": round(time.perf_counter() - t0, 1)}


def grade(case, final_answer: str) -> bool | None:
    from reforge.runtime.sql.comparator import compare_results, run_sql
    from reforge.runtime.sql.prompt import parse_rows

    try:
        return compare_results(parse_rows(final_answer), run_sql(case.db_path, case.gold_sql),
                               order_sensitive=case.expects_ordering)
    except Exception:  # noqa: BLE001 — gold SQL failure is a harness problem, excluded
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="bird_ceiling")
    ap.add_argument("--agent-model", default="qwen3.8-max")
    ap.add_argument("--thinking-budget", type=int, default=4000)
    ap.add_argument("--smoke", type=int, default=0, help="only the first N questions")
    args = ap.parse_args()

    from online_adapters import configure
    configure(args.agent_model, args.thinking_budget)

    from reforge.online.embed import Encoder
    from reforge.runtime.sql.bird_loader import load_bird_dev

    root = _ROOT / "runs" / args.name
    root.mkdir(parents=True, exist_ok=True)
    by_db = {db: load_bird_dev(root=_ROOT / "data" / "bird", db_ids={db}) for db in DBS}
    picked = select(by_db)
    emb = Encoder(cache_path=root / "embed_cache.jsonl")
    plan = []
    for c in picked:
        db = next(d for d in DBS if c.case_id.endswith("_" + d))
        pool = fact_pool_for(c, by_db[db])
        facts = recalled_facts(c, pool, emb)
        own = {x.lower() for x in clauses(c.evidence)}
        plan.append({"case_id": c.case_id, "db": db, "difficulty": c.difficulty, "question": c.question,
                     "evidence": c.evidence, "pool_size": len(pool), "facts": facts,
                     "own_clause_recalled": sum(f.lower() in own for f, _ in facts),
                     "own_clauses": len(own)})
    cfg_path = root / "run_config.json"
    cfg = {"databases": DBS, "per_db": PER_DB, "seed": SEED, "top_k": TOP_K, "arms": ARMS,
           "agent_model": args.agent_model, "thinking_budget": args.thinking_budget,
           "embedding": emb.model_id, "questions": [p["case_id"] for p in plan],
           "go_rule": "recalled - none >= +0.10 and paired 95% CI (Newcombe) excludes 0"}
    if cfg_path.exists() and json.loads(cfg_path.read_text(encoding="utf-8"))["questions"] != cfg["questions"]:
        sys.exit("question set differs from the existing run_config.json")
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    (root / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")

    rec_path = root / "records.jsonl"
    done = set()
    if rec_path.exists():
        for line in rec_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                # early records stored a 2000-char prefix and were graded on it: rerun those
                maybe_cut = "answer_chars" not in r and len(r["final_answer"]) >= 2000
                if r["outcome"] != "HARNESS_ERROR" and not maybe_cut:
                    done.add((r["case_id"], r["arm"]))
    todo = list(zip(picked, plan))[: args.smoke or None]
    for c, p in todo:
        for arm in ARMS:
            if (c.case_id, arm) in done:
                continue
            ac = arm_case(c, arm, p["facts"])
            r = run_one(ac)
            # grade on the FULL answer (multi-thousand-row results), store only a prefix
            r.update(case_id=c.case_id, db=p["db"], arm=arm, correct=grade(c, r["final_answer"]))
            r["final_answer"] = r["final_answer"][:2000]
            with open(rec_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            print(f"  [{c.case_id:38s} {arm:8s}] correct={r['correct']} outcome={r['outcome']} "
                  f"attempts={r['attempts']} tok={r['tokens']} {r['seconds']}s", flush=True)


def fact_pool_for(case, db_cases) -> list[str]:
    seen, out = set(), []
    for c in db_cases:
        if c.case_id == case.case_id:
            continue
        for cl in clauses(c.evidence):
            if cl.lower() not in seen:
                seen.add(cl.lower())
                out.append(cl)
    return out


if __name__ == "__main__":
    main()
