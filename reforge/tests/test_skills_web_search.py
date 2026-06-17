"""Tests for WebSearchSkill (P3).

Strategy: cover behaviour with a hand-written fake provider — never hit the
real Tavily API in CI. A separate optional integration test (skipped unless
TAVILY_API_KEY is set) exercises the real backend.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from reforge.runtime.skills import Skill, SkillContext, SkillResult
from reforge.runtime.skills.builtin import default_skill_registry
from reforge.runtime.skills.builtin.web_search import (
    SearchProvider,
    SearchProviderError,
    SearchResult,
    TavilyProvider,
    WebSearchSkill,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(tmp_path: Path) -> SkillContext:
    return SkillContext(session_id="ws-test", workspace=tmp_path, timeout_s=5)


class FakeProvider:
    """In-memory SearchProvider for testing."""

    name = "fake"

    def __init__(
        self,
        results: list[SearchResult] | None = None,
        answer_text: str = "",
        *,
        raise_on: str | None = None,
    ) -> None:
        self.results = results or []
        self.answer_text = answer_text
        self.raise_on = raise_on
        self.calls: list[tuple[str, dict]] = []

    def search(self, query, *, max_results=5, timeout_s=10.0):
        self.calls.append(("search", {"query": query, "max_results": max_results}))
        if self.raise_on == "search":
            raise SearchProviderError("simulated provider failure")
        return self.results[:max_results]

    def answer(self, query, *, max_results=5, timeout_s=10.0):
        self.calls.append(("answer", {"query": query}))
        if self.raise_on == "answer":
            raise SearchProviderError("simulated answer failure")
        return self.answer_text


# ---------------------------------------------------------------------------
# WebSearchSkill — protocol conformance + behaviour
# ---------------------------------------------------------------------------


class TestWebSearchSkillProtocol:
    def test_satisfies_skill_protocol(self) -> None:
        skill = WebSearchSkill(provider=FakeProvider())
        assert isinstance(skill, Skill)

    def test_fake_provider_satisfies_search_provider(self) -> None:
        assert isinstance(FakeProvider(), SearchProvider)

    def test_skill_has_openai_schema(self) -> None:
        skill = WebSearchSkill(provider=FakeProvider())
        assert "query" in skill.input_schema["properties"]
        assert skill.input_schema["required"] == ["query"]


class TestWebSearchSkillInvocation:
    def test_happy_path_with_answer(self, tmp_path: Path) -> None:
        provider = FakeProvider(
            results=[
                SearchResult(title="MCP Spec", url="https://example.com/mcp", snippet="The Model Context Protocol...", score=0.92),
                SearchResult(title="Blog post", url="https://example.com/blog", snippet="MCP enables...", score=0.85),
            ],
            answer_text="MCP is a 2024 protocol from Anthropic.",
        )
        skill = WebSearchSkill(provider=provider)
        result = skill.invoke({"query": "what is MCP"}, _ctx(tmp_path))
        assert isinstance(result, SkillResult)
        assert result.success
        assert result.metadata["provider"] == "fake"
        assert result.metadata["result_count"] == 2
        assert result.metadata["has_answer"] is True
        assert "Answer: MCP is a 2024 protocol" in result.output
        assert "https://example.com/mcp" in result.output
        assert "[1] MCP Spec" in result.output
        # Score formatted
        assert "(score=0.92)" in result.output

    def test_want_answer_false_skips_answer_call(self, tmp_path: Path) -> None:
        provider = FakeProvider(
            results=[SearchResult(title="t", url="https://x", snippet="s")],
            answer_text="should not be called",
        )
        skill = WebSearchSkill(provider=provider)
        result = skill.invoke(
            {"query": "q", "want_answer": False}, _ctx(tmp_path)
        )
        assert result.success
        assert "Answer:" not in result.output
        # Provider was called for search but not answer
        methods = [c[0] for c in provider.calls]
        assert "search" in methods and "answer" not in methods

    def test_empty_query_rejected(self, tmp_path: Path) -> None:
        skill = WebSearchSkill(provider=FakeProvider())
        for params in ({}, {"query": ""}, {"query": "   "}):
            r = skill.invoke(params, _ctx(tmp_path))
            assert not r.success and "query" in r.error.lower()

    def test_max_results_clamped(self, tmp_path: Path) -> None:
        provider = FakeProvider(
            results=[SearchResult(title=f"t{i}", url=f"https://x/{i}") for i in range(10)],
        )
        skill = WebSearchSkill(provider=provider)
        result = skill.invoke({"query": "q", "max_results": 3}, _ctx(tmp_path))
        assert result.success
        assert result.metadata["result_count"] == 3
        # Provider received the max_results value
        assert provider.calls[0][1]["max_results"] == 3

    def test_provider_error_surfaced_as_failure(self, tmp_path: Path) -> None:
        provider = FakeProvider(raise_on="search")
        skill = WebSearchSkill(provider=provider)
        result = skill.invoke({"query": "q"}, _ctx(tmp_path))
        assert not result.success
        assert "simulated provider failure" in result.error
        assert "fake" in result.error  # provider name in error

    def test_snippet_truncation(self, tmp_path: Path) -> None:
        long = "x" * 1000
        provider = FakeProvider(
            results=[SearchResult(title="t", url="https://x", snippet=long)],
        )
        skill = WebSearchSkill(provider=provider)
        result = skill.invoke({"query": "q", "want_answer": False}, _ctx(tmp_path))
        assert result.success
        assert "…[truncated]" in result.output


# ---------------------------------------------------------------------------
# Provider abstraction — multiple providers can plug in
# ---------------------------------------------------------------------------


class TestProviderInjection:
    def test_alternative_provider_works(self, tmp_path: Path) -> None:
        """Demonstrates SearchProvider Protocol is honoured — any compliant class works."""

        class StubBrave:
            name = "brave"

            def search(self, query, *, max_results=5, timeout_s=10.0):
                return [SearchResult(title="Brave hit", url="https://b.com")]

            def answer(self, query, *, max_results=5, timeout_s=10.0):
                return ""

        skill = WebSearchSkill(provider=StubBrave())
        result = skill.invoke({"query": "q"}, _ctx(tmp_path))
        assert result.success
        assert result.metadata["provider"] == "brave"
        assert "Brave hit" in result.output


# ---------------------------------------------------------------------------
# TavilyProvider — HTTP path mocked via urllib.request.urlopen
# ---------------------------------------------------------------------------


class _MockHTTPResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


class TestTavilyProvider:
    def test_missing_api_key_raises(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            provider = TavilyProvider()
            with pytest.raises(SearchProviderError) as exc:
                provider.search("q")
            assert "missing API key" in str(exc.value)

    def test_explicit_api_key_used(self) -> None:
        provider = TavilyProvider(api_key="explicit-key")
        captured: dict = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _MockHTTPResponse(json.dumps({"results": []}).encode("utf-8"))

        with patch("urllib.request.urlopen", fake_urlopen):
            provider.search("hello")

        assert captured["body"]["api_key"] == "explicit-key"
        assert captured["body"]["query"] == "hello"

    def test_response_parsed_into_search_results(self) -> None:
        provider = TavilyProvider(api_key="k")
        api_resp = {
            "answer": "syn answer",
            "results": [
                {"title": "T1", "url": "https://a", "content": "C1", "score": 0.9},
                {"title": "T2", "url": "https://b", "content": "C2", "score": 0.7},
                {"title": "drop-no-url", "content": "no url"},  # filtered
            ],
        }
        with patch(
            "urllib.request.urlopen",
            return_value=_MockHTTPResponse(json.dumps(api_resp).encode("utf-8")),
        ):
            results = provider.search("q")
        assert len(results) == 2
        assert results[0] == SearchResult(title="T1", url="https://a", snippet="C1", score=0.9)
        assert results[1].url == "https://b"

    def test_answer_extracts_string(self) -> None:
        provider = TavilyProvider(api_key="k")
        api_resp = {"answer": "the answer", "results": []}
        with patch(
            "urllib.request.urlopen",
            return_value=_MockHTTPResponse(json.dumps(api_resp).encode("utf-8")),
        ):
            assert provider.answer("q") == "the answer"

    def test_http_error_becomes_provider_error(self) -> None:
        import io
        import urllib.error

        provider = TavilyProvider(api_key="k")

        def fake(req, timeout=None):
            raise urllib.error.HTTPError(
                provider._endpoint, 401, "Unauthorized", {}, io.BytesIO(b"bad key")  # noqa: SLF001
            )

        with patch("urllib.request.urlopen", fake):
            with pytest.raises(SearchProviderError) as exc:
                provider.search("q")
        assert "401" in str(exc.value)

    def test_invalid_search_depth_rejected(self) -> None:
        with pytest.raises(ValueError):
            TavilyProvider(api_key="k", search_depth="ultra")


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestRegistryAutoDetection:
    def test_no_tavily_key_no_web_search_registered(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            reg = default_skill_registry()
        assert "web_search" not in reg.names()

    def test_tavily_key_present_registers_web_search(self) -> None:
        with patch.dict(os.environ, {"TAVILY_API_KEY": "fake"}, clear=False):
            reg = default_skill_registry()
        assert "web_search" in reg.names()

    def test_explicit_include_overrides_env(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            reg = default_skill_registry(include_web_search=True)
        assert "web_search" in reg.names()


# ---------------------------------------------------------------------------
# Optional real-API smoke (skipped without env var)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("TAVILY_API_KEY"),
    reason="TAVILY_API_KEY not set — skipping live integration test",
)
def test_real_tavily_smoke(tmp_path: Path) -> None:
    skill = WebSearchSkill()
    result = skill.invoke({"query": "what is the Model Context Protocol"}, _ctx(tmp_path))
    assert result.success, result.error
    assert result.metadata["result_count"] >= 1
