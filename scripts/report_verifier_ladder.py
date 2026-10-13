"""Report for run_verifier_ladder.py — recall / precision / token per tier.

Positive class = the run printed a WRONG number (gold verdict). A tier
"fires" when it flags the run; recall = wrong runs flagged, precision =
flagged runs that are wrong. Critic tokens are attributed to a ladder only
for the deduplicated keys that ladder actually sends to the critic.

    python scripts/report_verifier_ladder.py runs/verifier_ladder.jsonl
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

SEMANTIC_CASES = ("S4_",)     # contaminant is a legal value; only its meaning makes it wrong


def load(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def critic_wrong(r: dict) -> bool:
    return bool(r["critic"]) and r["critic"].get("verdict") == "wrong"


TIERS = {
    "L0 启发式(线上判定)": (lambda r: r["l0_fail"], None),
    "L1 仅信号(不看代码)": (lambda r: r["l1_fail_raw"], None),
    "L1 静态探针": (lambda r: r["l1_fail"], None),
    "L1b 变形复跑": (lambda r: bool(r["l1b_fail"]), None),
    "L2 单独(critic)": (critic_wrong, lambda r: True),
    "L1 → L2": (lambda r: r["l1_fail"] or critic_wrong(r), lambda r: not r["l1_fail"]),
    "L1b → L2": (lambda r: bool(r["l1b_fail"]) or critic_wrong(r), lambda r: not r["l1b_fail"]),
}


def score(rows: list[dict], fire, sent) -> dict:
    tp = sum(fire(r) and r["wrong"] for r in rows)
    fp = sum(fire(r) and not r["wrong"] for r in rows)
    fn = sum((not fire(r)) and r["wrong"] for r in rows)
    keys = {r["critic_key"]: r["critic"] for r in rows if sent and sent(r) and r["critic"]}
    tok = sum(c.get("total_tokens", 0) for c in keys.values())
    trig = sum(bool(sent and sent(r)) for r in rows)
    return {"tp": tp, "fp": fp, "fn": fn, "recall": tp / (tp + fn) if tp + fn else float("nan"),
            "precision": tp / (tp + fp) if tp + fp else float("nan"),
            "tokens": tok, "tok_per_rec": tok / len(rows) if rows else 0.0,
            "calls": len(keys), "trigger": trig / len(rows) if rows else 0.0}


def table(rows: list[dict], title: str) -> list[tuple[str, dict]]:
    n_wrong = sum(r["wrong"] for r in rows)
    print(f"\n== {title}: n={len(rows)} wrong={n_wrong} right={len(rows) - n_wrong} ==")
    print(f"  {'tier':22s} {'recall':>14s} {'precision':>16s} {'误报':>5s} {'tok/条':>8s} {'触发率':>7s} {'调用':>5s} {'累计tok':>8s}")
    out = []
    for name, (fire, sent) in TIERS.items():
        s = score(rows, fire, sent)
        out.append((name, s))
        rec = f"{s['recall']:.3f} ({s['tp']}/{s['tp'] + s['fn']})"
        pre = f"{s['precision']:.3f} ({s['tp']}/{s['tp'] + s['fp']})" if s["tp"] + s["fp"] else "  —  (0/0)"
        print(f"  {name:22s} {rec:>14s} {pre:>16s} {s['fp']:>5d} {s['tok_per_rec']:>8.1f} "
              f"{s['trigger']:>6.1%} {s['calls']:>5d} {s['tokens']:>8d}")
    return out


def main() -> None:
    rows = load(Path(sys.argv[1]))
    naive = {}
    by_case = defaultdict(Counter)
    for r in rows:
        if r["wrong"]:
            by_case[r["case_id"]][r["printed"]] += 1
    for cid, c in by_case.items():
        naive[cid] = c.most_common(1)[0][0]
    for r in rows:
        if not r["wrong"]:
            r["etype"] = ""
        elif r["case_id"].startswith(SEMANTIC_CASES):
            r["etype"] = "语义型"
        elif r["printed"] == naive[r["case_id"]]:
            r["etype"] = "结构型"
        else:
            r["etype"] = "非典型"

    print(f"records={len(rows)}  label_fixed={sum(r.get('label_fixed', False) for r in rows)}")
    allt = table(rows, "全部")
    for run in sorted({r["run"] for r in rows}):
        table([r for r in rows if r["run"] == run], f"{run} 轮")

    print("\n== 按错误类型的召回 (命中/该类错误数) ==")
    types = ("结构型", "语义型", "非典型")
    print(f"  {'tier':22s} " + " ".join(f"{t:>12s}" for t in types))
    for name, (fire, _s) in TIERS.items():
        cells = []
        for t in types:
            xs = [r for r in rows if r["etype"] == t]
            cells.append(f"{sum(fire(r) for r in xs)}/{len(xs)}")
        print(f"  {name:22s} " + " ".join(f"{c:>12s}" for c in cells))

    print("\n== 按 case 的召回 / 误报 (L1 静态 | L1b | L1→L2 | L1b→L2) ==")
    pick = ("L1 静态探针", "L1b 变形复跑", "L1 → L2", "L1b → L2")
    for cid in sorted({r["case_id"] for r in rows}):
        xs = [r for r in rows if r["case_id"] == cid]
        w = [r for r in xs if r["wrong"]]
        ok = [r for r in xs if not r["wrong"]]
        cells = [f"{sum(TIERS[p][0](r) for r in w)}/{len(w)} fp{sum(TIERS[p][0](r) for r in ok)}/{len(ok)}"
                 for p in pick]
        print(f"  {cid:30s} " + " | ".join(f"{c:>15s}" for c in cells))

    print("\n== L1b 状态 ==", dict(Counter(r["l1b_status"] for r in rows)))

    uniq = {r["critic_key"]: r for r in rows if r["critic"]}
    print(f"\n== critic(去重后 {len(uniq)} 个 key) ==")
    for run in sorted({r["run"] for r in rows}):
        ks = [r for r in uniq.values() if r["run"] == run]
        c = Counter((r["wrong"], r["critic"]["verdict"]) for r in ks)
        print(f"  {run:9s} 错→wrong {c[(True, 'wrong')]}  错→ok {c[(True, 'ok')]}  错→abstain {c[(True, 'abstain')]}"
              f"  |  对→wrong {c[(False, 'wrong')]}  对→ok {c[(False, 'ok')]}  对→abstain {c[(False, 'abstain')]}")
    errs = [r["critic"] for r in uniq.values() if r["critic"].get("error")]
    toks = [r["critic"].get("total_tokens", 0) for r in uniq.values()]
    print(f"  实际调用累计 token={sum(toks)}  均值/次={sum(toks) / max(1, len(toks)):.0f}  报错={len(errs)}")
    for r in uniq.values():
        c = r["critic"]
        bad = (c["verdict"] == "wrong") != r["wrong"]
        if bad:
            print(f"    miss  {r['critic_key']:55s} wrong={r['wrong']!s:5} verdict={c['verdict']:7s} {c.get('suspect', '')[:90]}")

    print("\n== 召回 vs token 曲线 (全部记录) ==")
    for name, s in sorted(allt, key=lambda x: (x[1]["tok_per_rec"], x[1]["recall"])):
        bar = "█" * round(s["recall"] * 40)
        print(f"  {s['tok_per_rec']:>7.1f} tok/条  {s['recall']:.3f} {bar:40s} P={s['precision']:.3f}  {name}")


if __name__ == "__main__":
    main()
