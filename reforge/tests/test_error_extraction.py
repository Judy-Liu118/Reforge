"""Tests for the shared traceback → error-type extractor.

Covers the contract used by execution_node and reflection_node so the two
stay in sync. See reforge/runtime/infrastructure/error_extraction.py.
"""

from __future__ import annotations

from reforge.runtime.infrastructure.error_extraction import extract_error_type


class TestExtractErrorType:
    def test_empty_returns_default(self) -> None:
        assert extract_error_type("") == ""

    def test_empty_with_custom_default(self) -> None:
        assert extract_error_type("", default="UnknownError") == "UnknownError"

    def test_unmatched_returns_default(self) -> None:
        # No Error/Warning/Exception token in the traceback
        assert extract_error_type("some noise\nno fatal token") == ""
        assert extract_error_type("noise", default="X") == "X"

    def test_bare_value_error(self) -> None:
        tb = (
            'Traceback (most recent call last):\n'
            '  File "x.py", line 1, in <module>\n'
            '    raise ValueError("bad")\n'
            'ValueError: bad'
        )
        assert extract_error_type(tb) == "ValueError"

    def test_dotted_exception_name(self) -> None:
        tb = (
            'Traceback (most recent call last):\n'
            '  File "x.py", line 1, in <module>\n'
            '    df = pd.read_csv("missing")\n'
            'pandas.errors.ParserError: malformed CSV'
        )
        assert extract_error_type(tb) == "pandas.errors.ParserError"

    def test_warning_suffix(self) -> None:
        tb = "DeprecationWarning: feature removed"
        assert extract_error_type(tb) == "DeprecationWarning"

    def test_exception_suffix(self) -> None:
        # Builtin BaseException is matched via the "Exception" sep
        tb = "Exception: generic"
        assert extract_error_type(tb) == "Exception"

    def test_first_match_wins_per_line_scan(self) -> None:
        # The first line containing a recognised suffix is returned.
        tb = (
            'Traceback (most recent call last):\n'
            'KeyError: profit\n'
            'ValueError: should not be returned'
        )
        assert extract_error_type(tb) == "KeyError"

    def test_strips_leading_whitespace(self) -> None:
        tb = "    RuntimeError: boom"
        assert extract_error_type(tb) == "RuntimeError"

    def test_traceback_only_no_final_line(self) -> None:
        # Header without an Error/Warning/Exception keyword anywhere
        tb = 'Traceback (most recent call last):\n  File "x.py", line 1'
        assert extract_error_type(tb, default="UnknownError") == "UnknownError"
