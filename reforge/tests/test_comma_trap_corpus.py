"""Contract tests for the comma-trap corpus, its oracle, and the contract probe.

These freeze the properties the bench's conclusions rest on. A corpus whose
trap silently stops firing, or an oracle that drifts from the data it grades,
produces a number that looks like a measurement and is not one — the failure
mode `docs/eval/MEMORY_TRANSFER_PROBE_PILOT.md` was written about.
"""

from __future__ import annotations

from reforge.benchmark.comma_trap_cases import (
    CASES,
    GRADE_TOLERANCE,
    SPECS,
    gold_value,
    grade_answer,
    task_rows,
)
from reforge.benchmark.memory_probe_cases import PAIRED_CASES_MP
from reforge.benchmark.memory_probe_contract import (
    CONTRACT_CLAUSE,
    PAIRED_CASES_MPC,
)


# ---------------------------------------------------------------------------
# Corpus shape


def test_every_task_carries_all_three_trap_layers():
    for spec in SPECS:
        rows = task_rows(spec)
        data, footer = rows[:-1], rows[-1]
        european = [raw for _, raw, _ in data if raw.rfind(",") > raw.rfind(".")]
        parens = [raw for _, raw, _ in data if raw.startswith("(")]
        assert len(european) == 3, f"{spec.task_id}: expected 3 European rows"
        assert len(parens) == 2, f"{spec.task_id}: expected 2 parenthesised negatives"
        assert footer[0] == "TOTAL"
        # Every data cell needs a thousands separator, so the trap fires on the
        # first row read rather than depending on which rows get inspected.
        assert all("," in raw or "." in raw for _, raw, _ in data)


def test_gold_excludes_footer_and_honours_signs():
    for spec in SPECS:
        rows = task_rows(spec)
        assert gold_value(spec) == round(sum(v for _, _, v in rows[:-1]), 2)
        assert rows[-1][2] == 0.0, "footer must contribute nothing to gold"
        assert any(v < 0 for _, _, v in rows), f"{spec.task_id}: no negative rows"


def test_footer_decoy_is_far_from_gold():
    """Summing the footer in must miss by orders of magnitude beyond tolerance."""
    for spec in SPECS:
        rows = task_rows(spec)
        footer = float(rows[-1][1].replace(",", ""))
        assert abs(footer) > GRADE_TOLERANCE * 100
        assert abs(gold_value(spec) + footer - gold_value(spec)) > GRADE_TOLERANCE


def test_rows_are_deterministic():
    assert task_rows(SPECS[0]) == task_rows(SPECS[0])
    assert task_rows(SPECS[0]) != task_rows(SPECS[1])


def test_cases_cover_specs_and_never_mention_the_trap():
    assert len(CASES) == len(SPECS)
    for case in CASES:
        # The request may name the file and column; naming the separators,
        # the European rows or the footer would hand over the answer.
        for leak in ("千分位", "逗号", "欧式", "TOTAL", "汇总行"):
            assert leak not in case.request, f"{case.id} leaks the trap: {leak}"


# ---------------------------------------------------------------------------
# Oracle


def test_grade_accepts_correct_formatting_variants():
    gold = 391204.53
    for answer in ("391204.53", "391,204.53", "the sum is 391204.5", "391204"):
        passed, _ = grade_answer(answer, gold)
        assert passed, answer


def test_grade_rejects_the_corpus_failure_modes():
    gold = 503048.01
    footer_summed = "891905.19889"      # footer in, European rows mangled
    rows_dropped = "286383.18"          # unparseable rows skipped
    masked = "0.0"                      # coerced everything to NaN
    for answer in (footer_summed, rows_dropped, masked, ""):
        passed, _ = grade_answer(answer, gold)
        assert not passed, answer


def test_grade_returns_parsed_values_for_forensics():
    passed, values = grade_answer("total=1,234.00 rows=12", 1234.0)
    assert passed
    assert 1234.0 in values


# ---------------------------------------------------------------------------
# Contract-augmented probe


def test_contract_variant_appends_without_touching_the_original():
    assert len(PAIRED_CASES_MPC) == len(PAIRED_CASES_MP)
    for original, derived in zip(PAIRED_CASES_MP, PAIRED_CASES_MPC):
        assert derived.seed.request == original.seed.request + CONTRACT_CLAUSE
        assert derived.transfer.request == original.transfer.request + CONTRACT_CLAUSE
        # Gold answers and the fingerprint axis are what make the pair a pair.
        assert derived.seed.expected_keywords == original.seed.expected_keywords
        assert derived.fingerprint_axis == original.fingerprint_axis
        assert derived.pair_id != original.pair_id


def test_contract_forbids_masking_without_revealing_the_legend():
    for leak in ("legend", "manifest", "_legend", "编码", "c_0001", "t_a1"):
        assert leak not in CONTRACT_CLAUSE, f"contract leaks {leak}"
    assert "ROWS=" in CONTRACT_CLAUSE
    assert "非零退出码" in CONTRACT_CLAUSE
