"""Admission gate for constraint cards — deterministic, no LLM.

    hard reject  no verifier evidence; empty trigger / guardrail; a banned term
                 (app or API names for env-wide cards, any concrete value the
                 probe redacted); a required anchor missing (e.g. the default
                 page size and `page_index` for a pagination card)
    pass         card enters the store QUARANTINED — it is recalled only after
                 promote(), which needs a passing canary report

The anchors exist because of a measured failure: an abstract pagination card
("read every page") left a 27b agent's behaviour unchanged, while the same
card with the default page size and a checkable trigger flipped 4/4 runs
(docs/EXPERIENCE_LOOP_SUMMARY.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_VALUE = re.compile(r"@|\d{6,}")


@dataclass(frozen=True)
class GateResult:
    ok: bool
    reasons: tuple[str, ...]


def _has(text: str, term: str) -> bool:
    return re.search(r"(?<![\w])" + re.escape(term.lower()) + r"(?![\w])", text) is not None


def check(trigger: str, guardrail: str, *, banned: set[str] | frozenset[str] = frozenset(),
          required: set[str] | frozenset[str] = frozenset(), has_evidence: bool = True) -> GateResult:
    reasons = []
    if not has_evidence:
        reasons.append("no_verifier_evidence")
    if not (trigger or "").strip() or not (guardrail or "").strip():
        reasons.append("empty_field")
    text = f"{trigger} {guardrail}".lower()
    reasons += [f"banned:{t}" for t in sorted(banned) if _has(text, t)]
    reasons += [f"missing:{t}" for t in sorted(required) if not _has(text, t)]
    if _VALUE.search(text):
        reasons.append("concrete_value")
    return GateResult(not reasons, tuple(reasons))
