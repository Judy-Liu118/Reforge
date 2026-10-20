"""Card retrieval for AppWorld: app-name coarse filter + bge-m3 cosine(instruction, card trigger), top-k >= theta.

Local embeddings only (no LLM tokens). Gold-free: the instruction text and the cards are all it sees.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reforge.online.embed import Encoder, cosine  # noqa: E402

APPS = ["amazon", "file_system", "gmail", "phone", "simple_note", "splitwise", "spotify", "todoist", "venmo"]


def apps_in(instruction: str) -> set[str]:
    t = instruction.lower().replace(" ", "_")
    return {a for a in APPS if re.search(rf"(?<![a-z]){a}(?![a-z])", t)}


def score(enc: Encoder, instruction: str, cards: list[dict]) -> list[dict]:
    vi, *vc = enc.encode([instruction] + [c["trigger"] for c in cards])
    mentioned = apps_in(instruction)
    rows = []
    for c, v in zip(cards, vc):
        scope = c.get("apps")
        passes = not mentioned or not scope or bool(set(scope) & mentioned)
        rows.append({"id": c["id"], "sim": round(cosine(vi, v), 4), "passes_filter": passes})
    return rows


def recall(enc: Encoder, instruction: str, cards: list[dict], theta: float, k: int = 2) -> tuple[list[dict], list[dict]]:
    rows = score(enc, instruction, cards)
    ok = sorted([r for r in rows if r["passes_filter"] and r["sim"] >= theta], key=lambda r: -r["sim"])[:k]
    chosen = {r["id"] for r in ok}
    return [c for c in cards if c["id"] in chosen], rows
