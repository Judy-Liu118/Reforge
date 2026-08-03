"""Tests for FailureFingerprint — structured failure signature extraction.

P-FP: Validates that extract_fingerprint() correctly parses Python tracebacks
into structured fields, and that the improved scoring surfaces more precise
memory matches than keyword guessing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from reforge.memory.fingerprint import extract_fingerprint
from reforge.memory.models import MemoryRecord
from reforge.memory.retrieval import MemoryRetriever
from reforge.memory.store import MemoryStore


# ---------------------------------------------------------------------------
# Traceback fixtures
# ---------------------------------------------------------------------------

_TB_IMPORT = """\
Traceback (most recent call last):
  File "main.py", line 1, in <module>
    import pandas as pd
ModuleNotFoundError: No module named 'pandas'
"""

_TB_KEY = """\
Traceback (most recent call last):
  File "main.py", line 5, in <module>
    print(df['sales'])
  File ".../pandas/core/frame.py", line 3024, in __getitem__
    indexer = self.columns.get_loc(key)
KeyError: 'sales'
"""

_TB_FILE = """\
Traceback (most recent call last):
  File "main.py", line 3, in <module>
    df = pd.read_csv('missing_data.csv')
  File ".../pandas/io/parsers.py", line 614, in read_csv
    return _read(filepath_or_buffer, kwds)
FileNotFoundError: [Errno 2] No such file or directory: 'missing_data.csv'
"""

_TB_NAME = """\
Traceback (most recent call last):
  File "main.py", line 4, in <module>
    print(df.head())
NameError: name 'df' is not defined
"""

_TB_SYNTAX = """\
  File "main.py", line 3
    if True
          ^
SyntaxError: invalid syntax
"""

_TB_ZERODIV = """\
Traceback (most recent call last):
  File "main.py", line 2, in <module>
    x = 1 / 0
ZeroDivisionError: division by zero
"""

_TB_IMPORT_NESTED = """\
Traceback (most recent call last):
  File "script.py", line 1
    from pandas.core import frame
ImportError: cannot import name 'frame' from 'pandas.core'
"""

_TB_INDENT = """\
  File "main.py", line 3
    return x
    ^
IndentationError: unexpected indent
"""

_TB_PERMISSION = """\
Traceback (most recent call last):
  File "main.py", line 2, in <module>
    open('/etc/shadow').read()
PermissionError: [Errno 13] Permission denied: '/etc/shadow'
"""

_TB_JSON = """\
Traceback (most recent call last):
  File "main.py", line 4, in <module>
    json.loads(s)
json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
"""

_TB_UNICODE = """\
Traceback (most recent call last):
  File "main.py", line 2, in <module>
    open('data.csv').read()
UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 0: invalid start byte
"""

_TB_ASSERTION = """\
Traceback (most recent call last):
  File "main.py", line 5, in <module>
    assert total == expected
AssertionError: totals diverged
"""

_TB_RECURSION = """\
Traceback (most recent call last):
  File "main.py", line 3, in f
    return f(n + 1)
RecursionError: maximum recursion depth exceeded
"""


# ---------------------------------------------------------------------------
# Class TestFingerprintExtraction
# ---------------------------------------------------------------------------

class TestFingerprintExtraction:

    def test_import_error_missing_module(self) -> None:
        fp = extract_fingerprint(_TB_IMPORT)
        assert fp.error_class == "ModuleNotFoundError"
        assert fp.missing_module == "pandas"
        assert fp.execution_phase == "import"
        assert fp.domain == "pandas"

    def test_key_error_missing_key(self) -> None:
        fp = extract_fingerprint(_TB_KEY)
        assert fp.error_class == "KeyError"
        assert fp.missing_key == "sales"
        assert fp.domain == "pandas"  # traceback mentions df / pandas

    def test_file_not_found(self) -> None:
        fp = extract_fingerprint(_TB_FILE)
        assert fp.error_class == "FileNotFoundError"
        assert fp.missing_file == "missing_data.csv"
        assert fp.execution_phase == "runtime"

    def test_name_error(self) -> None:
        fp = extract_fingerprint(_TB_NAME)
        assert fp.error_class == "NameError"
        assert fp.undefined_name == "df"

    def test_syntax_error_phase(self) -> None:
        fp = extract_fingerprint(_TB_SYNTAX)
        assert fp.error_class == "SyntaxError"
        assert fp.execution_phase == "syntax"

    def test_zero_division(self) -> None:
        fp = extract_fingerprint(_TB_ZERODIV)
        assert fp.error_class == "ZeroDivisionError"
        assert fp.execution_phase == "runtime"
        assert fp.missing_module == ""  # no module error

    def test_import_from_nested_module(self) -> None:
        fp = extract_fingerprint(_TB_IMPORT_NESTED)
        assert fp.error_class == "ImportError"
        assert fp.missing_module == "pandas"  # top-level package extracted

    def test_empty_traceback_falls_back_to_error_type(self) -> None:
        fp = extract_fingerprint("", error_type="KeyError")
        assert fp.error_class == "KeyError"

    def test_empty_traceback_empty_error_type(self) -> None:
        fp = extract_fingerprint("", error_type="")
        assert fp.error_class == ""
        assert fp.to_dict()["domain"] == ""  # graceful, not raises

    def test_to_dict_has_derived_keys(self) -> None:
        fp = extract_fingerprint(_TB_IMPORT)
        d = fp.to_dict()
        assert "root_cause" in d
        assert "domain" in d
        assert d["missing_module"] == "pandas"
        assert d["root_cause"] == "missing_import"

    def test_to_dict_omits_error_type_alias(self) -> None:
        """error_type was an alias of error_class; emitting both made every
        scorer that weighted the two fields double-count one signal."""
        d = extract_fingerprint(_TB_IMPORT).to_dict()
        assert "error_type" not in d
        assert d["error_class"] == "ModuleNotFoundError"

    def test_to_dict_key_error(self) -> None:
        fp = extract_fingerprint(_TB_KEY)
        d = fp.to_dict()
        assert d["missing_key"] == "sales"
        assert d["root_cause"] == "missing_key"


# ---------------------------------------------------------------------------
# Class TestNewErrorTypeCoverage — regression for #7 (fingerprint gaps)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "traceback, expected_class, expected_phase, expected_root_cause",
    [
        (_TB_INDENT,     "IndentationError",   "syntax",  "indentation_error"),
        (_TB_PERMISSION, "PermissionError",    "runtime", "permission_denied"),
        (_TB_JSON,       "JSONDecodeError",    "runtime", "invalid_json"),
        (_TB_UNICODE,    "UnicodeDecodeError", "runtime", "encoding_error"),
        (_TB_ASSERTION,  "AssertionError",     "runtime", "assertion_failed"),
        (_TB_RECURSION,  "RecursionError",     "runtime", "recursion_limit"),
    ],
)
def test_extended_error_types_classified(
    traceback: str,
    expected_class: str,
    expected_phase: str,
    expected_root_cause: str,
) -> None:
    fp = extract_fingerprint(traceback)
    assert fp.error_class == expected_class
    assert fp.execution_phase == expected_phase
    assert fp.to_dict()["root_cause"] == expected_root_cause


def test_permission_error_keeps_filesystem_domain() -> None:
    fp = extract_fingerprint(_TB_PERMISSION)
    assert fp.domain == "filesystem"  # _DOMAIN_PATTERNS already catches it


# ---------------------------------------------------------------------------
# Class TestMemoryRecordFingerprint
# ---------------------------------------------------------------------------

class TestMemoryRecordFingerprint:

    def test_from_session_with_traceback_populates_fingerprint(self) -> None:
        rec = MemoryRecord.from_session(
            session_id="s1",
            user_request="analyze csv data",
            outcome="FAILED",
            retry_count=1,
            error_type="",
            traceback=_TB_KEY,
        )
        sig = rec.problem_signature
        assert sig.get("error_class") == "KeyError"
        assert sig.get("missing_key") == "sales"
        assert sig.get("domain") == "pandas"
        # error_type back-filled from fingerprint
        assert rec.error_type == "KeyError"

    def test_from_session_without_traceback_uses_error_type(self) -> None:
        rec = MemoryRecord.from_session(
            session_id="s2",
            user_request="compute mean",
            outcome="FAILED",
            retry_count=0,
            error_type="ZeroDivisionError",
        )
        sig = rec.problem_signature
        assert sig.get("error_class") == "ZeroDivisionError"
        assert sig.get("root_cause") == "division_by_zero"

    def test_from_session_missing_module_fills_error_type(self) -> None:
        rec = MemoryRecord.from_session(
            session_id="s3",
            user_request="read data",
            outcome="FAILED",
            retry_count=0,
            error_type="",
            traceback=_TB_IMPORT,
        )
        assert rec.error_type == "ModuleNotFoundError"
        assert rec.problem_signature["missing_module"] == "pandas"


# ---------------------------------------------------------------------------
# Class TestRetrievalScoringWithFingerprint
# ---------------------------------------------------------------------------

class TestRetrievalScoringWithFingerprint:

    def _store_with_records(self, tmp_path: Path) -> tuple[MemoryStore, MemoryRetriever]:
        store = MemoryStore(base_dir=tmp_path)
        # Record 1: KeyError on 'sales' column — pandas domain
        r1 = MemoryRecord.from_session(
            session_id="s1", user_request="compute average revenue",
            outcome="RECOVERED", retry_count=1,
            traceback=_TB_KEY,
            recovery_action="rename column to 'revenue'",
        )
        # Record 2: ImportError for pandas
        r2 = MemoryRecord.from_session(
            session_id="s2", user_request="analyze csv",
            outcome="FAILED", retry_count=1,
            traceback=_TB_IMPORT,
        )
        # Record 3: unrelated ZeroDivision
        r3 = MemoryRecord.from_session(
            session_id="s3", user_request="compute ratio",
            outcome="FAILED", retry_count=0,
            traceback=_TB_ZERODIV,
        )
        for r in (r1, r2, r3):
            store.save(r)
        retriever = MemoryRetriever(store)
        return store, retriever

    def test_keyerror_query_finds_keyerror_record_first(self, tmp_path: Path) -> None:
        _, retriever = self._store_with_records(tmp_path)
        results = retriever.search("KeyError: 'sales'", limit=3)
        assert results, "Expected at least one result"
        assert results[0].problem_signature.get("error_class") == "KeyError"

    def test_missing_module_query_surfaces_import_record(self, tmp_path: Path) -> None:
        _, retriever = self._store_with_records(tmp_path)
        results = retriever.search("ModuleNotFoundError: No module named 'pandas'", limit=3)
        assert results, "Expected at least one result"
        assert results[0].problem_signature.get("missing_module") == "pandas"

    def test_unrelated_query_does_not_surface_unrelated_record(self, tmp_path: Path) -> None:
        _, retriever = self._store_with_records(tmp_path)
        results = retriever.search("KeyError: 'sales'", limit=1)
        assert not any(
            r.problem_signature.get("error_class") == "ZeroDivisionError"
            for r in results
        ), "ZeroDivisionError record should not appear in top-1 for KeyError query"

    def test_find_by_error_type_uses_structured_field(self, tmp_path: Path) -> None:
        _, retriever = self._store_with_records(tmp_path)
        results = retriever.find_by_error_type("KeyError")
        assert any(r.error_type == "KeyError" for r in results)


# ---------------------------------------------------------------------------
# Class TestExecutionMemoryScoringWithFingerprint
# ---------------------------------------------------------------------------

class TestExecutionMemoryScoringWithFingerprint:

    def test_record_with_traceback_builds_fingerprint(self, tmp_path: Path) -> None:
        from reforge.memory.execution_memory import ExecutionMemory, ExecutionRecord

        mem = ExecutionMemory(path=tmp_path / "exec.jsonl")
        mem.record(
            request="analyze csv",
            outcome="FAILED",
            failure_mode="execution_error",
            error_type="",
            traceback=_TB_KEY,
        )
        records = [
            ExecutionRecord.model_validate_json(ln)
            for ln in (tmp_path / "exec.jsonl").read_text().splitlines()
            if ln.strip()
        ]
        assert len(records) == 1
        sig = records[0].problem_signature
        assert sig.get("error_class") == "KeyError"
        assert sig.get("missing_key") == "sales"

    def test_structured_sig_improves_recall_precision(self, tmp_path: Path) -> None:
        from reforge.memory.execution_memory import ExecutionMemory

        mem = ExecutionMemory(path=tmp_path / "exec.jsonl")
        mem.record(
            request="analyze csv", outcome="RECOVERED",
            failure_mode="execution_error", error_type="KeyError",
            traceback=_TB_KEY, repair_strategy="rename the column",
        )
        mem.record(
            request="ratio computation", outcome="FAILED",
            failure_mode="execution_error", error_type="ZeroDivisionError",
            traceback=_TB_ZERODIV,
        )
        results = mem.recall_similar(
            request="read csv",
            failure_mode="execution_error",
            problem_signature={"error_class": "KeyError", "missing_key": "sales"},
        )
        assert results, "Should find at least one record"
        assert results[0].problem_signature.get("error_class") == "KeyError"


# ---------------------------------------------------------------------------
# Script-location independence — guards the DockerBackend stdin decision.
# ---------------------------------------------------------------------------

_TB_PANDAS_FROM_FILE = """\
Traceback (most recent call last):
  File "/work/_script.py", line 4, in <module>
    print(df["revenue"].sum())
  File "/usr/lib/python3.11/site-packages/pandas/core/frame.py", line 4102, in __getitem__
    indexer = self.columns.get_loc(key)
  File "/usr/lib/python3.11/site-packages/pandas/core/indexes/base.py", line 3812, in get_loc
    raise KeyError(key) from err
KeyError: 'revenue'
"""

_TB_PANDAS_FROM_STDIN = """\
Traceback (most recent call last):
  File "<stdin>", line 4, in <module>
    print(df["revenue"].sum())
  File "/usr/lib/python3.11/site-packages/pandas/core/frame.py", line 4102, in __getitem__
    indexer = self.columns.get_loc(key)
  File "/usr/lib/python3.11/site-packages/pandas/core/indexes/base.py", line 3812, in get_loc
    raise KeyError(key) from err
KeyError: 'revenue'
"""


class TestFingerprintIgnoresScriptLocation:
    """DockerBackend pipes the program to `python -`, so the user frame reads
    `File "<stdin>"` instead of a path under the workspace. That change rested on
    a reading of this parser — that no fingerprint field derives from the user
    frame. These tests assert it instead of trusting the reading.

    Deliberately not run through a real container: python:3.11-slim ships no
    pandas, and the file-based mode no longer exists to compare against. Two
    tracebacks differing only in the user frame isolate exactly the claim.
    """

    def test_same_fingerprint_regardless_of_user_frame(self) -> None:
        from_file = extract_fingerprint(_TB_PANDAS_FROM_FILE, error_type="KeyError")
        from_stdin = extract_fingerprint(_TB_PANDAS_FROM_STDIN, error_type="KeyError")
        assert from_file.to_dict() == from_stdin.to_dict()

    def test_fingerprint_is_not_vacuously_equal(self) -> None:
        """Equality would also hold if both sides parsed to nothing — pin the values.

        `domain` is the field worth watching: it is inferred by scanning the whole
        traceback, so it is the one place the user frame could have leaked in. It
        resolves to "pandas" off the site-packages frames, which stdin leaves alone.
        """
        d = extract_fingerprint(_TB_PANDAS_FROM_STDIN, error_type="KeyError").to_dict()
        assert d["error_class"] == "KeyError"
        assert d["missing_key"] == "revenue"
        assert d["root_cause"] == "missing_key"
        assert d["execution_phase"] == "runtime"
        assert d["domain"] == "pandas"
