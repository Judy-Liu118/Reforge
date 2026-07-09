"""Smoke test — actually calls the configured LLM API.

Skipped unless RUN_LLM_SMOKE=1 is set in the environment.

Run manually:
    RUN_LLM_SMOKE=1 python -m pytest reforge/tests/integration/test_llm_smoke.py -v
"""
from __future__ import annotations

import os

import pytest

SKIP_REASON = "Set RUN_LLM_SMOKE=1 to run live API smoke tests"
skip_unless_smoke = pytest.mark.skipif(
    os.getenv("RUN_LLM_SMOKE") != "1",
    reason=SKIP_REASON,
)


@skip_unless_smoke
def test_llm_client_real_call():
    """Verify the configured API key + model returns a non-empty response."""
    from reforge.models.adapters.llm_client import LLMClient

    client = LLMClient()
    result = client.chat(
        system_prompt="You are a concise assistant.",
        user_message="Reply with exactly the word: pong",
    )
    assert result, "LLM returned empty response"
    assert isinstance(result, str)


@skip_unless_smoke
def test_llm_client_hook_fires_on_real_call():
    """Verify hook events are emitted during a real API call."""
    from reforge.models.adapters.llm_client import LLMClient, set_hook

    events: list[tuple[str, dict]] = []
    set_hook(lambda t, p: events.append((t, p)))
    try:
        client = LLMClient()
        client.chat("You are concise.", "Say: ok")
    finally:
        set_hook(None)

    types = {e[0] for e in events}
    assert "llm_call_start" in types
    assert "llm_call_complete" in types
    # Check that latency and token counts were captured
    complete = next(e[1] for e in events if e[0] == "llm_call_complete")
    assert complete["latency_ms"] > 0
