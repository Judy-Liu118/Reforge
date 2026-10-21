"""L2 cross-model LLM critic — the optional second-tier verifier of the experience loop.

Called only on signals the deterministic verifiers cannot confirm (empty output, nan / inf,
values coerced to missing). It gives a *judgment*, not a reproducible confirmation, so a
critic-confirmed failure is tagged `confirmation="judgment"` in its evidence and its card has
verifier="llm_critic" — distinct from `confirmation="reproducible"` cards (re-run / AST).
Either kind still enters QUARANTINE and needs an offline canary to be promoted.

    REFORGE_EXPERIENCE_CRITIC=1              turn the tier on (also needs REFORGE_EXPERIENCE_CARDS=1)
    REFORGE_EXPERIENCE_CRITIC_MODEL          required; must differ from LLM_MODEL (no self-review)
    REFORGE_EXPERIENCE_CRITIC_BASE_URL/_API_KEY   default to LLM_BASE_URL / LLM_API_KEY
    REFORGE_EXPERIENCE_CRITIC_THINKING       "true" / "false" -> extra_body.enable_thinking; unset sends nothing
    REFORGE_EXPERIENCE_CRITIC_MAX_TOKENS     per-process spend cap (default 50000); over it the critic abstains

The system prompt is the frozen verifier-ladder critic prompt (scripts/run_verifier_ladder.py),
verbatim; its sha is stamped on every judgment. Abstention is first-class: only verdict="wrong"
with confidence >= CONFIRM_AT confirms.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

FLAG = "REFORGE_EXPERIENCE_CRITIC"
CONFIRM_AT = 0.8            # verdict wrong below this confidence counts as abstain (suppress false positives)

CRITIC_SYSTEM = """你是对抗式审查员。一个数据处理 agent 写了代码并打印了一个数,程序正常退出。
你的工作是找茬:先【假设这个结果是错的】,找出它最可能错在哪——数据里有什么会让这段代码静默算错的东西
(对照数据画像),或者代码算的并不是任务要的那个量。
然后核实这个错因是否成立:它必须同时满足 (a) 数据画像里确实有这个问题,(b) 代码确实没有正确处理它
(写了检查但没改变计算、或处理写了却不会生效,都算没处理)。
成立 → verdict="wrong";找不到任何成立的错因、且你能说明代码已覆盖画像里的可疑点 → verdict="ok";
拿不准(证据不足、代码逻辑你无法确定是否生效) → verdict="abstain"。不要硬判,允许弃权。
你看不到正确答案。只输出一个 JSON:
{"suspect": "一句话:最可能的错因", "evidence": "一句话:画像/代码里的依据", "verdict": "wrong|ok|abstain", "confidence": 0到1}"""
PROMPT_SHA = hashlib.sha256(CRITIC_SYSTEM.encode("utf-8")).hexdigest()[:12]

_PATH = re.compile(r"[A-Za-z]:\\[^\s'\"`]+?\.(?:csv|jsonl|json)|/[^\s'\"`]+?\.(?:csv|jsonl|json)")


def enabled() -> bool:
    return os.environ.get(FLAG, "0") == "1"


# ---- data profile (same neutral profile the ladder's critic saw) --------------------------------
def _is_num(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def profile_text(paths: list[Path], max_chars: int = 4000) -> str:
    """df.describe()-style profile of the data files a task names; no probe flags, no answers."""
    import pandas as pd

    parts = []
    for p in paths:
        try:
            if p.suffix == ".csv":
                df = pd.read_csv(p, dtype=str, keep_default_na=False)
            else:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                parts.append(f"### {p.name}  lines={len(lines)}\nhead:\n" + "\n".join(lines[:6]))
                continue
        except Exception as e:                       # unreadable file: say so, the critic may abstain
            parts.append(f"### {p.name}  unreadable: {type(e).__name__}")
            continue
        parts.append(f"### {p.name}  rows={len(df)}")
        for col in df.columns:
            vals = df[col].tolist()
            num = [float(v) for v in vals if _is_num(v.strip())]
            line = f"- {col}: n_unique={len(set(vals))} numeric_parseable={len(num)}/{len(vals)}"
            if num and len(num) == len(vals):
                line += f" min={min(num):g} max={max(num):g} mean={sum(num) / len(num):.2f}"
            if len(set(vals)) <= 12 or len(num) < len(vals):
                line += " top_values=" + ", ".join(f"{k!r}×{n}" for k, n in df[col].value_counts().head(8).items())
            non_num = [v for v in vals if not _is_num(v.strip())]
            if num and non_num:
                line += f" non_numeric_examples={non_num[:4]!r}"
            parts.append(line)
        parts.append("head:\n" + df.head(4).to_csv(index=False).strip())
    return "\n".join(parts)[:max_chars]


def data_paths(user_request: str) -> list[Path]:
    return [Path(m) for m in dict.fromkeys(_PATH.findall(user_request or "")) if Path(m).exists()]


# ---- the critic ---------------------------------------------------------------------------------
@dataclass(frozen=True)
class Judgment:
    verdict: str                 # wrong | ok | abstain
    confidence: float | None
    confirmed: bool              # verdict == wrong and confidence >= CONFIRM_AT
    suspect: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    error: str = ""
    parse_ok: bool = True        # False: the reply had no parseable JSON verdict (counted as abstain)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class Critic:
    def __init__(self, model: str, base_url: str, api_key: str, *, generator_model: str,
                 max_tokens: int = 50_000, thinking: str = "") -> None:
        if not model:
            raise ValueError(f"{FLAG}=1 needs REFORGE_EXPERIENCE_CRITIC_MODEL")
        if model == generator_model:
            raise ValueError(f"critic model {model!r} equals the generator model — cross-model review required")
        self.model, self.base_url, self.api_key = model, base_url, api_key
        self.max_tokens, self.thinking, self.spent = max_tokens, thinking, 0

    @classmethod
    def from_env(cls) -> "Critic":
        from reforge.config import config
        return cls(os.environ.get("REFORGE_EXPERIENCE_CRITIC_MODEL", ""),
                   os.environ.get("REFORGE_EXPERIENCE_CRITIC_BASE_URL", config.llm_base_url),
                   os.environ.get("REFORGE_EXPERIENCE_CRITIC_API_KEY", config.llm_api_key),
                   generator_model=config.llm_model,
                   max_tokens=int(os.environ.get("REFORGE_EXPERIENCE_CRITIC_MAX_TOKENS", "50000")),
                   thinking=os.environ.get("REFORGE_EXPERIENCE_CRITIC_THINKING", "").strip().lower())

    def _call(self, user_msg: str) -> tuple[str, int, int]:
        from openai import OpenAI

        extra = {"extra_body": {"enable_thinking": self.thinking == "true"}} if self.thinking in ("true", "false") else {}
        r = OpenAI(base_url=self.base_url, api_key=self.api_key, timeout=300).chat.completions.create(
            model=self.model, temperature=0,
            messages=[{"role": "system", "content": CRITIC_SYSTEM}, {"role": "user", "content": user_msg}], **extra)
        u = r.usage
        return r.choices[0].message.content or "", (u.prompt_tokens if u else 0), (u.completion_tokens if u else 0)

    def judge(self, request: str, profile: str, code: str, stdout: str) -> Judgment:
        if self.spent >= self.max_tokens:
            return Judgment("abstain", None, False, "", self.model, 0, 0, error="critic token cap reached")
        msg = (f"## 任务\n{request}\n\n## 数据画像\n{profile}\n\n## 代码\n```python\n{code[-6000:]}\n```\n\n"
               f"## 程序输出(末尾)\n{(stdout or '')[-2000:]}")
        try:
            raw, pt, ct = self._call(msg)
        except Exception as e:                       # never break the run; an unreachable critic abstains
            logger.warning("critic call failed: %s", e)
            return Judgment("abstain", None, False, "", self.model, 0, 0, error=f"{type(e).__name__}")
        self.spent += pt + ct
        from reforge.observability.llm_events import _emit
        _emit("llm_call_complete", {"model": self.model, "attempt": 1, "latency_ms": 0,
                                    "prompt_tokens": pt, "completion_tokens": ct})
        m = re.search(r"\{.*\}", raw, re.S)
        try:
            j = json.loads(m.group(0)) if m else {}
        except ValueError:
            j = {}
        verdict = j.get("verdict") if j.get("verdict") in ("wrong", "ok", "abstain") else "abstain"
        try:
            conf = float(j.get("confidence")) if j.get("confidence") is not None else None
        except (TypeError, ValueError):
            conf = None
        return Judgment(verdict, conf, verdict == "wrong" and conf is not None and conf >= CONFIRM_AT,
                        str(j.get("suspect", ""))[:300], self.model, pt, ct, parse_ok=bool(j.get("verdict")))
