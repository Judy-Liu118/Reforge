"""Phase 1 BIRD ablation harness — pre-registered formulas pinned.

The metric formulas, pairing rule, and sensitivity operationalization
are locked in docs/eval/PHASE0_METRICS.md v4 + docs/eval/PHASE1_CORPUS.md.
These tests pin the harness-side implementation of those locks so a
future refactor cannot silently change what a reported number means.
All tests are hermetic — no BIRD data, no LLM, no runtime.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from reforge.benchmark.phase1.corpus import (
    CALIBRATION_QUESTION_IDS,
    PHASE1_CASE_IDS,
    select_phase1_case_ids,
)
from reforge.benchmark.phase1.driver import (
    AttemptObservation,
    Phase1Record,
    PhaseOneDriver,
    load_records,
)
from reforge.benchmark.phase1.report import (
    fn_rate_all_attempts,
    fn_rate_correct_attempts,
    l6_run_count,
    mean_attempts_on_unsolved,
    paired_deltas,
    recovery_rate,
    success_rate,
    tokens_per_solved,
    write_report,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record(
    *,
    case_id: str = "bird_1_db",
    mode: str = "governor",
    seed: int = 0,
    passed: bool = True,
    attempts: int = 1,
    tokens_unknown: bool = False,
    tokens: int = 100,
    runtime_outcome: str = "SUCCESS",
    observations: list[AttemptObservation] | None = None,
    difficulty: str = "simple",
) -> Phase1Record:
    return Phase1Record(
        case_id=case_id,
        difficulty=difficulty,
        mode=mode,
        seed=seed,
        passed=passed,
        first_try=passed and attempts == 1,
        recovered=passed and attempts > 1,
        attempts=attempts,
        action="ACCEPT" if passed else "STOP",
        policy_reason="",
        failure_mode="",
        runtime_outcome=runtime_outcome,
        retry_count=attempts - 1,
        duration_ms=1000.0,
        tokens_prompt=tokens,
        tokens_completion=tokens,
        tokens_unknown=tokens_unknown,
        n_llm_calls=attempts,
        top_level_exception="",
        attempt_observations=observations or [],
    )


# ---------------------------------------------------------------------------
# Corpus selection rule
# ---------------------------------------------------------------------------


def _dev_entry(qid: int, db: str, diff: str, sql: str = "SELECT 1") -> dict:
    return {"question_id": qid, "db_id": db, "difficulty": diff, "SQL": sql}


def test_select_filters_dialect_and_calibration_and_is_deterministic():
    entries = (
        [_dev_entry(i, "db", "simple") for i in range(100)]
        + [_dev_entry(i, "db", "moderate") for i in range(100, 150)]
        + [_dev_entry(i, "db", "challenging") for i in range(150, 170)]
        # dialect gotchas — must never be drawn
        + [_dev_entry(900 + i, "db", "simple", "SELECT STRFTIME('%Y', d)") for i in range(3)]
        + [_dev_entry(910, "db", "simple", "SELECT IIF(a, b, c)")]
    )
    picks = select_phase1_case_ids(entries)
    assert len(picks) == 20
    assert picks == select_phase1_case_ids(entries)  # deterministic
    drawn_qids = {int(p.split("_")[1]) for p in picks}
    assert not drawn_qids & {900, 901, 902, 910}
    assert not drawn_qids & CALIBRATION_QUESTION_IDS


def test_frozen_list_matches_rule_on_real_bird_data():
    # conftest chdirs each test into tmp — resolve the repo root explicitly.
    bird_root = Path(__file__).resolve().parents[2] / "data" / "bird"
    dev_json = bird_root / "dev.json"
    if not dev_json.exists():
        pytest.skip("BIRD dev.json not installed")
    entries = json.loads(dev_json.read_text(encoding="utf-8"))

    def has_db(db_id: str) -> bool:
        return (bird_root / "dev_databases" / db_id / f"{db_id}.sqlite").exists()

    assert select_phase1_case_ids(entries, has_db=has_db) == list(PHASE1_CASE_IDS)


# ---------------------------------------------------------------------------
# Record round-trip + leg-granular resume
# ---------------------------------------------------------------------------


def test_record_json_round_trip():
    rec = _record(observations=[AttemptObservation(
        attempt=1, exit_code=0, eval_passed=False, eval_score=0.4,
        comparator_correct=True, stdout_head="42",
    )])
    back = Phase1Record.from_json(rec.to_json())
    assert back == rec


def test_resume_drops_partial_legs_keeps_complete(tmp_path: Path):
    records_path = tmp_path / "records.jsonl"
    complete = [_record(case_id=f"c{i}", mode="governor", seed=0) for i in range(3)]
    partial = [_record(case_id="c0", mode="naive", seed=0)]
    records_path.write_text(
        "".join(r.to_json() + "\n" for r in complete + partial), encoding="utf-8",
    )
    driver = PhaseOneDriver(n_seeds=1, records_path=records_path)
    kept = driver._load_resumable(n_cases=3)
    assert {(r.mode, r.seed) for r in kept} == {("governor", 0)}
    # the file itself was rewritten without the partial leg
    assert {(r.mode, r.seed) for r in load_records(records_path)} == {("governor", 0)}


# ---------------------------------------------------------------------------
# Tier A metric formulas (PHASE0_METRICS v4 §3)
# ---------------------------------------------------------------------------


def test_recovery_rate_denominator_is_first_try_failures():
    leg = [
        _record(case_id="a", passed=True, attempts=1),   # first-try — excluded
        _record(case_id="b", passed=True, attempts=2),   # recovered
        _record(case_id="c", passed=False, attempts=3),  # failed
    ]
    assert recovery_rate(leg) == pytest.approx(0.5)


def test_recovery_rate_none_when_all_first_try():
    leg = [_record(case_id="a"), _record(case_id="b")]
    assert recovery_rate(leg) is None


def test_mean_attempts_on_unsolved_none_when_all_solved():
    assert mean_attempts_on_unsolved([_record()]) is None
    leg = [_record(passed=False, attempts=4), _record(passed=False, attempts=2)]
    assert mean_attempts_on_unsolved(leg) == pytest.approx(3.0)


def test_tokens_per_solved_excludes_unknown_not_zeroes():
    leg = [
        _record(case_id="a", tokens=100),                        # 200 total
        _record(case_id="b", tokens=999, tokens_unknown=True),   # excluded
        _record(case_id="c", passed=False, tokens=500),          # unsolved — excluded
    ]
    assert tokens_per_solved(leg) == pytest.approx(200.0)


def test_paired_deltas_skip_seeds_where_either_arm_is_na():
    records = [
        # seed 0: both arms defined (governor recovers 1/1, naive 0/1)
        _record(case_id="a", mode="governor", seed=0, passed=True, attempts=2),
        _record(case_id="a", mode="naive", seed=0, passed=False, attempts=3),
        # seed 1: governor all first-try -> recovery_rate N/A -> seed dropped
        _record(case_id="a", mode="governor", seed=1, passed=True, attempts=1),
        _record(case_id="a", mode="naive", seed=1, passed=False, attempts=3),
    ]
    deltas, used = paired_deltas(records, recovery_rate, n_seeds=2)
    assert used == 1
    assert deltas == [pytest.approx(1.0)]


def test_success_rate_paired_delta_sign():
    records = []
    for seed in range(3):
        records.append(_record(case_id="a", mode="governor", seed=seed, passed=True))
        records.append(_record(case_id="a", mode="naive", seed=seed, passed=False))
    deltas, used = paired_deltas(records, success_rate, n_seeds=3)
    assert used == 3
    assert all(d == pytest.approx(1.0) for d in deltas)


# ---------------------------------------------------------------------------
# Sensitivity appendix (v4 §4)
# ---------------------------------------------------------------------------


def _obs(*, correct: bool, eval_passed: bool) -> AttemptObservation:
    return AttemptObservation(
        attempt=1, exit_code=0, eval_passed=eval_passed, eval_score=0.0,
        comparator_correct=correct, stdout_head="",
    )


def test_fn_rates_count_comparator_correct_evaluator_rejected():
    leg = [_record(observations=[
        _obs(correct=True, eval_passed=False),   # the false negative
        _obs(correct=True, eval_passed=True),
        _obs(correct=False, eval_passed=False),  # true negative — not FN
        _obs(correct=False, eval_passed=True),   # false positive — not FN
    ])]
    assert fn_rate_all_attempts(leg) == pytest.approx(0.25)
    assert fn_rate_correct_attempts(leg) == pytest.approx(0.5)


def test_l6_run_count_is_comparator_pass_runtime_failed():
    leg = [
        _record(passed=True, runtime_outcome="FAILED"),
        _record(passed=True, runtime_outcome="SUCCESS"),
        _record(passed=False, runtime_outcome="FAILED"),
    ]
    assert l6_run_count(leg) == 1


# ---------------------------------------------------------------------------
# Report wording — the significance rule is load-bearing
# ---------------------------------------------------------------------------


def test_report_wording_significant_vs_null(tmp_path: Path):
    records = []
    for seed in range(5):
        for i in range(4):
            # success: governor always passes, naive always fails -> significant
            records.append(_record(
                case_id=f"c{i}", mode="governor", seed=seed, passed=True, attempts=2,
            ))
            records.append(_record(
                case_id=f"c{i}", mode="naive", seed=seed, passed=False, attempts=2,
            ))
    out = tmp_path / "report.md"
    write_report(records, out_path=out, n_seeds=5)
    text = out.read_text(encoding="utf-8")
    assert "**significant**" in text                      # success_rate row
    assert "no significant effect (CI includes 0)" in text  # attempts row (0 delta)
    assert "Appendix D" in text
    assert "field-of-record" in text.lower() or "comparator" in text
