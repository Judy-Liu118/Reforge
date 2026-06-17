"""TavilyProvider — default WebSearchSkill backend.

Tavily (https://tavily.com) is an LLM-optimised search API. We hit its
`/search` endpoint directly with stdlib urllib — no `requests` dependency.

Auth: pass `api_key=` or set the `TAVILY_API_KEY` environment variable.
Pricing: free tier 1000 queries/month (as of 2026).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from reforge.runtime.skills.builtin.web_search.provider import (
    SearchProviderError,
    SearchResult,
)

_TAVILY_ENDPOINT = "https://api.tavily.com/search"


class TavilyProvider:
    """Tavily-backed search. Synchronous, stdlib-only HTTP."""

    name = "tavily"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        endpoint: str = _TAVILY_ENDPOINT,
        search_depth: str = "basic",
    ) -> None:
        self._api_key = api_key or os.environ.get("TAVILY_API_KEY")
        self._endpoint = endpoint
        self._search_depth = search_depth
        if search_depth not in ("basic", "advanced"):
            raise ValueError(f"search_depth must be 'basic' or 'advanced', got {search_depth!r}")

    # ------------------------------------------------------------------
    # SearchProvider Protocol
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        timeout_s: float = 10.0,
    ) -> list[SearchResult]:
        payload = self._payload(query, max_results=max_results, include_answer=False)
        data = self._post(payload, timeout_s=timeout_s)
        return self._parse_results(data)

    def answer(
        self,
        query: str,
        *,
        max_results: int = 5,
        timeout_s: float = 10.0,
    ) -> str:
        payload = self._payload(query, max_results=max_results, include_answer=True)
        data = self._post(payload, timeout_s=timeout_s)
        ans = data.get("answer", "")
        return ans if isinstance(ans, str) else ""

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _payload(self, query: str, *, max_results: int, include_answer: bool) -> dict:
        if not self._api_key:
            raise SearchProviderError(
                "tavily: missing API key — pass api_key= or set TAVILY_API_KEY"
            )
        return {
            "api_key": self._api_key,
            "query": query,
            "search_depth": self._search_depth,
            "max_results": max(1, min(int(max_results), 20)),
            "include_answer": include_answer,
        }

    def _post(self, payload: dict, *, timeout_s: float) -> dict:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise SearchProviderError(
                f"tavily HTTP {exc.code}: {exc.reason}: {detail[:200]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise SearchProviderError(f"tavily network error: {exc.reason}") from exc
        except TimeoutError as exc:
            raise SearchProviderError(f"tavily timeout after {timeout_s}s") from exc

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SearchProviderError(f"tavily returned non-JSON: {exc}") from exc

    @staticmethod
    def _parse_results(data: dict) -> list[SearchResult]:
        items = data.get("results") or []
        out: list[SearchResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            url = item.get("url", "")
            if not url:
                continue
            out.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=url,
                    snippet=item.get("content", ""),
                    score=item.get("score") if isinstance(item.get("score"), (int, float)) else None,
                )
            )
        return out
