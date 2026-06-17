"""Tests for the vision API retry-with-backoff helper.

Transient APIConnectionError / APITimeoutError / 5xx surface from GLM and
qwen-vl with some regularity; without retries a single hiccup wastes a
reforge codegen retry.
"""

from __future__ import annotations

import pytest

from reforge.runtime.skills.builtin._api_retry import call_with_retry, _is_retryable


# ---------------------------------------------------------------------------
# Retryable classification
# ---------------------------------------------------------------------------


class APIConnectionError(Exception):
    """Stand-in matching the openai SDK name."""


class APITimeoutError(Exception):
    pass


class ReadTimeout(Exception):
    pass


class _ServerError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class _BadRequest(Exception):
    def __init__(self) -> None:
        super().__init__("HTTP 400")
        self.status_code = 400


class TestIsRetryable:
    def test_api_connection_error_retryable(self) -> None:
        assert _is_retryable(APIConnectionError("boom"))

    def test_api_timeout_retryable(self) -> None:
        assert _is_retryable(APITimeoutError("slow"))

    def test_read_timeout_retryable(self) -> None:
        assert _is_retryable(ReadTimeout("slow"))

    def test_5xx_retryable(self) -> None:
        assert _is_retryable(_ServerError(503))
        assert _is_retryable(_ServerError(500))
        assert _is_retryable(_ServerError(599))

    def test_4xx_not_retryable(self) -> None:
        """Bad auth / bad input won't fix itself — retrying just delays the failure."""
        assert not _is_retryable(_BadRequest())

    def test_value_error_not_retryable(self) -> None:
        assert not _is_retryable(ValueError("bad arg"))

    def test_runtime_error_not_retryable(self) -> None:
        assert not _is_retryable(RuntimeError("generic"))


# ---------------------------------------------------------------------------
# call_with_retry behaviour
# ---------------------------------------------------------------------------


class TestCallWithRetry:
    def test_returns_immediately_on_success(self) -> None:
        calls = {"n": 0}
        def fn():
            calls["n"] += 1
            return "ok"
        assert call_with_retry(fn) == "ok"
        assert calls["n"] == 1

    def test_retries_transient_then_succeeds(self) -> None:
        calls = {"n": 0}
        sleeps: list[float] = []
        def fn():
            calls["n"] += 1
            if calls["n"] < 3:
                raise APIConnectionError("transient")
            return "recovered"
        result = call_with_retry(fn, sleep=sleeps.append)
        assert result == "recovered"
        assert calls["n"] == 3
        # Exponential backoff: 1s, 2s before the third try
        assert sleeps == [1.0, 2.0]

    def test_raises_after_max_attempts(self) -> None:
        calls = {"n": 0}
        sleeps: list[float] = []
        def fn():
            calls["n"] += 1
            raise APIConnectionError("always down")
        with pytest.raises(APIConnectionError):
            call_with_retry(fn, sleep=sleeps.append)
        assert calls["n"] == 3
        # Only sleeps between attempts, not after the final failure
        assert sleeps == [1.0, 2.0]

    def test_non_retryable_raises_immediately(self) -> None:
        calls = {"n": 0}
        sleeps: list[float] = []
        def fn():
            calls["n"] += 1
            raise _BadRequest()
        with pytest.raises(_BadRequest):
            call_with_retry(fn, sleep=sleeps.append)
        assert calls["n"] == 1
        assert sleeps == []  # No sleep on first non-retryable failure

    def test_custom_max_attempts(self) -> None:
        calls = {"n": 0}
        def fn():
            calls["n"] += 1
            raise APITimeoutError("slow")
        with pytest.raises(APITimeoutError):
            call_with_retry(fn, max_attempts=5, sleep=lambda _: None)
        assert calls["n"] == 5

    def test_5xx_triggers_retry(self) -> None:
        calls = {"n": 0}
        def fn():
            calls["n"] += 1
            if calls["n"] < 2:
                raise _ServerError(503)
            return "ok"
        assert call_with_retry(fn, sleep=lambda _: None) == "ok"
        assert calls["n"] == 2

    def test_4xx_does_not_trigger_retry(self) -> None:
        calls = {"n": 0}
        def fn():
            calls["n"] += 1
            raise _BadRequest()
        with pytest.raises(_BadRequest):
            call_with_retry(fn, sleep=lambda _: None)
        assert calls["n"] == 1
