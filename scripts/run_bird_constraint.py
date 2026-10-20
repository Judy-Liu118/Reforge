"""Stage A: does a library fact help when given as a CONSTRAINT, recalled by DB + column?

Offline, reuses the phase-0 harness and questions (runs/bird_ceiling): the
`none` and `given` arms are taken from phase 0 unchanged; one new arm runs.

    constraint   top-1 evidence clause from OTHER questions on the same db,
                 picked by column match (not question similarity), then
                 rewritten by the distiller LLM into a pre-check — names the
                 table/column and what to verify, states NO literal value,
                 threshold or formula — injected in the evidence slot

Pre-registered before any run (written to constraint_config.json):
    target columns   schema columns named in the question (whole-token,
                     case-insensitive, `_`/`-`/space-insensitive) ∪ the top-3
                     columns by bge-m3 cosine(question, "table.column")
    clause columns   schema columns named in the clause (same lexical rule)
    pick             clause with the most target-column overlap; ties → higher
                     bge-m3 cosine(question, clause); overlap 0 → no fact, the
                     arm reuses that question's phase-0 `none` run (on = off)
    rewrite          distiller model, clause + its columns only (never the
                     current question), cached in constraint_plan.json
    decision         "constraint helps" iff constraint − none ≥ 0 AND
                     constraint − recalled(phase 0) ≥ +0.10; CIs Newcombe +
                     bootstrap, reported regardless

    python scripts/run_bird_constraint.py --plan-only     # pick + rewrite, inspect
    python scripts/run_bird_constraint.py                 # run the arm (resumable)
    python scripts/run_bird_constraint.py --report        # → runs/bird_ceiling/report_constraint.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_bird_ceiling as P0  # noqa: E402

ROOT = _ROOT / "runs" / "bird_ceiling"
ARM = "constraint"
TOP_COLS = 3
HEADER = "Pre-checks for this database (learned from earlier failures; verify, do not copy values):"

REWRITE_PROMPT = """You turn a fact about a SQLite database into a reusable PRE-CHECK for someone writing SQL on it later,
for a different question. Rules:
- Name the table/column(s) involved and say what to VERIFY in the data before using them
  (e.g. inspect DISTINCT values / value format / units / which column holds the concept).
- Do NOT state any literal value, code, threshold, number, date or formula from the fact. The later question may
  need different ones; they must be read from the data.
- One or two sentences, imperative, English. Output only the pre-check.

Columns: {columns}
Fact: {fact}"""


def _norm(s: str) -> str:
    return re.sub(r"[\s_\-`\"']+", " ", s.lower()).strip()


def schema_columns(db_path: str) -> list[tuple[str, str]]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tabs = [t for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return [(t, r[1]) for t in tabs for r in con.execute(f'PRAGMA table_info("{t}")')]
    finally:
        con.close()


def named(text: str, cols: list[tuple[str, str]]) -> set[str]:
    t = " " + _norm(text) + " "
    out = set()
    for tab, col in cols:
        k = _norm(col)
        if len(k) >= 2 and re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", t):
            out.add(f"{tab}.{col}")
    return out


def target_columns(question: str, cols, emb) -> set[str]:
    from reforge.online.embed import cosine

    q = emb(question)
    names = [f"{t}.{c}" for t, c in cols]
    vecs = emb.encode([n.replace(".", " ") for n in names])
    top = [n for _, n in sorted(((cosine(q, v), n) for n, v in zip(names, vecs)), reverse=True)[:TOP_COLS]]
    return named(question, cols) | set(top)


def pick(case, pool: list[str], cols, emb) -> dict | None:
    from reforge.online.embed import cosine

    tgt = target_columns(case.question, cols, emb)
    q = emb(case.question)
    best = None
    for cl, v in zip(pool, emb.encode(pool)):
        ccols = named(cl, cols)
        ov = len(ccols & tgt)
        if not ov:
            continue
        key = (ov, cosine(q, v))
        if best is None or key > best[0]:
            best = (key, cl, sorted(ccols))
    if best is None:
        return {"targets": sorted(tgt), "fact": None}
    (ov, sim), cl, ccols = best
    return {"targets": sorted(tgt), "fact": cl, "fact_columns": ccols, "overlap": ov, "sim": round(sim, 4)}


def rewrite(fact: str, columns: list[str]) -> str:
    from openai import OpenAI

    c = OpenAI(base_url=os.environ["REFORGE_DISTILL_BASE_URL"], api_key=os.environ["REFORGE_DISTILL_API_KEY"])
    r = c.chat.completions.create(model=os.environ["REFORGE_DISTILL_MODEL"], temperature=0,
                                  messages=[{"role": "user", "content": REWRITE_PROMPT.format(
                                      fact=fact, columns=", ".join(columns))}])
    return (r.choices[0].message.content or "").strip()


def build_plan(emb) -> list[dict]:
    from reforge.runtime.sql.bird_loader import load_bird_dev

    path = ROOT / "constraint_plan.json"
    cached = {p["case_id"]: p for p in json.loads(path.read_text(encoding="utf-8"))} if path.exists() else {}
    by_db = {db: load_bird_dev(root=_ROOT / "data" / "bird", db_ids={db}) for db in P0.DBS}
    plan = []
    for c in P0.select(by_db):
        db = next(d for d in P0.DBS if c.case_id.endswith("_" + d))
        if c.case_id in cached:
            plan.append(cached[c.case_id])
            continue
        cols = schema_columns(c.db_path)
        p = {"case_id": c.case_id, "db": db, **pick(c, P0.fact_pool_for(c, by_db[db]), cols, emb)}
        p["constraint"] = rewrite(p["fact"], p["fact_columns"]) if p["fact"] else None
        p["own_evidence"] = c.evidence
        plan.append(p)
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    return plan


def leaked_literals(constraint: str, fact: str) -> list[str]:
    """Literal tokens of the fact (quoted strings, numbers) that survived the rewrite."""
    lits = re.findall(r"'([^']+)'|\"([^\"]+)\"|(?<![\w.])(\d+(?:\.\d+)?)(?![\w.])", fact)
    toks = {x for t in lits for x in t if x}
    return sorted(t for t in toks if re.search(rf"(?<![\w.]){re.escape(t)}(?![\w.])", constraint))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if args.report:
        text = report()
        (ROOT / "report_constraint.md").write_text(text, encoding="utf-8")
        print(text)
        return

    p0 = json.loads((ROOT / "run_config.json").read_text(encoding="utf-8"))
    from online_adapters import configure
    configure(p0["agent_model"], p0["thinking_budget"])

    from reforge.online.embed import Encoder
    from reforge.runtime.sql.bird_loader import load_bird_dev

    emb = Encoder(cache_path=ROOT / "embed_cache.jsonl")
    cfg = {"arm": ARM, "top_columns": TOP_COLS, "agent_model": p0["agent_model"],
           "rewriter_model": os.environ.get("REFORGE_DISTILL_MODEL"), "embedding": emb.model_id,
           "questions": p0["questions"],
           "decision": "constraint - none >= 0 AND constraint - recalled(phase0) >= +0.10"}
    (ROOT / "constraint_config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    plan = build_plan(emb)
    for p in plan:
        if p["constraint"]:
            p["leaked"] = leaked_literals(p["constraint"], p["fact"])
    (ROOT / "constraint_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{sum(bool(p['fact']) for p in plan)}/{len(plan)} questions get a constraint; "
          f"{sum(bool(p.get('leaked')) for p in plan)} rewrites kept a literal")
    if args.plan_only:
        return

    cases = {c.case_id: c for db in P0.DBS for c in load_bird_dev(root=_ROOT / "data" / "bird", db_ids={db})}
    rec = ROOT / "records.jsonl"
    rows = [json.loads(x) for x in rec.read_text(encoding="utf-8").splitlines() if x.strip()]
    done = {r["case_id"] for r in rows if r["arm"] == ARM and r["outcome"] != "HARNESS_ERROR"}
    none = {r["case_id"]: r for r in rows if r["arm"] == "none" and ("answer_chars" in r or len(r["final_answer"]) < 2000)}
    for p in plan:
        if p["case_id"] in done:
            continue
        c = cases[p["case_id"]]
        if not p["constraint"]:
            r = {**none[c.case_id], "arm": ARM, "reused_none": True}
        else:
            r = P0.run_one(replace(c, evidence=HEADER + "\n- " + p["constraint"]))
            r.update(case_id=c.case_id, db=p["db"], arm=ARM, correct=P0.grade(c, r["final_answer"]),
                     reused_none=False)
            r["final_answer"] = r["final_answer"][:2000]
        with open(rec, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"  [{c.case_id:38s} {ARM}] correct={r['correct']} reused_none={r['reused_none']} "
              f"tok={r.get('tokens')}", flush=True)


def report() -> str:
    from report_bird_ceiling import _ci, paired

    from reforge.online.canary import wilson

    plan = {p["case_id"]: p for p in json.loads((ROOT / "constraint_plan.json").read_text(encoding="utf-8"))}
    latest = {}
    for x in (ROOT / "records.jsonl").read_text(encoding="utf-8").splitlines():
        if x.strip():
            r = json.loads(x)
            if "answer_chars" not in r and len(r["final_answer"]) >= 2000:
                continue
            latest[(r["case_id"], r["arm"])] = r
    arms = ("none", "given", "recalled", ARM)
    by_q = {q: {a: latest[(q, a)]["correct"] for a in arms if (q, a) in latest} for q in plan}
    full = {q: v for q, v in by_q.items() if all(v.get(a) is not None for a in arms)}
    n = len(full)
    got = [q for q in full if plan[q]["fact"]]
    out = ["# Stage A — constraint-form facts, recalled by DB + column\n",
           f"n = {n} paired questions (phase-0 set); {len(got)} received a constraint, the other {n - len(got)} reuse "
           f"their phase-0 `none` run (on = off).\n",
           "| arm | correct | accuracy [Wilson 95%] |", "|---|---|---|"]
    for a in arms:
        k = sum(v[a] for v in full.values())
        w = wilson(k, n)
        out.append(f"| {a} | {k}/{n} | {k / n:.2f} [{w[0]:.2f}, {w[1]:.2f}] |")
    out += ["\n| Δ | value | Newcombe 95% | bootstrap 95% | agree | x-only / y-only |", "|---|---|---|---|---|---|"]
    res = {}
    for x, y in ((ARM, "none"), ("recalled", "none"), (ARM, "recalled"), ("given", "none")):
        p = paired(full, x, y)
        res[(x, y)] = p
        out.append(f"| {x} − {y} | {p['delta']:+.3f} | {_ci(p['newcombe'])} | {_ci(p['bootstrap'])} | {p['agree']} | "
                   f"{p['x_only']} / {p['y_only']} |")
    sub = {q: full[q] for q in got}
    if sub:
        p = paired(sub, ARM, "none")
        out.append(f"\nOn the {len(sub)} questions that actually received a constraint: constraint − none = "
                   f"{p['delta']:+.3f} {_ci(p['newcombe'])}.")
    helps = res[(ARM, "none")]["delta"] >= 0 and res[(ARM, "recalled")]["delta"] >= 0.10
    out.append(f"\n**Pre-registered decision: {'constraint borrowing holds → stage B' if helps else 'not met'}** "
               f"(constraint − none ≥ 0 AND constraint − recalled ≥ +0.10).\n")
    out += ["## Constraints handed over\n", "| question | fact (other question's evidence) | constraint | literal kept | "
            "none → constraint | given |", "|---|---|---|---|---|---|"]
    for q in got:
        p = plan[q]
        cell = lambda s: (s or "").replace("|", "/").replace("\n", " ")       # noqa: E731
        out.append(f"| `{q}` | {cell(p['fact'])} | {cell(p['constraint'])} | {', '.join(p.get('leaked') or []) or '—'} | "
                   f"{full[q]['none']} → {full[q][ARM]} | {full[q]['given']} |")
    out += ["\n## Caveats\n",
            "- Two things changed at once versus phase 0 (constraint form AND DB+column top-1 recall); this arm "
            "cannot say which one mattered.",
            "- One run per (question, arm); n = 30 → CIs about ±0.2. Phase-0 arms were run earlier; drift in the "
            "API model between runs is not controlled.",
            "- Facts are BIRD's human-written evidence of other questions — still the optimistic source for variant B."]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    main()
