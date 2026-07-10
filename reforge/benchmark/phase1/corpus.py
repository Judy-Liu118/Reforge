"""Phase 1 BIRD corpus — locked stratified sample.

The 20 case_ids below are the frozen record from
``docs/eval/PHASE1_CORPUS.md``; :func:`select_phase1_case_ids`
re-derives them from the a-priori rule so a test can assert the code
and the document never drift. Changes to either require a revision-log
entry in the corpus doc — the sample is immutable once a run has been
reported against it.
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterable, Mapping

# Locked frozen sample, in run order (simple -> moderate -> challenging,
# each stratum in draw order). case_id format matches
# ``reforge.runtime.sql.bird_loader.load_bird_dev``:
# ``bird_{question_id}_{db_id}``.
PHASE1_CASE_IDS: tuple[str, ...] = (
    "bird_1527_debit_card_specializing",
    "bird_1086_european_football_2",
    "bird_1172_thrombosis_prediction",
    "bird_1384_student_club",
    "bird_301_toxicology",
    "bird_907_formula_1",
    "bird_549_codebase_community",
    "bird_659_codebase_community",
    "bird_392_card_games",
    "bird_1502_debit_card_specializing",
    "bird_288_toxicology",
    "bird_146_financial",
    "bird_1296_thrombosis_prediction",
    "bird_797_superhero",
    "bird_881_formula_1",
    "bird_1110_european_football_2",
    "bird_192_financial",
    "bird_733_superhero",
    "bird_1058_european_football_2",
    "bird_1189_thrombosis_prediction",
)

# --- The a-priori selection rule (PHASE1_CORPUS.md, locked) ---

SAMPLING_SEED = 20260710
STRATA: tuple[tuple[str, int], ...] = (
    ("simple", 12),
    ("moderate", 6),
    ("challenging", 2),
)
# Phase-0 calibration picks are excluded at question level (their five
# specific questions motivated the M1 memory-loop repair); sibling
# questions on the same databases stay eligible.
CALIBRATION_QUESTION_IDS: frozenset[int] = frozenset({7, 1313, 354, 697, 838})
# Gold SQL leaning on SQLite-dialect functions measures dialect trivia,
# not recovery behavior. Same filter as the Phase-0 pool audit.
_DIALECT_FN = re.compile(r"\b(JULIANDAY|STRFTIME|DATETIME|IIF)\s*\(", re.IGNORECASE)


def select_phase1_case_ids(
    dev_entries: Iterable[Mapping],
    *,
    has_db: "callable[[str], bool] | None" = None,
) -> list[str]:
    """Re-derive the frozen sample from raw ``dev.json`` entries.

    ``has_db`` filters out questions whose sqlite file is absent
    (partial BIRD installs); pass ``None`` to keep all entries, which is
    correct against the pinned full dev.zip.
    """
    pool: dict[str, list[Mapping]] = {name: [] for name, _ in STRATA}
    for item in dev_entries:
        if has_db is not None and not has_db(item["db_id"]):
            continue
        if _DIALECT_FN.search(item["SQL"]):
            continue
        if item["question_id"] in CALIBRATION_QUESTION_IDS:
            continue
        stratum = pool.get(item.get("difficulty", ""))
        if stratum is not None:
            stratum.append(item)

    # One RNG instance consumed in stratum order — the draw for a later
    # stratum depends on the earlier draws, exactly as documented.
    rng = random.Random(SAMPLING_SEED)
    picks: list[str] = []
    for name, k in STRATA:
        stratum = sorted(pool[name], key=lambda it: it["question_id"])
        picks.extend(
            f"bird_{it['question_id']}_{it['db_id']}" for it in rng.sample(stratum, k)
        )
    return picks
