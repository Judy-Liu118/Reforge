"""Bind the FROZEN verifier ladder (run_verifier_ladder.py) to the online loop.

Probes, repairs, profile and critic prompt are used unchanged; the ladder's
sha is stamped on every verdict. What this file adds is plumbing only:

  * cheap(): L0 + L1 + L1b for one run, as reforge.online.decision.decide wants
  * critic(): the frozen Critic (k=1, temperature 0), with its outcomes split
    into verdict / error / budget-exhausted — the frozen class reports all
    three as "abstain", which online would push into the review queue
  * critic k=3 (flag): three samples at temperature 0.7 with the frozen
    system prompt; confidence = majority share, so 2/3 (0.67) stays below τ
  * a schema profile for tasks without CSVs (BIRD), where the probes see
    nothing and the critic is the only tier left
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verifier_apply import VERIFIER_SHA, V  # noqa: E402


def _row(task, traj, attempt: int) -> dict:
    """Exactly the fields the frozen critic and L1b read — nothing else (no gold, no pass flag)."""
    return {"run": "online", "replicate": 0, "arm": f"a{attempt}", "case_id": task.id,
            "code_tail": traj.code_tail, "stdout_tail": traj.stdout_tail, "printed": traj.printed}


def sqlite_profile(data_dir: Path, rows: int = 3) -> str:
    parts = []
    for db in sorted(Path(data_dir).glob("*.sqlite")):
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            for (name, ddl) in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table'"):
                n = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                head = conn.execute(f'SELECT * FROM "{name}" LIMIT {rows}').fetchall()
                parts.append(f"### {db.name}:{name} rows={n}\n{ddl}\nhead: {head!r}")
        finally:
            conn.close()
    return "\n".join(parts)


def _parse(raw: str) -> dict:
    """Same JSON extraction the frozen Critic.judge applies."""
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        j = json.loads(m.group(0)) if m else {}
    except (json.JSONDecodeError, ValueError):
        j = {}
    v = j.get("verdict") if j.get("verdict") in ("wrong", "ok", "abstain") else "abstain"
    return {"verdict": v, "confidence": j.get("confidence"), "suspect": j.get("suspect", ""),
            "evidence": j.get("evidence", ""), "parse_ok": bool(j)}


def data_signals(data_dir: Path) -> list[str]:
    """Data-only probe (no code) — usable before the run, e.g. for recall."""
    return sorted({s["signal"] for s in V.detect_signals(V.load_tables(Path(data_dir)))})


def cheap_tiers(task, data_dir: Path, traj, attempt: int, work: Path) -> dict:
    """L0 + L1 + L1b, zero tokens."""
    tables = V.load_tables(Path(data_dir))
    signals = V.detect_signals(tables)
    l1 = V.level1(signals, traj.code_tail)
    l1b = V.level1b(_row(task, traj, attempt), tables, signals, Path(work))
    return {"l0_failed": traj.outcome == "FAILED", "l0_reason": traj.error_hint,
            "signals": sorted({s["signal"] for s in signals}), "l1": l1, "l1b": l1b,
            "verifier_sha": VERIFIER_SHA}


class LadderVerifier:
    sha = VERIFIER_SHA

    def __init__(self, critic_model: str, cache_dir: Path, *, budget: int, k: int = 1,
                 k_temperature: float = 0.7) -> None:
        V.TOKEN_BUDGET = budget          # run-budget cap only, set from outside the frozen file
        self.k, self.k_temperature = k, k_temperature
        self.critic_impl = V.Critic(critic_model, Path(cache_dir) / "critic.jsonl")
        self.k_cache_path = Path(cache_dir) / "critic_k3.jsonl"
        self.k_cache: dict[str, dict] = {}
        if self.k_cache_path.exists():
            for line in self.k_cache_path.read_text(encoding="utf-8").splitlines():
                r = json.loads(line)
                self.k_cache[r["key"]] = r

    @property
    def spent(self) -> int:
        return self.critic_impl.spent + sum(r.get("total_tokens", 0) for r in self.k_cache.values())

    # -- tiers ---------------------------------------------------------------

    def data_signals(self, data_dir: Path) -> list[str]:
        return data_signals(data_dir)

    def cheap(self, task, data_dir: Path, traj, attempt: int, work: Path) -> dict:
        return cheap_tiers(task, data_dir, traj, attempt, work)

    def critic(self, task, data_dir: Path, traj, attempt: int) -> dict:
        tables = V.load_tables(Path(data_dir))
        profile = V.profile_text(tables) if tables else sqlite_profile(Path(data_dir))
        row = _row(task, traj, attempt)
        digest = hashlib.sha256((traj.code_tail + "\x00" + traj.stdout_tail).encode("utf-8")).hexdigest()[:12]
        key = f"online|{task.id}|{digest}"           # content-addressed: a resumed try reuses the call
        out = self._k3(key, task.request, profile, row) if self.k > 1 else \
            self._k1(key, task.request, profile, row)
        out["printed"] = traj.printed
        return out

    def _k1(self, key, request, profile, row) -> dict:
        cv = dict(self.critic_impl.judge(key, request, profile, row))
        err = cv.get("error", "")
        cv["budget_exhausted"] = err == "token budget reached"
        cv["tier"] = "critic"
        return cv

    def _k3(self, key, request, profile, row) -> dict:
        key = f"{key}|k{self.k}"
        if key not in self.k_cache:
            if self.spent > V.TOKEN_BUDGET:
                return {"verdict": "abstain", "error": "token budget reached", "budget_exhausted": True,
                        "tier": "critic_k3"}
            msg = (f"## 任务\n{request}\n\n## 数据画像\n{profile}\n\n## 代码\n```python\n{row['code_tail']}\n```\n\n"
                   f"## 程序输出(末尾)\n{row['stdout_tail']}")
            samples, tokens = [], 0
            for _ in range(self.k):
                try:
                    resp = self.critic_impl.client.chat.completions.create(
                        model=self.critic_impl.model, temperature=self.k_temperature,
                        messages=[{"role": "system", "content": V._CRITIC_SYSTEM}, {"role": "user", "content": msg}])
                except Exception as e:  # noqa: BLE001 — not cached, retried on the next try
                    return {"verdict": "abstain", "error": f"{type(e).__name__}: {str(e)[:300]}",
                            "budget_exhausted": False, "tier": "critic_k3"}
                samples.append(_parse(resp.choices[0].message.content or ""))
                tokens += resp.usage.total_tokens
            self.k_cache[key] = {"key": key, "samples": samples, "total_tokens": tokens,
                                 "model": self.critic_impl.model}
            with open(self.k_cache_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(self.k_cache[key], ensure_ascii=False) + "\n")
        return consistency(self.k_cache[key]["samples"]) | {
            "total_tokens": self.k_cache[key]["total_tokens"], "tier": "critic_k3", "budget_exhausted": False}


def consistency(samples: list[dict]) -> dict:
    """Majority verdict; confidence = majority share. An abstain majority or a tie → abstain."""
    votes = Counter(s["verdict"] for s in samples)
    (top, n), *rest = votes.most_common()
    tie = bool(rest) and rest[0][1] == n
    verdict = "abstain" if tie or top == "abstain" else top
    lead = next((s for s in samples if s["verdict"] == verdict), samples[0])
    return {"verdict": verdict, "confidence": round(n / len(samples), 4) if verdict != "abstain" else None,
            "suspect": lead.get("suspect", ""), "evidence": lead.get("evidence", ""),
            "samples": [{"verdict": s["verdict"], "self_conf": s.get("confidence")} for s in samples]}
