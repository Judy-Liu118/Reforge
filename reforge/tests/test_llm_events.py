"""Tests for reforge.observability.llm_events.

Covers:
  - set_hook / _emit round-trip
  - hook exception isolation
  - token_accounting: accumulation, scope exit, nested scopes,
    -1 sentinel handling, non-completion events ignored, thread-level
    ContextVar isolation, exception-safe scope reset.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

import reforge.observability.llm_events as _events
from reforge.observability.llm_events import (
    TokenLedgerEntry,
    _emit,
    set_hook,
    token_accounting,
)


# ---------------------------------------------------------------------------
# Hook
# ---------------------------------------------------------------------------


def test_set_hook_registers_and_clears():
    original = _events._hook
    try:
        fn = lambda et, pl: None  # noqa: E731 — terse for test
        set_hook(fn)
        assert _events._hook is fn
        set_hook(None)
        assert _events._hook is None
    finally:
        _events._hook = original


def test_emit_invokes_hook():
    events: list[tuple[str, dict]] = []
    set_hook(lambda et, pl: events.append((et, pl)))
    try:
        _emit("llm_call_complete", {"prompt_tokens": 10, "completion_tokens": 5})
    finally:
        set_hook(None)
    assert events == [
        ("llm_call_complete", {"prompt_tokens": 10, "completion_tokens": 5})
    ]


def test_hook_exception_does_not_propagate():
    def bad(*a, **k):
        raise RuntimeError("boom")

    set_hook(bad)
    try:
        # Must not raise — observability never breaks the call path.
        _emit("llm_call_complete", {"prompt_tokens": 10, "completion_tokens": 5})
    finally:
        set_hook(None)


# ---------------------------------------------------------------------------
# Token accounting
# ---------------------------------------------------------------------------


class TestTokenAccountingScope:
    def test_no_active_scope_is_silent_noop(self):
        # Just must not crash; no observable side effects.
        _emit("llm_call_complete", {"prompt_tokens": 10, "completion_tokens": 5})

    def test_accumulates_within_scope(self):
        with token_accounting("case1", 0) as ledger:
            _emit("llm_call_complete", {"prompt_tokens": 10, "completion_tokens": 5})
            _emit("llm_call_complete", {"prompt_tokens": 20, "completion_tokens": 7})
        assert ledger.case_id == "case1"
        assert ledger.seed == 0
        assert ledger.prompt_tokens == 30
        assert ledger.completion_tokens == 12
        assert ledger.calls == 2
        assert ledger.unknown is False
        assert ledger.total_tokens == 42

    def test_emit_after_exit_does_not_mutate_ledger(self):
        with token_accounting("case1", 0) as ledger:
            _emit("llm_call_complete", {"prompt_tokens": 10, "completion_tokens": 5})
        # Outside the scope — ledger must be frozen-in-time.
        _emit("llm_call_complete", {"prompt_tokens": 999, "completion_tokens": 999})
        assert ledger.prompt_tokens == 10
        assert ledger.completion_tokens == 5
        assert ledger.calls == 1

    def test_ignores_non_completion_events(self):
        with token_accounting("case1", 0) as ledger:
            _emit("llm_call_start", {"prompt_chars": 100})
            _emit("llm_call_retry", {"attempt": 1, "error": "Timeout"})
            _emit("llm_call_error", {"error": "exhausted"})
        assert ledger.calls == 0
        assert ledger.prompt_tokens == 0
        assert ledger.completion_tokens == 0
        assert ledger.unknown is False


class TestSentinelHandling:
    """`-1` is the sentinel the LLMClient emits when `response.usage is None`.

    Pre-registration (PHASE0_METRICS): accumulator must NOT silently add
    -1 (underflow would spuriously inflate apparent throughput). It must
    count the call but flip `unknown=True`; downstream excludes the run
    from tokens_per_solved.
    """

    def test_both_minus_one_marks_unknown_and_does_not_accumulate(self):
        with token_accounting("case1", 0) as ledger:
            _emit("llm_call_complete", {"prompt_tokens": -1, "completion_tokens": -1})
            _emit("llm_call_complete", {"prompt_tokens": 10, "completion_tokens": 5})
        assert ledger.calls == 2
        assert ledger.prompt_tokens == 10
        assert ledger.completion_tokens == 5
        assert ledger.unknown is True

    def test_partial_sentinel_also_marks_unknown(self):
        # Only one of the two is -1 — still unsafe to accumulate either.
        with token_accounting("case1", 0) as ledger:
            _emit(
                "llm_call_complete", {"prompt_tokens": -1, "completion_tokens": 5}
            )
        assert ledger.unknown is True
        assert ledger.prompt_tokens == 0
        assert ledger.completion_tokens == 0
        assert ledger.calls == 1

    def test_zero_tokens_is_not_sentinel(self):
        # An honest zero (cached response, no completion) must not flip unknown.
        with token_accounting("case1", 0) as ledger:
            _emit("llm_call_complete", {"prompt_tokens": 0, "completion_tokens": 0})
        assert ledger.unknown is False
        assert ledger.calls == 1


class TestNestedScopes:
    def test_inner_does_not_pollute_outer(self):
        with token_accounting("outer", 0) as outer:
            _emit("llm_call_complete", {"prompt_tokens": 1, "completion_tokens": 1})
            with token_accounting("inner", 99) as inner:
                _emit(
                    "llm_call_complete",
                    {"prompt_tokens": 100, "completion_tokens": 50},
                )
            assert inner.prompt_tokens == 100
            assert inner.completion_tokens == 50
            # Outer must be untouched by inner's emit.
            assert outer.prompt_tokens == 1
            # Continuing in outer scope appends to outer, not inner.
            _emit("llm_call_complete", {"prompt_tokens": 2, "completion_tokens": 2})
        assert outer.prompt_tokens == 3
        assert outer.completion_tokens == 3
        assert outer.calls == 2

    def test_outer_restored_after_inner_exit(self):
        with token_accounting("outer", 0) as outer:
            with token_accounting("inner", 1):
                pass
            # After inner exits, the active scope must be outer again.
            _emit("llm_call_complete", {"prompt_tokens": 7, "completion_tokens": 3})
        assert outer.prompt_tokens == 7


class TestScopeIsolation:
    def test_exception_inside_scope_still_resets(self):
        with pytest.raises(ValueError, match="boom"):
            with token_accounting("case1", 0):
                _emit(
                    "llm_call_complete", {"prompt_tokens": 5, "completion_tokens": 5}
                )
                raise ValueError("boom")
        # If the scope leaked, this emit would accumulate into a stale entry.
        # The only observable assertion: no exception, no stray state.
        _emit("llm_call_complete", {"prompt_tokens": 999, "completion_tokens": 999})

    def test_concurrent_threads_have_isolated_scopes(self):
        """Each worker enters its own context; ContextVar gives per-thread default."""
        results: dict[str, TokenLedgerEntry] = {}

        def worker(case_id: str, n_calls: int) -> None:
            with token_accounting(case_id, 0) as ledger:
                for _ in range(n_calls):
                    _emit(
                        "llm_call_complete",
                        {"prompt_tokens": 1, "completion_tokens": 1},
                    )
            results[case_id] = ledger

        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = [ex.submit(worker, f"case{i}", i + 1) for i in range(4)]
            for f in futures:
                f.result()

        for i in range(4):
            entry = results[f"case{i}"]
            assert entry.calls == i + 1
            assert entry.prompt_tokens == i + 1
            assert entry.completion_tokens == i + 1


# ---------------------------------------------------------------------------
# Hook + accumulator coexistence
# ---------------------------------------------------------------------------


def test_user_hook_and_accumulator_both_fire():
    """A registered hook still receives events emitted inside a scope."""
    seen: list[str] = []
    set_hook(lambda et, pl: seen.append(et))
    try:
        with token_accounting("case1", 0) as ledger:
            _emit("llm_call_complete", {"prompt_tokens": 10, "completion_tokens": 5})
    finally:
        set_hook(None)
    assert seen == ["llm_call_complete"]
    assert ledger.prompt_tokens == 10


def test_hook_failure_does_not_prevent_accumulation():
    """If the user hook raises, the ledger must still record the event."""

    def bad(*a, **k):
        raise RuntimeError("hook crashed")

    set_hook(bad)
    try:
        with token_accounting("case1", 0) as ledger:
            _emit("llm_call_complete", {"prompt_tokens": 10, "completion_tokens": 5})
    finally:
        set_hook(None)
    assert ledger.prompt_tokens == 10
    assert ledger.completion_tokens == 5
    assert ledger.calls == 1
