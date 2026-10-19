"""Pre-registration v3, offline: calibrate the semantic recall threshold, then check canary reachability.

Gold is not read here. Labels used are corpus metadata of the offline dev set
(trap kind = root cause), never task answers.

1. Calibration (dev set only — the 7 tables held out of stream AND canary):
   every dev pair is encoded (bge-m3, request text, as the loop will encode a
   task and a card's source request) and labelled
       positive   trap–trap, same trap kind (same root cause)
       negative   trap–trap different kind, or trap–natural
       excluded   natural–natural (no root-cause label)
   Rule fixed before any similarity was computed: θ = the threshold maximising
   F1 of "same root cause" over dev pairs; ties → the larger θ. Reported with
   AUC, quantiles per class, precision/recall at θ, and top-1 retrieval on dev.
2. Reachability (canary vs stream, nothing run): for each canary task, the
   top-1 stream task (= the best card any stream task could mint) by cosine;
   reachable iff ≥ θ. Natural canary candidates that miss are replaced by the
   next candidate on a stream table; the first 4 reachable are kept.

    python scripts/preflight_recall_v3.py            # → runs/online/v3_prep/{calibration,preflight}.json
"""

from __future__ import annotations

import argparse
import json
import sys
from itertools import combinations
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from reforge.online.embed import DEFAULT_MODEL_DIR, Encoder, cosine  # noqa: E402

N_NATURAL = 4


def _kind(spec) -> str | None:
    return spec.id.split(":", 1)[1].split("__")[0] if spec.source == "trap" else None


def _label(a, b) -> bool | None:
    ka, kb = _kind(a), _kind(b)
    if ka is None and kb is None:
        return None
    return ka is not None and ka == kb


def _auc(pos: list[float], neg: list[float]) -> float:
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 4)


def _q(xs: list[float]) -> dict:
    xs = sorted(xs)
    pick = lambda f: round(xs[min(len(xs) - 1, int(f * len(xs)))], 4)      # noqa: E731
    return {"n": len(xs), "min": round(xs[0], 4), "p10": pick(.1), "p50": pick(.5), "p90": pick(.9),
            "max": round(xs[-1], 4)}


def calibrate(dev, emb) -> dict:
    vec = {s.id: emb(s.request) for s in dev}
    pairs = []
    for a, b in combinations(dev, 2):
        lab = _label(a, b)
        if lab is not None:
            pairs.append((cosine(vec[a.id], vec[b.id]), lab, a.id, b.id))
    pos = [s for s, l, *_ in pairs if l]
    neg = [s for s, l, *_ in pairs if not l]
    best = None
    for t in sorted({round(s, 4) for s, *_ in pairs}):
        tp = sum(s >= t and l for s, l, *_ in pairs)
        fp = sum(s >= t and not l for s, l, *_ in pairs)
        fn = len(pos) - tp
        f1 = 2 * tp / (2 * tp + fp + fn) if tp else 0.0
        if best is None or f1 >= best["f1"]:
            best = {"theta": t, "f1": round(f1, 4), "tp": tp, "fp": fp, "fn": fn,
                    "precision": round(tp / (tp + fp), 4) if tp + fp else None, "recall": round(tp / len(pos), 4)}
    neg_by = {"trap_other_kind": [s for s, l, a, b in pairs if not l and _kind_of(a) and _kind_of(b)],
              "trap_natural": [s for s, l, a, b in pairs if not l and not (_kind_of(a) and _kind_of(b))]}
    # top-1 retrieval among the other dev tasks, as the loop would see it
    top1 = []
    for a in dev:
        b = max((x for x in dev if x.id != a.id), key=lambda x: (cosine(vec[a.id], vec[x.id]), x.id))
        s = cosine(vec[a.id], vec[b.id])
        top1.append({"task": a.id, "top1": b.id, "sim": round(s, 4), "above": s >= best["theta"],
                     "same_root_cause": _label(a, b)})
    th = best["theta"]
    traps = [r for r in top1 if r["task"].startswith("trap:")]
    nats = [r for r in top1 if not r["task"].startswith("trap:")]
    return {
        "rule": "theta = argmax F1(same root cause) over dev pairs; ties -> larger theta",
        "dev_tasks": len(dev), "dev_traps": sum(s.source == "trap" for s in dev), "pairs": len(pairs),
        "auc": _auc(pos, neg), "positive": _q(pos), "negative": _q(neg),
        "negative_by_type": {k: _q(v) for k, v in neg_by.items() if v},
        "chosen": best,
        "top1_dev": {"trap_handed_card": sum(r["above"] for r in traps), "trap_n": len(traps),
                     "trap_handed_same_root_cause": sum(r["above"] and bool(r["same_root_cause"]) for r in traps),
                     "natural_handed_card": sum(r["above"] for r in nats), "natural_n": len(nats)},
        "top1_rows": top1,
        "theta": th,
    }


def _kind_of(task_id: str) -> str | None:
    return task_id.split(":", 1)[1].split("__")[0] if task_id.startswith("trap:") else None


def reach(canary_specs, stream_specs, emb, theta) -> list[dict]:
    out = []
    for c in canary_specs:
        cv = emb(c.request)
        sims = {x.id: cosine(cv, emb(x.request)) for x in stream_specs}
        best = max(sims, key=lambda k: (sims[k], k))
        s = sims[best]
        above = [k for k, v in sims.items() if v >= theta]
        out.append({"canary": c.id, "top1": best, "sim": round(s, 4), "reachable": s >= theta,
                    "n_above": len(above), "top1_same_kind": (_kind_of(c.id) == _kind_of(best)) if _kind_of(c.id) else None,
                    "same_kind_above": sum(_kind_of(x) == _kind_of(c.id) for x in above) if _kind_of(c.id) else None})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default=str(DEFAULT_MODEL_DIR))
    ap.add_argument("--infiagent-dir", default="runs/_external/infiagent")
    args = ap.parse_args()
    out_dir = _ROOT / "runs" / "online" / "v3_prep"
    out_dir.mkdir(parents=True, exist_ok=True)
    emb = Encoder(args.model_dir, cache_path=out_dir / "embed_cache.jsonl")

    from online_adapters import NATURAL_CANARY_V3_CANDIDATES, v3_sources

    canary_all, stream, dev = v3_sources(_ROOT / args.infiagent_dir, NATURAL_CANARY_V3_CANDIDATES)
    cal = calibrate(dev, emb)
    theta = cal["theta"]
    stream_specs = [s for ps in stream.values() for s, _ in ps]
    # natural candidates are not stream tasks here (v3_sources removed all of them); the 4 not
    # chosen go back to the stream, so reachability is checked against stream + unchosen.
    cand = [s for s, _ in canary_all if s.source != "trap"]
    traps = [s for s, _ in canary_all if s.source == "trap"]
    rows_t = reach(traps, stream_specs + cand, emb, theta)
    chosen, rows_n = [], []
    for c in cand:
        pool = stream_specs + [x for x in cand if x.id != c.id]
        r = reach([c], pool, emb, theta)[0]
        rows_n.append(r)
        if r["reachable"] and len(chosen) < N_NATURAL:
            chosen.append(c.id)
    # Re-check against the FINAL stream (v1 stream + the candidates not chosen); drop and refill until stable.
    while True:
        final = stream_specs + [x for x in cand if x.id not in chosen]
        rows_c = reach(traps + [x for x in cand if x.id in chosen], final, emb, theta)
        lost = [r["canary"] for r in rows_c if not r["reachable"] and not r["canary"].startswith("trap:")]
        if not lost:
            break
        chosen = [c for c in chosen if c not in lost]
        nxt = [x.id for x in cand if x.id not in chosen and x.id not in lost
               and reach([x], stream_specs + [y for y in cand if y.id not in chosen and y.id != x.id], emb,
                         theta)[0]["reachable"]]
        if not nxt:
            break
        chosen.append(nxt[0])
    pre = {"theta": theta, "model": emb.model_id, "natural_candidates_first_pass": rows_n,
           "natural_canary_chosen": chosen, "final_check": rows_c,
           "all_trap_canary_reachable": all(r["reachable"] for r in rows_c if r["canary"].startswith("trap:")),
           "natural_filled": len(chosen) == N_NATURAL
                             and all(r["reachable"] for r in rows_c if not r["canary"].startswith("trap:"))}
    (out_dir / "calibration.json").write_text(json.dumps(cal, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "preflight.json").write_text(json.dumps(pre, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in cal.items() if k != "top1_rows"}, ensure_ascii=False, indent=1))
    print(json.dumps(pre, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
