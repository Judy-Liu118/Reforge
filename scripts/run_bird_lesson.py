"""Offline feasibility/ceiling test: abstract guardrail LESSONS (ReasoningBank / MemGuard format) on BIRD.

Deliberately favourable to recall, and honest about it: questions are picked
because their BIRD evidence maps a natural-language word to a CODED literal
(financial Czech codes / status letters / gender letters, toxicology label /
element / bond symbols, thrombosis SEX / Admission flags) that the question
itself does not contain — the kind of convention an agent silently gets wrong.

Arms (paired by question, one run each):
    none     no hint
    lesson   top-1 lesson from a small hand-curated guardrail pool, by bge-m3
             cosine(question, title + when); below θ → no lesson (= none, reused)
    oracle   the pool lesson that matches the question's coded column kind
             (mapping fixed below from the evidence metadata) — separates the
             value of the guardrail from the accuracy of retrieval

Pre-registered before any run (also in run_config.json):
    questions   5 per db (financial, toxicology, thrombosis_prediction), uniform
                random seed 0 among eligible ones, phase-0 questions excluded
    θ           median top-1 cosine over 100 random dev questions (seed 0) from
                the other 8 databases — computed without gold
    injection   one guidance block in the prompt's hint slot, with the fixed
                output rule (reason only; print no checks; no added / dropped /
                merged columns)
    decision    lesson − none ≥ +0.10 and CI excludes 0 → clearly effective;
                ≥ 0 and CI not clearly negative → not harmful, proceed with care;
                clearly negative → stop. Newcombe + bootstrap both reported.
    gold        only the grader executes gold SQL, after each run

Lessons carry no literal value, threshold, formula or value domain; that is
asserted against every quoted literal in these databases' dev evidence.

    python scripts/run_bird_lesson.py --plan-only
    python scripts/run_bird_lesson.py
    python scripts/run_bird_lesson.py --report
"""

from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import sys
from dataclasses import replace
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_bird_ceiling as P0  # noqa: E402

ROOT = _ROOT / "runs" / "bird_lesson"
DBS = ("financial", "toxicology", "thrombosis_prediction")
PER_DB, SEED, N_CALIB = 5, 0, 100
ARMS = ("none", "lesson", "oracle")
CODED = {"financial": ("status", "frequency", "type", "operation", "k_symbol", "gender"),
         "toxicology": ("label", "element", "bond_type"),
         "thrombosis_prediction": ("sex", "admission", "thrombosis")}

POOL = [
    {"id": "L1", "title": "Category words are often stored as short codes",
     "when": "The question filters or groups by a category described in words, such as sex, gender, status, type "
             "or outcome.",
     "lesson": "Stored values are frequently abbreviations, single letters or symbols rather than the question's "
               "words. Before writing the filter, look at the column's DISTINCT values and map the wording to the "
               "stored code by meaning; a literal that does not occur runs without error and silently matches "
               "nothing."},
    {"id": "L2", "title": "Attribute codes may be in another language",
     "when": "The question filters on a transaction, payment, order or account attribute (its type, operation, "
             "purpose or frequency) stated in English.",
     "lesson": "Such attributes may be stored as codes in another language. Read the column's DISTINCT values, "
               "work out what each means, and use the one that matches; never assume the English word is stored."},
    {"id": "L3", "title": "Kinds of links may be encoded as symbols",
     "when": "The question is about kinds of bonds, links or connections between entities, such as single, double "
             "or triple.",
     "lesson": "Kinds are often encoded as symbols rather than words. Inspect the type column's DISTINCT values and "
               "decide which symbol denotes which kind before filtering."},
    {"id": "L4", "title": "Abbreviated names have an exact stored form",
     "when": "The question names a chemical element, unit, country or another entity usually written as an "
             "abbreviation.",
     "lesson": "Abbreviations are stored in one specific spelling and letter case, and SQLite string comparison is "
               "case-sensitive. Check the DISTINCT values for the exact stored form before comparing."},
    {"id": "L5", "title": "Yes/no properties are often stored as flag characters",
     "when": "The question refers to a yes/no property of a record, such as in-patient versus out-patient, "
             "carcinogenic or not, admitted or not, or the outcome of a test.",
     "lesson": "Binary properties are often stored as sign or flag characters. Inspect the column's DISTINCT values "
               "and decide from the column name and context which one denotes the positive case before filtering."},
    {"id": "L6", "title": "An empty result usually means a wrong literal",
     "when": "Any query with a WHERE filter on a text column.",
     "lesson": "If a filter matches zero rows, the literal is probably not how the data stores it. Verify the "
               "literal occurs in the column before trusting an empty or zero result."},
    {"id": "L7", "title": "Joins can multiply rows",
     "when": "Counting entities after joining a one-to-many table.",
     "lesson": "A join can repeat each entity once per related row; decide whether the count needs DISTINCT ids."},
    {"id": "L8", "title": "Integer division truncates",
     "when": "Computing a percentage, ratio or average from counts.",
     "lesson": "SQLite integer division truncates; cast to REAL before dividing."},
    {"id": "L9", "title": "Dates may be stored as text",
     "when": "Filtering by a year, a month or a date range.",
     "lesson": "Dates may be stored as text in one particular format; look at a few stored values and use matching "
               "string or date functions."},
    {"id": "L10", "title": "Missing values change aggregates",
     "when": "Averages, minimums, maximums or rankings over columns that may be missing.",
     "lesson": "Aggregates skip NULLs and comparisons drop them; decide explicitly how missing values should count."},
]
ORACLE = {"gender": "L1", "sex": "L1", "status": "L1", "frequency": "L2", "type": "L2", "operation": "L2",
          "k_symbol": "L2", "bond_type": "L3", "element": "L4", "label": "L5", "admission": "L5", "thrombosis": "L5"}
GUIDE = ("Guidance from earlier failures — use it only in your reasoning: print no checks or exploration, do not "
         "add, drop or merge output columns, and output exactly what the question asks.")


def coded_hits(item: dict) -> list[tuple[str, str]]:
    ev, q = item.get("evidence") or "", item["question"].lower()
    out = []
    for col in CODED[item["db_id"]]:
        for m in re.finditer(rf"(?i)\b{col}\b\s*(?:=|IN\s*\(|!=|<>)\s*'([^']+)'", ev):
            if m.group(1).lower() not in q:
                out.append((col, m.group(1)))
    return out


def eligible(raw: list[dict], exclude: set[str]) -> dict[str, list[dict]]:
    out = {db: [] for db in DBS}
    for x in raw:
        cid = f"bird_{x['question_id']}_{x['db_id']}"
        if x["db_id"] in out and cid not in exclude and coded_hits(x):
            out[x["db_id"]].append({**x, "case_id": cid, "hits": coded_hits(x)})
    return out


def assert_pool_clean(raw: list[dict]) -> list[str]:
    """No quoted literal from these databases' evidence may appear in any lesson.

    Multi-character literals: case-insensitive whole-token match. Single characters
    ('F', '+', '#', ...) only count when quoted, since a capital 'A' starts sentences."""
    lits = {m for x in raw if x["db_id"] in DBS for m in re.findall(r"'([^']+)'", x.get("evidence") or "")}
    text = " ".join(f"{l['title']} {l['when']} {l['lesson']}" for l in POOL)
    bad = []
    for lit in sorted(lits):
        if len(lit) == 1:
            hit = f"'{lit}'" in text or f'"{lit}"' in text
        else:
            hit = re.search(rf"(?i)(?<![\w]){re.escape(lit)}(?![\w])", text) is not None
        if hit:
            bad.append(lit)
    return bad


def block(lesson: dict) -> str:
    return f"{GUIDE}\n- {lesson['title']}: {lesson['lesson']}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agent-model", default="qwen3.8-27b")
    ap.add_argument("--thinking-budget", type=int, default=4000)
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if args.report:
        text = report()
        (ROOT / "report.md").write_text(text, encoding="utf-8")
        print(text)
        return

    from online_adapters import configure
    configure(args.agent_model, args.thinking_budget)
    from reforge.online.embed import Encoder, cosine
    from reforge.runtime.sql.bird_loader import load_bird_dev

    ROOT.mkdir(parents=True, exist_ok=True)
    raw = json.loads((_ROOT / "data" / "bird" / "dev.json").read_text(encoding="utf-8"))
    bad = assert_pool_clean(raw)
    assert not bad, f"lesson pool contains literals from the evidence: {bad}"
    exclude = set(json.loads((_ROOT / "runs" / "bird_ceiling" / "run_config.json").read_text(encoding="utf-8"))["questions"])
    el = eligible(raw, exclude)
    rng = random.Random(SEED)
    picked = [x for db in DBS for x in rng.sample(sorted(el[db], key=lambda x: x["case_id"]), PER_DB)]

    emb = Encoder(cache_path=ROOT / "embed_cache.jsonl")
    keys = emb.encode([f"{l['title']}. {l['when']}" for l in POOL])
    others = [x for x in raw if x["db_id"] not in DBS]
    calib = random.Random(SEED).sample(others, N_CALIB)
    top1 = [max(cosine(v, k) for k in keys) for v in emb.encode([x["question"] for x in calib])]
    theta = round(statistics.median(top1), 4)

    plan = []
    for x in picked:
        qv = emb(x["question"])
        sims = [(cosine(qv, k), l["id"]) for k, l in zip(keys, POOL)]
        s, lid = max(sims)
        oracle = ORACLE[x["hits"][0][0]]
        plan.append({"case_id": x["case_id"], "db": x["db_id"], "question": x["question"], "evidence": x["evidence"],
                     "coded": x["hits"], "lesson": lid if s >= theta else None, "lesson_sim": round(s, 4),
                     "oracle": oracle, "lesson_is_oracle": lid == oracle and s >= theta,
                     "lesson_matches_any_kind": s >= theta and lid in {ORACLE[c] for c, _ in x["hits"]}})
    cfg = {"databases": DBS, "per_db": PER_DB, "seed": SEED, "theta": theta, "theta_rule":
           f"median top-1 cosine over {N_CALIB} random dev questions (seed {SEED}) from the other databases",
           "agent_model": args.agent_model, "thinking_budget": args.thinking_budget, "embedding": emb.model_id,
           "pool": POOL, "oracle_map": ORACLE, "guide": GUIDE, "questions": [p["case_id"] for p in plan],
           "eligible_per_db": {db: len(v) for db, v in el.items()}}
    cfg_path = ROOT / "run_config.json"
    if cfg_path.exists() and json.loads(cfg_path.read_text(encoding="utf-8"))["questions"] != cfg["questions"]:
        sys.exit("question set differs from the existing run_config.json")
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    (ROOT / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"θ={theta}  eligible={cfg['eligible_per_db']}  lesson given {sum(bool(p['lesson']) for p in plan)}/"
          f"{len(plan)}  lesson==oracle {sum(p['lesson_is_oracle'] for p in plan)}/{len(plan)}")
    for p in plan:
        print(f"  {p['case_id']:34s} coded={p['coded'][0]} lesson={p['lesson']}({p['lesson_sim']}) oracle={p['oracle']}")
    if args.plan_only:
        return

    cases = {c.case_id: c for db in DBS for c in load_bird_dev(root=_ROOT / "data" / "bird", db_ids={db})}
    by_id = {l["id"]: l for l in POOL}
    rec = ROOT / "records.jsonl"
    rows = [json.loads(x) for x in rec.read_text(encoding="utf-8").splitlines() if x.strip()] if rec.exists() else []
    done = {(r["case_id"], r["arm"]): r for r in rows if r["outcome"] != "HARNESS_ERROR"}
    for p in plan:
        c = cases[p["case_id"]]
        for arm in ARMS:
            if (c.case_id, arm) in done:
                continue
            lid = p["lesson"] if arm == "lesson" else p["oracle"] if arm == "oracle" else None
            if arm != "none" and lid is None:                       # below θ: identical to none
                r = {**done[(c.case_id, "none")], "arm": arm, "reused_none": True}
            else:
                r = P0.run_one(replace(c, evidence=block(by_id[lid]) if lid else ""))
                r.update(case_id=c.case_id, db=p["db"], arm=arm, lesson=lid, reused_none=False,
                         correct=P0.grade(c, r["final_answer"]))
                r["final_answer"] = r["final_answer"][:2000]
            done[(c.case_id, arm)] = r
            with open(rec, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            print(f"  [{c.case_id:34s} {arm:6s}] lesson={lid} correct={r['correct']} tok={r.get('tokens')}", flush=True)


def report() -> str:
    from report_bird_ceiling import _ci, paired

    from reforge.online.canary import wilson

    cfg = json.loads((ROOT / "run_config.json").read_text(encoding="utf-8"))
    plan = {p["case_id"]: p for p in json.loads((ROOT / "plan.json").read_text(encoding="utf-8"))}
    latest = {}
    for x in (ROOT / "records.jsonl").read_text(encoding="utf-8").splitlines():
        if x.strip():
            r = json.loads(x)
            latest[(r["case_id"], r["arm"])] = r
    full = {q: {a: latest[(q, a)]["correct"] for a in ARMS} for q in plan
            if all((q, a) in latest and latest[(q, a)]["correct"] is not None for a in ARMS)}
    n = len(full)
    out = ["# Guardrail lessons on BIRD — offline feasibility / ceiling test\n",
           "**Deliberately favourable to recall**: questions were chosen because a coded-value convention (that the "
           "question does not spell out) decides them. Not a sample of BIRD.\n",
           f"agent `{cfg['agent_model']}` (thinking ≤ {cfg['thinking_budget']}) · embedding `{cfg['embedding']}` · "
           f"θ = {cfg['theta']} ({cfg['theta_rule']}) · {n} paired questions, one run per (question, arm)\n",
           "| arm | correct | accuracy [Wilson 95%] |", "|---|---|---|"]
    for a in ARMS:
        k = sum(v[a] for v in full.values())
        w = wilson(k, n)
        out.append(f"| {a} | {k}/{n} | {k / n:.2f} [{w[0]:.2f}, {w[1]:.2f}] |")
    out += ["\n| Δ | value | Newcombe 95% | bootstrap 95% | agree | x-only / y-only |", "|---|---|---|---|---|---|"]
    res = {}
    for x, y in (("lesson", "none"), ("oracle", "none"), ("oracle", "lesson")):
        p = paired(full, x, y)
        res[(x, y)] = p
        out.append(f"| {x} − {y} | {p['delta']:+.3f} | {_ci(p['newcombe'])} | {_ci(p['bootstrap'])} | {p['agree']} | "
                   f"{p['x_only']} / {p['y_only']} |")
    p = res[("lesson", "none")]
    lo = p["newcombe"][0]
    # "clearly negative" = the point estimate is below 0; the user's rule gives no number for the CI part,
    # so the lower bound is reported, not used as a cut.
    verdict = ("clearly effective → build the loop" if p["delta"] >= 0.10 and lo > 0 else
               f"not harmful (Δ ≥ 0; Newcombe lower bound {lo:+.2f}) → proceed with care" if p["delta"] >= 0 else
               "negative → stop")
    out.append(f"\n**Pre-registered decision (lesson − none): {verdict}.**\n")
    got = sum(bool(plan[q]["lesson"]) for q in full)
    out.append(f"Retrieval: lesson handed over on {got}/{n}; equal to the oracle lesson on "
               f"{sum(plan[q]['lesson_is_oracle'] for q in full)}/{n}; matching any coded kind of the question on "
               f"{sum(plan[q].get('lesson_matches_any_kind', False) for q in full)}/{n}.\n")
    out += ["## Per question\n", "| question | coded convention (from evidence) | lesson (sim) | oracle | none | lesson | oracle |",
            "|---|---|---|---|---|---|---|"]
    for q, v in full.items():
        pl = plan[q]
        out.append(f"| `{q}` | {pl['coded'][0][0]} = '{pl['coded'][0][1]}' | {pl['lesson'] or '—'} ({pl['lesson_sim']}) | "
                   f"{pl['oracle']} | {v['none']} | {v['lesson']} | {v['oracle']} |")
    out += ["\n## Questions that got worse with a lesson\n"]
    for q, v in full.items():
        for arm in ("lesson", "oracle"):
            if v["none"] and not v[arm]:
                a, b = latest[(q, "none")], latest[(q, arm)]
                out.append(f"- `{q}` ({arm}): none printed {a['final_answer'][:120]!r} → {arm} printed "
                           f"{b['final_answer'][:120]!r}")
    out += ["\n## Caveats\n",
            "- Ceiling test on hand-picked convention questions; n is small and every cell is one sample.",
            "- Different agent model from phase 0 / stage A (qwen3.8-27b vs qwen3.8-max); not comparable across.",
            "- The guardrail pool was written by hand for these conventions; a loop would have to learn such lessons "
            "from its own failures.",
            "- BIRD ships value descriptions next to each database (database_description/*.csv); a lesson pointing "
            "there would likely be stronger, but is an environment fact, not a guardrail, and was left out."]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    main()
