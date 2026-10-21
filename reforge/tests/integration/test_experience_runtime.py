"""Experience loop wired into the main runtime (RuntimeRunner → build_graph), mock LLM, real sandbox.

Flag off: byte-identical codegen prompt, no experience log written.
Flag on:  a swallowed exception is probed, confirmed by the un-swallowed re-run, minted into
          quarantine; a quarantined card never reaches a prompt; after a passing canary it does.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import Mock, patch

import pytest

from reforge.experience import CanaryReport
from reforge.experience import runtime_hooks
from reforge.models.prompts.templates import CODE_GENERATION_SYSTEM
from reforge.runtime.orchestration.engine.runner import RuntimeRunner

SWALLOW = "d = {}\ntry:\n    v = d['missing']\nexcept Exception:\n    pass\nprint('done')"
HARMLESS = "try:\n    v = 1\nexcept Exception:\n    pass\nprint(v)"
NO_RETURN = "def total(xs):\n    s = sum(xs)\nprint(total([1, 2, 3]))"

_INTENT = Mock()
_INTENT.chat = Mock(return_value="NORMAL_EXECUTION")


def _run(code: str, systems: list[str]):
    class FakeLLM:
        def chat(self, system: str, user: str) -> str:
            if system.startswith(CODE_GENERATION_SYSTEM[:40]):
                systems.append(system)
                return code
            return "1. plan"                         # planner / reflection

        def chat_multimodal(self, *a, **k):          # pragma: no cover - text tasks only
            raise AssertionError

    with ExitStack() as stack:
        for module in ("planner", "codegen", "reflection"):
            stack.enter_context(patch(f"reforge.runtime.orchestration.graph.nodes.{module}.LLMClient", FakeLLM))
        stack.enter_context(patch("reforge.runtime.policy.task_intent.LLMClient", return_value=_INTENT))
        return RuntimeRunner().run("compute the value")


@pytest.fixture
def exp_log(tmp_path, monkeypatch):
    path = tmp_path / "experience" / "loop.jsonl"
    monkeypatch.setenv(runtime_hooks.LOG_ENV, str(path))
    runtime_hooks._loop_for.cache_clear()
    yield path
    runtime_hooks._loop_for.cache_clear()


def test_flag_off_prompt_unchanged_and_nothing_logged(exp_log, monkeypatch):
    monkeypatch.delenv("REFORGE_EXPERIENCE_CARDS", raising=False)
    systems: list[str] = []
    state = _run(SWALLOW, systems)
    assert state.exec_state.exit_code == 0
    assert systems and all(s == CODE_GENERATION_SYSTEM for s in systems)
    assert not exp_log.exists()


def test_flag_on_swallowed_exception_is_verified_and_quarantined(exp_log, monkeypatch):
    monkeypatch.setenv("REFORGE_EXPERIENCE_CARDS", "1")
    systems: list[str] = []
    _run(SWALLOW, systems)
    cards = runtime_hooks.runtime_loop().store.cards()
    assert len(cards) == 1
    card = next(iter(cards.values()))
    assert card.status == "quarantined" and card.kind == "swallowed_exception"
    assert card.evidence[0]["exception_type"] == "KeyError" and card.evidence[0]["unswallowed_exit"] != 0
    assert "missing" not in str(card.evidence)                  # redacted: no data from the script
    assert all("Guardrails learned" not in s for s in systems)   # quarantined → never injected


def test_promoted_card_reaches_the_next_prompt_and_is_logged(exp_log, monkeypatch):
    monkeypatch.setenv("REFORGE_EXPERIENCE_CARDS", "1")
    _run(SWALLOW, [])
    loop = runtime_hooks.runtime_loop()
    cid = next(iter(loop.store.cards()))
    loop.promote(cid, CanaryReport(cid, n=4, control_success=2, treated_success=3))
    systems: list[str] = []
    _run("print(42)", systems)
    assert systems[0].startswith(CODE_GENERATION_SYSTEM)
    assert "Guardrails learned" in systems[0] and runtime_hooks.TEMPLATES["swallowed_exception"]["trigger"] in systems[0]
    assert loop.store.used_by(cid)                               # provenance recorded


def test_same_lesson_twice_is_not_readmitted(exp_log, monkeypatch):
    monkeypatch.setenv("REFORGE_EXPERIENCE_CARDS", "1")
    _run(SWALLOW, [])
    _run(SWALLOW, [])
    loop = runtime_hooks.runtime_loop()
    assert len(loop.store.cards()) == 1
    assert any("duplicate_of" in r for e in loop.store.log.events("CARD_REJECTED") for r in e.payload["reasons"])


def test_handler_that_never_fires_is_not_minted(exp_log, monkeypatch):
    monkeypatch.setenv("REFORGE_EXPERIENCE_CARDS", "1")
    _run(HARMLESS, [])
    assert runtime_hooks.runtime_loop().store.cards() == {}


def test_missing_return_is_verified_statically(exp_log, monkeypatch):
    monkeypatch.setenv("REFORGE_EXPERIENCE_CARDS", "1")
    _run(NO_RETURN, [])
    (card,) = runtime_hooks.runtime_loop().store.cards().values()
    assert card.kind == "missing_return" and card.verifier == "ast_no_return_value"


def test_hook_failure_never_breaks_the_run(exp_log, monkeypatch):
    monkeypatch.setenv("REFORGE_EXPERIENCE_CARDS", "1")

    def boom():
        raise RuntimeError("store unavailable")
    monkeypatch.setattr(runtime_hooks, "runtime_loop", boom)
    state = _run("print('ok')", [])
    assert state.exec_state.exit_code == 0 and state.exec_state.stdout == "ok\n"
