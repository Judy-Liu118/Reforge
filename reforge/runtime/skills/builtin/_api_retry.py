"""Shared retry-with-backoff for vision API calls.

GLM, Qwen, and OpenAI all surface transient `APIConnectionError` /
`APITimeoutError` / `ReadTimeout` from time to time — anywhere from a
single hiccup to a 30-second outage. Without retries, a single transient
error fails an entire reforge attempt and burns a codegen retry on the
runtime side. With three exponential-backoff retries the failure rate
of the visual self-heal loop drops sharply.

Retried errors are *transport / availability* failures. HTTP 4xx (bad
auth, invalid input, model not found) are NOT retried — those won't fix
themselves and retrying just slows the failure down.
"""

from __future__ import annotations

import time
from typing import Callable, TypeVar

T = TypeVar("T")

_DEFAULT_MAX_ATTEMPTS = 3
_DEFAULT_BACKOFF_S = 1.0


def _is_retryable(exc: BaseException) -> bool:
    """True for transient transport / availability errors.

    We match by exception class name so this module stays decoupled from
    the openai SDK shape, which can change across versions.
    """
    name = type(exc).__name__
    if name in {
        "APIConnectionError",
        "APITimeoutError",
        "ConnectionError",
        "ReadTimeout",
        "Timeout",
        "RemoteProtocolError",
    }:
        return True
    # 5xx server errors are also transient
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and 500 <= status < 600:
        return True
    return False


def call_with_retry(
    fn: Callable[[], T],
    *,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    backoff_s: float = _DEFAULT_BACKOFF_S,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run `fn`, retrying transient failures with exponential backoff.

    Backoff is doubled each attempt: 1s, 2s, 4s. Non-retryable errors
    raise immediately so misconfiguration doesn't waste user time.
    """
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except BaseException as exc:  # noqa: BLE001 — intentional broad catch
            last_exc = exc
            if not _is_retryable(exc) or attempt == max_attempts:
                raise
            sleep(backoff_s * (2 ** (attempt - 1)))
    # Unreachable: loop either returns or raises
    assert last_exc is not None
    raise last_exc
