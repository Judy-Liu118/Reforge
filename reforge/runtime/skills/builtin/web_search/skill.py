"""WebSearchSkill — Reforge Skill wrapping any SearchProvider."""

from __future__ import annotations

import time

from reforge.runtime.skills.builtin.web_search.provider import (
    SearchProvider,
    SearchProviderError,
)
from reforge.runtime.skills.builtin.web_search.tavily import TavilyProvider
from reforge.runtime.skills.context import SkillContext
from reforge.runtime.skills.result import SkillResult

_DEFAULT_MAX_RESULTS = 5
_SNIPPET_TRUNCATE = 500


class WebSearchSkill:
    """Search the web and return either an LLM-synthesised answer or a result list.

    The default backend is Tavily. Inject a different `SearchProvider` to swap
    in SerpAPI, Brave, or any custom backend.
    """

    name = "web_search"
    description = (
        "Search the web for current information. Returns a synthesised answer when "
        "the backend supports it, plus a list of source URLs with snippets. Use "
        "this when the task requires information beyond what's in memory or the codebase."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language search query.",
            },
            "max_results": {
                "type": "integer",
                "description": f"Maximum number of source results to return (default {_DEFAULT_MAX_RESULTS}).",
                "default": _DEFAULT_MAX_RESULTS,
            },
            "want_answer": {
                "type": "boolean",
                "description": "If true, also fetch a synthesised answer (slower, costs more tokens). Default true.",
                "default": True,
            },
        },
        "required": ["query"],
    }
    prompt_fragment = ""

    def __init__(self, provider: SearchProvider | None = None) -> None:
        self._provider = provider or TavilyProvider()

    def invoke(self, params: dict, context: SkillContext) -> SkillResult:
        query = params.get("query")
        if not isinstance(query, str) or not query.strip():
            return SkillResult(
                success=False, error="web_search: 'query' is required and must be non-empty"
            )
        max_results = max(1, int(params.get("max_results", _DEFAULT_MAX_RESULTS)))
        want_answer = bool(params.get("want_answer", True))

        start = time.perf_counter()
        try:
            results = self._provider.search(
                query, max_results=max_results, timeout_s=float(context.timeout_s)
            )
            answer = (
                self._provider.answer(
                    query, max_results=max_results, timeout_s=float(context.timeout_s)
                )
                if want_answer
                else ""
            )
        except SearchProviderError as exc:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            return SkillResult(
                success=False,
                error=f"web_search[{self._provider.name}]: {exc}",
                duration_ms=duration_ms,
            )

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        output = _format_output(query, answer, results)
        return SkillResult(
            success=True,
            output=output,
            raw={"answer": answer, "results": results},
            duration_ms=duration_ms,
            metadata={
                "provider": self._provider.name,
                "result_count": len(results),
                "has_answer": bool(answer),
            },
        )


def _format_output(query: str, answer: str, results: list) -> str:
    """LLM-friendly stringification: answer first, then numbered sources."""
    lines: list[str] = []
    if answer:
        lines.append(f"Answer: {answer}")
        lines.append("")
    lines.append(f"Sources for: {query}")
    for i, r in enumerate(results, start=1):
        snippet = r.snippet or ""
        if len(snippet) > _SNIPPET_TRUNCATE:
            snippet = snippet[:_SNIPPET_TRUNCATE] + " …[truncated]"
        score = f" (score={r.score:.2f})" if r.score is not None else ""
        lines.append(f"[{i}] {r.title}{score}")
        lines.append(f"    {r.url}")
        if snippet:
            lines.append(f"    {snippet}")
    return "\n".join(lines)
