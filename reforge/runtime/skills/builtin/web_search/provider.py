"""SearchProvider Protocol — pluggable backend for WebSearchSkill.

Multiple providers (Tavily, SerpAPI, Brave, DuckDuckGo, custom) can satisfy
this shape. WebSearchSkill takes any of them via constructor injection, so
swapping backends is one line in your setup code.

Why a Protocol rather than inheritance: keeps providers decoupled from
Reforge internals — anyone can implement SearchResult.search(...) without
importing our base class.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class SearchProviderError(RuntimeError):
    """Raised when a provider fails irrecoverably (network down, auth, quota)."""


@dataclass(frozen=True)
class SearchResult:
    """One search hit. Provider-agnostic shape."""

    title: str
    url: str
    snippet: str = ""
    score: float | None = None  # provider-specific relevance, None when unavailable


@runtime_checkable
class SearchProvider(Protocol):
    """A web search backend."""

    @property
    def name(self) -> str: ...

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        timeout_s: float = 10.0,
    ) -> list[SearchResult]: ...

    def answer(
        self,
        query: str,
        *,
        max_results: int = 5,
        timeout_s: float = 10.0,
    ) -> str:
        """Optional: return an LLM-synthesised answer string when supported.

        Providers that don't synthesise (DuckDuckGo, raw SerpAPI) MAY return
        the empty string. WebSearchSkill falls back to result snippets in that case.
        """
        ...
