"""ResearchReporter — render ResearchResult as structured Markdown."""

from __future__ import annotations

from reforge.runtime.research.models import HypothesisRecord, ResearchResult

_STATUS_ICON = {
    "confirmed": "✓",
    "rejected": "✗",
    "inconclusive": "?",
    "pending": "·",
}


class ResearchReporter:
    """Render a ResearchResult as a readable Markdown document."""

    def render(self, result: ResearchResult) -> str:
        sections = [
            self._header(result),
            self._summary(result),
            self._hypothesis_table(result),
            self._contradictions(result),
            self._conclusion(result),
        ]
        return "\n\n".join(s for s in sections if s)

    def _header(self, result: ResearchResult) -> str:
        ts = result.timestamp[:10] if result.timestamp else "unknown"
        lines = [
            f"# Research Report",
            f"",
            f"**Question:** {result.question}",
            f"**ID:** `{result.research_id}`  |  **Date:** {ts}",
        ]
        return "\n".join(lines)

    def _summary(self, result: ResearchResult) -> str:
        confirmed = sum(1 for h in result.final_hypotheses if h.status == "confirmed")
        rejected = sum(1 for h in result.final_hypotheses if h.status == "rejected")
        inconclusive = sum(1 for h in result.final_hypotheses if h.status == "inconclusive")
        total = len(result.final_hypotheses)
        lines = [
            "## Summary",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total rounds | {result.total_rounds} |",
            f"| Hypotheses tested | {total} |",
            f"| Confirmed | {confirmed} |",
            f"| Rejected | {rejected} |",
            f"| Inconclusive | {inconclusive} |",
            f"| Contradictions | {len(result.contradictions_detected)} |",
        ]
        return "\n".join(lines)

    def _hypothesis_table(self, result: ResearchResult) -> str:
        if not result.final_hypotheses:
            return ""
        lines = [
            "## Hypotheses",
            "",
            "| # | Round | Status | Hypothesis | Evidence |",
            "|---|-------|--------|------------|----------|",
        ]
        for i, h in enumerate(result.final_hypotheses, 1):
            icon = _STATUS_ICON.get(h.status, "·")
            ev = h.evidence[0][:80].replace("|", "\\|") if h.evidence else ""
            hyp = h.hypothesis[:70].replace("|", "\\|")
            lines.append(
                f"| {i} | {h.round_number} | {icon} {h.status.capitalize()} "
                f"| {hyp} | {ev} |"
            )
        return "\n".join(lines)

    def _contradictions(self, result: ResearchResult) -> str:
        lines = ["## Contradictions", ""]
        if not result.contradictions_detected:
            lines.append("*(none detected)*")
        else:
            for c in result.contradictions_detected:
                lines.append(f"- {c}")
        return "\n".join(lines)

    def _conclusion(self, result: ResearchResult) -> str:
        if not result.conclusion:
            return ""
        return "## Conclusion\n\n" + result.conclusion
