"""ExecutionMemory — records and recalls runtime execution experiences.

Independent from Governor/workflow. JSONL-based, no new dependencies.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from reforge.memory.fingerprint import extract_fingerprint
from reforge.paths import execution_memory_path


class ExecutionRecord(BaseModel):
    """A single runtime execution experience — request, resolution, repair strategy."""

    timestamp: str = Field(default="")
    request: str = Field(default="")
    outcome: str = Field(default="")
    failure_mode: str = Field(default="")
    retryable: bool = Field(default=False)
    repair_strategy: str = Field(default="")
    task_intent: str = Field(default="")
    problem_signature: dict = Field(default_factory=dict)
    error_type: str = Field(default="")


class ExecutionMemory:
    """Stores and retrieves execution experiences. JSONL-backed, scored search."""

    def __init__(self, path: Path | None = None) -> None:
        # Resolve at call time so REFORGE_PROJECT_DIR / chdir from test
        # isolation harnesses take effect.
        self._path = path or execution_memory_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        request: str,
        outcome: str,
        failure_mode: str,
        retryable: bool = False,
        repair_strategy: str = "",
        task_intent: str = "",
        problem_signature: dict | None = None,
        error_type: str = "",
        traceback: str = "",
    ) -> None:
        sig = problem_signature
        # An empty dict means the same thing as None here — the caller had no
        # structural signal. Treating only None as absent let {} through and
        # wrote signature-less records, which recall_similar's structural-match
        # admission gate then refuses to surface.
        if not sig:
            fp = extract_fingerprint(traceback, error_type)
            sig = fp.to_dict()
        rec = ExecutionRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            request=request,
            outcome=outcome,
            failure_mode=failure_mode,
            retryable=retryable,
            repair_strategy=repair_strategy,
            task_intent=task_intent,
            problem_signature=sig,
            error_type=error_type,
        )
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(rec.model_dump_json() + "\n")

    def recall_similar(
        self,
        request: str,
        failure_mode: str,
        problem_signature: dict | None = None,
    ) -> list[ExecutionRecord]:
        """Recall the top-3 most similar past execution experiences.

        Uses weighted scoring instead of hard failure_mode filtering,
        so partial matches on problem_signature still surface useful records.

        Admission requires a match on at least one *qualifying* structural
        signal — failure_mode, root_cause, or a specific fingerprint field
        (error_class / missing_module / missing_key / missing_file /
        undefined_name). Low-specificity signals — request-text overlap and
        `domain` — only order candidates that already qualified; they cannot
        admit a record on their own. Without this gate an unrelated
        TimeoutError record rode into the top-3 (and potentially into
        `records[0]`, which ClassifyStage forwards as the repair_hint) on
        shared function words alone.
        """
        if not self._path.exists():
            return []

        query_words = set(request.lower().split())
        sig = problem_signature or {}
        results: list[tuple[float, ExecutionRecord]] = []

        with open(self._path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = ExecutionRecord.model_validate_json(line)
                qualifies, structural, keyword = _score(rec, query_words, failure_mode, sig)
                if qualifies:
                    results.append((structural + keyword, rec))

        results.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in results[:3]]


# Upper bound on the request-text contribution. Deliberately below the
# smallest single-field structural weight that can decide a ranking, so text
# overlap acts as a tie-breaker between structurally comparable records and
# can never reorder records that differ structurally.
_KEYWORD_WEIGHT = 3.0


def _keyword_score(query_words: set[str], rec_words: set[str]) -> float:
    """Sørensen–Dice overlap of the two request texts, scaled to _KEYWORD_WEIGHT.

    Normalised by the *combined* length rather than counted raw: a raw count
    rewards verbosity, since a long request shares more function words with
    everything. Dice divides the shared words by how much text was needed to
    share them, so padding a request with filler lowers its score instead of
    inflating it.
    """
    if not query_words or not rec_words:
        return 0.0
    shared = len(query_words & rec_words)
    if not shared:
        return 0.0
    return 2.0 * shared / (len(query_words) + len(rec_words)) * _KEYWORD_WEIGHT


# Fingerprint fields specific enough that a match grants a record eligibility
# to be recalled. failure_mode and root_cause (scored below) are qualifiers
# too. `domain` and request-word overlap are deliberately NOT here — they only
# order candidates that already qualified.
#
# No "error_type" entry: FailureFingerprint.to_dict() used to emit it as an
# alias of error_class, so weighting both scored one signal twice.
#
# Why `domain` is excluded (the premise, not just the verdict): this runtime's
# scope is narrowed to Python script generation, so `domain` is effectively
# constant ("python"/"pandas"/…) across nearly every stored record. Used as an
# admission condition it degenerates into an always-true predicate and would
# admit structurally-unrelated records. It stays a ranking tie-breaker only.
# If the scope later widens to multiple languages, `domain`'s specificity rises
# again and this qualifier/tie-breaker split must be re-evaluated.
_QUALIFYING_FINGERPRINT_KEYS: tuple[tuple[str, float], ...] = (
    ("error_class", 4.0),
    ("missing_module", 5.0),
    ("missing_key", 4.0),
    ("missing_file", 3.0),
    ("undefined_name", 3.0),
)


def _score(
    rec: ExecutionRecord,
    query_words: set[str],
    failure_mode: str,
    sig: dict,
) -> tuple[bool, float, float]:
    """Score *rec* against the query as (qualifies, structural, keyword).

    `qualifies` is the admission gate: True iff the record matches at least one
    *qualifying* structural signal (failure_mode / root_cause / a
    _QUALIFYING_FINGERPRINT_KEYS field). `structural` and `keyword` are the
    ranking contributions — `recall_similar` admits on `qualifies` and ranks on
    `structural + keyword`. Returned separately so a record's position can be
    traced back to structural vs text-overlap evidence.
    """
    qualifies = False
    structural = 0.0
    rec_sig = rec.problem_signature

    # failure_mode match — a qualifier
    if rec.failure_mode == failure_mode:
        qualifies = True
        structural += 5.0
    elif failure_mode and (failure_mode in rec.failure_mode or rec.failure_mode in failure_mode):
        qualifies = True
        structural += 2.0

    # Structured fingerprint exact matches (highest precision) — qualifiers.
    for key, weight in _QUALIFYING_FINGERPRINT_KEYS:
        qv = sig.get(key)
        rv = rec_sig.get(key)
        if qv and rv and qv == rv:
            qualifies = True
            structural += weight

    # root_cause structural match — a qualifier
    if sig.get("root_cause") and sig["root_cause"] == rec_sig.get("root_cause"):
        qualifies = True
        structural += 3.0

    # domain match — ranking tie-breaker only (see _QUALIFYING_FINGERPRINT_KEYS
    # note): too low-specificity in the current single-language scope to admit.
    if sig.get("domain") and sig["domain"] == rec_sig.get("domain"):
        structural += 2.0

    keyword = _keyword_score(query_words, set(rec.request.lower().split()))
    return qualifies, structural, keyword
