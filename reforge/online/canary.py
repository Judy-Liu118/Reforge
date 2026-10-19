"""Canary — a held-out gold set, run beside the loop to see what it does to the agent.

Measurement only: canary runs never write cards, never log CARD_RECALLED,
never reach the verifier or the review queue. Its tasks share no table with
the stream (split fixed before the first stream task).

At each checkpoint (t=0 with whatever the bank holds — empty on a fresh run —
then every `every` committed tasks, and once when the stream is exhausted)
every canary task is run as a pair:

    off   agent with no memory
    on    agent with the cards the loop's recall would hand it from the
          active bank at that moment (same request + data-signal recall)

When recall returns nothing the two inputs are identical, so `on` reuses the
`off` run (flagged `on_reused_off`) instead of adding pure sampling noise.

Reported per checkpoint: pass rates with Wilson 95% CIs and the paired lift
(on − off) with Newcombe's Wilson-based CI for paired proportions. With
n = 12 this only detects large changes. The Newcombe CI is cross-checked by a
paired percentile bootstrap on the same pairs (pre-registered: B = 10000,
seed 0; "agree" = same verdict on whether 0 is inside, and both endpoints
within 0.10). Lift is reported from here only — a
retry succeeding inside the loop is not evidence, its card was fit to that task.
"""

from __future__ import annotations

import json
import math
import random
import shutil
from pathlib import Path

from reforge.online.card_bank import ACTIVE, CardBank
from reforge.online.loop_log import LoopEventLog
from reforge.online.shadow import card_contains_answer, score
from reforge.online.slots import task_slots

Z = 1.959964


def _safe(task_id: str) -> str:
    return task_id.replace(":", "_")          # task ids carry a "source:" prefix; not a valid Windows path char


def wilson(k: int, n: int, z: float = Z) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)


def newcombe_paired(a: int, b: int, c: int, d: int, z: float = Z) -> tuple[float, tuple[float, float]] | None:
    """Δ = p_on − p_off for paired binary outcomes, Newcombe (1998) method 10.

    a: both right, b: on right / off wrong, c: on wrong / off right, d: both wrong.
    """
    n = a + b + c + d
    if n == 0:
        return None
    p1, p2 = (a + b) / n, (a + c) / n
    l1, u1 = wilson(a + b, n, z)
    l2, u2 = wilson(a + c, n, z)
    den = math.sqrt((a + b) * (c + d) * (a + c) * (b + d))
    # Newcombe's corrected φ: without the N/2 term, zero discordant pairs give φ = 1 and a
    # zero-width interval — false certainty from a dozen pairs.
    num = max(a * d - b * c - n / 2, 0.0) if a * d > b * c else a * d - b * c
    phi = num / den if den else 0.0
    delta = math.sqrt(max(0.0, (p1 - l1) ** 2 - 2 * phi * (p1 - l1) * (u2 - p2) + (u2 - p2) ** 2))
    eps = math.sqrt(max(0.0, (u1 - p1) ** 2 - 2 * phi * (u1 - p1) * (p2 - l2) + (p2 - l2) ** 2))
    diff = p1 - p2
    return round(diff, 4), (round(max(-1.0, diff - delta), 4), round(min(1.0, diff + eps), 4))


BOOT_B, BOOT_SEED, AGREE_TOL = 10_000, 0, 0.10


def bootstrap_paired(a: int, b: int, c: int, d: int, *, n_boot: int = BOOT_B, seed: int = BOOT_SEED,
                     alpha: float = 0.05) -> tuple[float, float] | None:
    """Percentile CI for Δ = p_on − p_off, resampling the n pairs with replacement."""
    n = a + b + c + d
    if n == 0:
        return None
    pairs = [1] * b + [-1] * c + [0] * (a + d)      # per-pair on − off
    rng = random.Random(seed)
    diffs = sorted(sum(rng.choices(pairs, k=n)) / n for _ in range(n_boot))
    lo, hi = diffs[int(alpha / 2 * n_boot)], diffs[min(n_boot - 1, int((1 - alpha / 2) * n_boot))]
    return round(lo, 4), round(hi, 4)


def ci_agree(ci1, ci2, tol: float = AGREE_TOL) -> bool | None:
    if not ci1 or not ci2:
        return None
    same_zero = (ci1[0] <= 0 <= ci1[1]) == (ci2[0] <= 0 <= ci2[1])
    return same_zero and abs(ci1[0] - ci2[0]) <= tol and abs(ci1[1] - ci2[1]) <= tol


class Canary:
    def __init__(self, root: Path, pairs: list, *, agent, data_signals, log: LoopEventLog, bank: CardBank,
                 every: int = 20, recall_kw: dict | None = None, verbose: bool = False) -> None:
        self.root = Path(root)
        self.pairs = list(pairs)
        self.agent, self.data_signals = agent, data_signals
        self.log, self.bank = log, bank
        self.every = every
        self.recall_kw = dict(recall_kw or {})
        self.verbose = verbose
        self.rows_path = self.root / "canary.jsonl"

    # -- bookkeeping -------------------------------------------------------------

    def rows(self) -> list[dict]:
        if not self.rows_path.exists():
            return []
        return [json.loads(x) for x in self.rows_path.read_text(encoding="utf-8").splitlines() if x.strip()]

    def done(self, checkpoint: int) -> bool:
        return any(e.payload.get("checkpoint") == checkpoint for e in self.log.events("CANARY_RESULT"))

    def due(self, n_committed: int, *, exhausted: bool = False) -> bool:
        if self.done(n_committed):
            return False
        return n_committed % self.every == 0 or exhausted

    def tokens(self) -> int:
        return sum(r.get("tokens", 0) for r in self.rows() if not r.get("on_reused_off"))

    # -- one checkpoint ----------------------------------------------------------

    def run_checkpoint(self, checkpoint: int) -> dict:
        active = self.bank.by_status(ACTIVE)
        version = self.bank.version()
        have = {(r["checkpoint"], r["task_id"], r["arm"]): r for r in self.rows()
                if r["checkpoint"] == checkpoint and not r.get("error")}
        work = self.root / "canary_work" / str(checkpoint)
        try:
            for spec, gold_ref in self.pairs:
                off = have.get((checkpoint, spec.id, "off")) or self._run(checkpoint, spec, gold_ref, "off", [], version,
                                                                         work)
                if (checkpoint, spec.id, "on") in have or off.get("error"):
                    continue
                pristine = spec.build(work / _safe(spec.id) / "pristine")
                hits = self.bank.recall(spec.request, self.data_signals(pristine),
                                        slots=task_slots(spec.request, spec.tables, pristine),
                                        **self.recall_kw) if active else []
                if not hits:
                    self._write({**off, "arm": "on", "card_ids": [], "on_reused_off": True})
                else:
                    self._run(checkpoint, spec, gold_ref, "on", hits, version, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        result = self.summarize(checkpoint, version, len(active))
        self.log.append("CANARY_RESULT", **result)
        if self.verbose:
            print(f"  [canary @{checkpoint}] off={result['off_pass']}/{result['n_pairs']} "
                  f"on={result['on_pass']}/{result['n_pairs']} lift={result['lift']} CI={result['lift_ci']} "
                  f"(with cards: {result['n_on_with_cards']}, bank_active={len(active)})", flush=True)
        return result

    def _run(self, checkpoint, spec, gold_ref, arm, hits, version, work) -> dict:
        d = work / _safe(spec.id) / arm
        data_dir = spec.build(d / "data")
        traj = self.agent.run(spec, data_dir, d / "run", [c.to_memory_record() for c, _ in hits])
        s = score(gold_ref, traj.stdout_tail) if not traj.error else {}
        row = {"checkpoint": checkpoint, "task_id": spec.id, "source": spec.source, "arm": arm,
               "bank_version": version, "card_ids": [c.card_id for c, _ in hits],
               "card_scores": [sc for _, sc in hits], "card_injected": traj.card_injected,
               "correct": s.get("correct"), "answer": s.get("answer"), "printed": traj.printed,
               "tokens": traj.tokens_prompt + traj.tokens_completion, "error": traj.error,
               "on_reused_off": False}
        if hits:
            row["card_has_canary_gold"] = card_contains_answer(gold_ref, [c.body for c, _ in hits])
        self._write(row)
        return row

    def _write(self, row: dict) -> None:
        with open(self.rows_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def summarize(self, checkpoint: int, version: str, n_active: int) -> dict:
        latest: dict[tuple, dict] = {}
        for r in self.rows():
            if r["checkpoint"] == checkpoint and not r.get("error"):
                latest[(r["task_id"], r["arm"])] = r
        a = b = c = d = 0
        with_cards = 0
        for spec, _ in self.pairs:
            off, on = latest.get((spec.id, "off")), latest.get((spec.id, "on"))
            if not off or not on or off["correct"] is None or on["correct"] is None:
                continue
            with_cards += bool(on["card_ids"])
            x, y = bool(on["correct"]), bool(off["correct"])
            a += x and y
            b += x and not y
            c += y and not x
            d += not x and not y
        n = a + b + c + d
        lift = newcombe_paired(a, b, c, d)
        boot = bootstrap_paired(a, b, c, d)
        return {"checkpoint": checkpoint, "bank_version": version, "bank_active": n_active, "n_pairs": n,
                "off_pass": a + c, "on_pass": a + b, "off_ci": wilson(a + c, n), "on_ci": wilson(a + b, n),
                "both_right": a, "on_only": b, "off_only": c, "both_wrong": d,
                "lift": lift[0] if lift else None, "lift_ci": list(lift[1]) if lift else None,
                "lift_ci_boot": list(boot) if boot else None,
                "ci_agree": ci_agree(lift[1] if lift else None, boot),
                "n_on_with_cards": with_cards}
