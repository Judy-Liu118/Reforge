"""Shadow measurement — the ONLY module that reads gold.

It is called after a task's decision is committed to the loop log, writes to
its own JSONL, and nothing in the control path reads that file back. Its job
is to say how good the gold-free decisions were, never to make them.

Per attempt row:
    answer_correct         the agent's answer vs gold
    label / label_correct  the loop's verdict vs gold; None for unverified /
                           uncertain (those make no correctness claim)
    critic_verdict / critic_conf / critic_verdict_correct
                           the critic's own call, kept even when the loop
                           discarded it as below τ — this pair is what the
                           critic calibration curve is drawn from
    tier / confidence      the tier that decided and the confidence it
                           assigned (L1b 0.95, L0 1.0, critic self-report);
                           paired with label_correct per tier. L1b's 0.95 was
                           measured on the trap corpus only — this pairing is
                           how its behaviour on natural data gets measured
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from reforge.online.tasks import GoldRef


def score(gold_ref: GoldRef, stdout: str) -> dict:
    """Score one stdout against gold; a scorer crash is recorded, not raised."""
    try:
        return dict(gold_ref._score(stdout))
    except Exception as e:  # noqa: BLE001 — measurement must not take the loop down
        return {"correct": None, "answer": None, "gold": None, "score_error": f"{type(e).__name__}: {e}"}


def _agrees(claim: str | None, answer_correct: bool | None) -> bool | None:
    """Does a pass/fail-type claim match gold? None when either side is silent."""
    if answer_correct is None:
        return None
    if claim in ("pass", "ok"):
        return answer_correct
    if claim in ("fail", "wrong"):
        return not answer_correct
    return None


class ShadowLog:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._keys: set[tuple] = set()
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    self._keys.add((r["task_id"], r["try_id"], r["attempt"]))

    def has(self, task_id: str, try_id: str, attempt: int) -> bool:
        return (task_id, try_id, attempt) in self._keys

    def record(self, *, task_id: str, try_id: str, attempt: int, source: str,
               gold_ref: GoldRef | None, stdout: str, verdict: dict) -> dict | None:
        """*verdict* is the VERDICT event payload. No gold → no row."""
        if gold_ref is None or self.has(task_id, try_id, attempt):
            return None
        s = score(gold_ref, stdout)
        ok = s.get("correct")
        row = {
            "task_id": task_id, "try_id": try_id, "attempt": attempt, "source": source,
            "answer_correct": ok, "answer": s.get("answer"), "gold": s.get("gold"),
            "label": verdict.get("label"), "confidence": verdict.get("confidence"),
            "tier": verdict.get("tier"), "audited": verdict.get("audited", False),
            "label_correct": _agrees(verdict.get("label"), ok),
            "critic_verdict": verdict.get("critic_verdict"), "critic_conf": verdict.get("critic_conf"),
            "critic_verdict_correct": _agrees(verdict.get("critic_verdict"), ok),
            "card_ids": verdict.get("card_ids", []),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        if "score_error" in s:
            row["score_error"] = s["score_error"]
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._keys.add((task_id, try_id, attempt))
        return row

    def rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(x) for x in self.path.read_text(encoding="utf-8").splitlines() if x.strip()]


def calibration(rows: list[dict], edges: tuple[float, ...] = (0.0, 0.5, 0.7, 0.85, 1.0001)) -> list[dict]:
    """Critic self-confidence vs how often its call matched gold, per bin.

    Only rows where the critic gave a wrong/ok call AND a confidence that
    normalised to a number; rows with unusable confidence are counted apart.
    """
    pts = [(r["critic_conf"], r["critic_verdict_correct"]) for r in rows
           if r.get("critic_verdict_correct") is not None and isinstance(r.get("critic_conf"), (int, float))]
    out = []
    for lo, hi in zip(edges, edges[1:]):
        b = [c for conf, c in pts if lo <= conf < hi]
        out.append({"lo": lo, "hi": min(hi, 1.0), "n": len(b),
                    "accuracy": (sum(b) / len(b)) if b else None,
                    "mean_conf": (sum(conf for conf, _ in pts if lo <= conf < hi) / len(b)) if b else None})
    return out


def calibration_by_tier(rows: list[dict]) -> dict[str, dict]:
    """Per deciding tier: (assigned confidence, did the pass/fail claim match gold).

    Only claims (pass/fail); unverified / uncertain make none. `mean_conf` vs
    `accuracy` is the calibration gap for that tier.
    """
    out: dict[str, dict] = {}
    for r in rows:
        if r.get("label_correct") is None or not isinstance(r.get("confidence"), (int, float)):
            continue
        b = out.setdefault(r.get("tier") or "?", {"n": 0, "correct": 0, "conf_sum": 0.0})
        b["n"] += 1
        b["correct"] += bool(r["label_correct"])
        b["conf_sum"] += r["confidence"]
    return {t: {"n": b["n"], "accuracy": round(b["correct"] / b["n"], 4),
                "mean_conf": round(b["conf_sum"] / b["n"], 4)} for t, b in out.items()}
