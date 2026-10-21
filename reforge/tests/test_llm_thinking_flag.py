"""LLM_ENABLE_THINKING: unset → request unchanged; true/false → extra_body.enable_thinking."""

from reforge.models.adapters import llm_client


def test_unset_sends_nothing(monkeypatch):
    monkeypatch.setattr(llm_client.config, "llm_enable_thinking", "")
    assert llm_client._thinking_kwargs() == {}


def test_false_and_true(monkeypatch):
    monkeypatch.setattr(llm_client.config, "llm_enable_thinking", "false")
    assert llm_client._thinking_kwargs() == {"extra_body": {"enable_thinking": False}}
    monkeypatch.setattr(llm_client.config, "llm_enable_thinking", "true")
    assert llm_client._thinking_kwargs() == {"extra_body": {"enable_thinking": True}}


def test_garbage_is_ignored(monkeypatch):
    monkeypatch.setattr(llm_client.config, "llm_enable_thinking", "maybe")
    assert llm_client._thinking_kwargs() == {}
