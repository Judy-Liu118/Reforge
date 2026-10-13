"""Apply the FROZEN verifier ladder (run_verifier_ladder.py) to new run logs.

Nothing here decides a verdict: every probe, repair, profile and the critic
prompt are imported unchanged. The sha256 of run_verifier_ladder.py is stamped
on every output row so a later edit to the verifier is detectable.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_verifier_ladder as V  # noqa: E402

VERIFIER_SHA = hashlib.sha256(Path(V.__file__).read_bytes()).hexdigest()[:16]


def apply_ladder(rows: list[dict], data_dirs: dict[str, Path], requests: dict[str, str], out_path: Path,
                 *, critic_model: str | None = V.CRITIC_MODEL, critic_scope: str = "all",
                 critic_filter=None, workers: int = 6) -> list[dict]:
    """rows need: run, replicate, arm, case_id, printed, wrong, outcome, code_tail, stdout_tail.

    critic_filter(row) -> bool limits which rows may reach the critic at all
    (token control); rows it rejects get critic=None.
    """
    tables = {c: V.load_tables(d) for c, d in data_dirs.items()}
    signals = {c: V.detect_signals(t) for c, t in tables.items()}
    profiles = {c: V.profile_text(t) for c, t in tables.items()}
    l1 = [V.level1(signals[r["case_id"]], r["code_tail"]) for r in rows]

    root = Path(tempfile.mkdtemp(prefix="reforge_vapply_"))
    todo: dict[tuple, int] = {}
    for i, r in enumerate(rows):
        todo.setdefault((r["case_id"], r["code_tail"], r["printed"]), i)

    def work(item):
        key, i = item
        r = rows[i]
        return key, V.level1b(r, tables[r["case_id"]], signals[r["case_id"]], root / str(i))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        memo = dict(ex.map(work, todo.items()))
    shutil.rmtree(root, ignore_errors=True)

    critic = V.Critic(critic_model, out_path.with_suffix(".critic.jsonl")) if critic_model else None
    out = []
    with open(out_path, "w", encoding="utf-8") as fh:
        for r, a in zip(rows, l1):
            b = memo[(r["case_id"], r["code_tail"], r["printed"])]
            key = f"{r['run']}|{r['case_id']}|{r['printed']}"
            rec = {"run": r["run"], "replicate": r["replicate"], "arm": r["arm"], "group": r.get("group", ""),
                   "case_id": r["case_id"], "printed": r["printed"], "gold": r.get("gold"), "wrong": r["wrong"],
                   "outcome": r["outcome"], "l0_fail": r["outcome"] == "FAILED",
                   "l1_signals": a["signals"], "l1_unhandled": a["unhandled"],
                   "l1_fail_raw": a["fail_raw"], "l1_fail": a["fail"],
                   "l1b_fail": b.get("fail"), "l1b_status": b.get("status"), "l1b_repaired": b.get("repaired"),
                   "critic_key": key, "critic_triggered": False, "critic": None, "verifier_sha": VERIFIER_SHA}
            if critic and (critic_filter is None or critic_filter(r)) and (not a["fail"] or critic_scope == "all"):
                rec["critic"] = critic.judge(key, requests[r["case_id"]], profiles[r["case_id"]], r)
                rec["critic_triggered"] = not a["fail"]
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.append(rec)
    if critic:
        print(f"critic keys={len(critic.cache)} cumulative tokens={critic.spent}")
    return out
