"""LLM-call observability — module-level event emission + per-scope token accounting.

Two responsibilities live here, kept colocated because both observe LLM calls
and both must be activated *outside* any system-under-test:

  1. ``set_hook`` / ``_emit`` — module-level callback fired on every LLM
     lifecycle event (``llm_call_start`` / ``llm_call_complete`` /
     ``llm_call_retry`` / ``llm_call_error``). Previously private to
     ``llm_client``; lifted here so observers other than the primary
     client share one event surface.

  2. ``token_accounting(case_id, seed)`` — a contextvars-keyed
     accumulator that captures ``llm_call_complete`` events emitted
     inside its ``with`` block and records (prompt_tokens,
     completion_tokens, calls) keyed by (case_id, seed). Built for
     benchmark drivers (``SqlBenchSession``, ``MultiSeedDriver``, …) to
     wrap each ``runner.run()``.

Design constraints — pre-registered in ``docs/eval/PHASE0_METRICS.md``:

* **Measurement-only**. Zero footprint on ``RuntimeState``, the
  governor decision path, or LLM-client behavior. The hook only adds
  an emit; the accumulator only listens.
* **contextvars, not thread-local**. ``ContextVar`` is the right
  primitive when the consumer knows the scope explicitly (a ``with``
  block in a driver loop). It survives ``async``/``await`` correctly,
  doesn't leak between concurrent benchmark workers (each thread /
  task gets its own context copy), and a missed exit is a
  test-detectable bug rather than a silent state corruption.
* **``-1`` sentinel handling**. The openai SDK returns ``usage=None``
  for providers that don't populate it; the LLM client emits
  ``prompt_tokens = -1`` / ``completion_tokens = -1`` in that case.
  The accumulator MUST NOT silently add ``-1`` (underflow would
  spuriously inflate apparent throughput). Instead the run's totals
  are marked ``unknown=True`` and the eval driver excludes such runs
  from ``tokens_per_solved`` (reporting the excluded count).

See also ``docs/KNOWN_LIMITATIONS.md`` L2 — vision skills currently
bypass ``LLMClient`` and therefore this hook. Coverage is 100% on the
measured BIRD/pandas-CSV corpora because they contain no image inputs.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Iterator


# ---------------------------------------------------------------------------
# Module-level hook
# ---------------------------------------------------------------------------


_hook: Callable[[str, dict[str, Any]], None] | None = None


def set_hook(fn: Callable[[str, dict[str, Any]], None] | None) -> None:
    """Register the global LLM-event hook.

    The hook is called with ``(event_type, payload)`` on every event
    emitted via ``_emit``. Event types: ``llm_call_start``,
    ``llm_call_complete``, ``llm_call_retry``, ``llm_call_error``.

    Thread-safe for reads; set once at startup before concurrent calls.
    """
    global _hook
    _hook = fn


def _emit(event_type: str, payload: dict[str, Any]) -> None:
    """Fire one LLM-lifecycle event.

    Two observers run, independently and in order:

    1. The token accumulator (if a ``token_accounting`` scope is
       active in the current context) — see ``_ledger_emit``.
    2. The user-registered ``_hook``, if any. Exceptions raised by
       the hook are swallowed; observability must never break the
       call path.
    """
    _ledger_emit(event_type, payload)
    if _hook is not None:
        try:
            _hook(event_type, payload)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Token accounting — contextvars-keyed accumulator
# ---------------------------------------------------------------------------


@dataclass
class TokenLedgerEntry:
    """Per-(case_id, seed) token totals captured by a ``token_accounting`` scope.

    ``unknown`` flips to ``True`` the moment any captured
    ``llm_call_complete`` event reports a ``-1`` sentinel for
    prompt/completion tokens. Downstream metric code must consult
    ``unknown`` and exclude such runs from ``tokens_per_solved`` rather
    than treating ``prompt_tokens`` / ``completion_tokens`` as authoritative.
    """

    case_id: str
    seed: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    unknown: bool = False

    @property
    def total_tokens(self) -> int:
        """Sum of prompt + completion. Only meaningful when ``not unknown``."""
        return self.prompt_tokens + self.completion_tokens


# The active accumulator scope. ``None`` when no ``token_accounting``
# context is open. Per-thread / per-async-task isolation is provided
# by ContextVar semantics.
_active_entry: ContextVar[TokenLedgerEntry | None] = ContextVar(
    "reforge_token_accounting", default=None
)


@contextmanager
def token_accounting(case_id: str, seed: int) -> Iterator[TokenLedgerEntry]:
    """Capture ``llm_call_complete`` token totals during the ``with`` block.

    Usage pattern (from a benchmark driver loop)::

        for seed_idx in range(n_seeds):
            for case in cases:
                with token_accounting(case.case_id, seed_idx) as ledger:
                    state = runner.run(prompt)
                report.attach_tokens(case.case_id, seed_idx, ledger)

    The yielded ``TokenLedgerEntry`` accumulates in place; reading
    fields on it after the ``with`` block exits returns the final
    totals. Nesting is supported: an inner scope replaces the active
    entry within its block, and the outer scope is restored on exit
    (no token leakage between scopes).
    """
    entry = TokenLedgerEntry(case_id=case_id, seed=seed)
    token = _active_entry.set(entry)
    try:
        yield entry
    finally:
        _active_entry.reset(token)


def _ledger_emit(event_type: str, payload: dict[str, Any]) -> None:
    """Accumulate ``llm_call_complete`` tokens into the active scope, if any.

    Called from ``_emit`` ahead of the user hook. Silent no-op when no
    ``token_accounting`` scope is active or the event isn't a completion.
    """
    if event_type != "llm_call_complete":
        return
    entry = _active_entry.get()
    if entry is None:
        return

    prompt = payload.get("prompt_tokens", -1)
    completion = payload.get("completion_tokens", -1)

    # Always count the call, even when usage is unknown — call count
    # is independently useful (retry behavior signal) and excluding it
    # would systematically under-count vision-bypassing providers
    # the moment the L2 gap (KNOWN_LIMITATIONS) gets closed.
    entry.calls += 1

    if prompt < 0 or completion < 0:
        entry.unknown = True
        return

    entry.prompt_tokens += prompt
    entry.completion_tokens += completion
