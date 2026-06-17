"""Research-mode CLI functions — auto-detection, run, history, and export."""

from __future__ import annotations

import sys
from pathlib import Path

from reforge.cli.formatter import (
    format_research_header,
    format_research_history,
    format_research_hypothesis,
    format_research_summary,
)
from reforge.runtime.research.aggregator import EvidenceAggregator
from reforge.runtime.research.models import ResearchResult, ResearchRound
from reforge.runtime.research.reporter import ResearchReporter
from reforge.runtime.agents.synthesizer import render_conclusion
from reforge.runtime.research.session import ResearchSession
from reforge.runtime.research.store import ResearchStore
from reforge.runtime.infrastructure.trajectory.store import TrajectoryStore

_RESEARCH_KEYWORDS_EN = frozenset({
    "why", "what causes", "investigate", "explain why",
    "how does", "what factors", "analyze why", "reason for",
    "cause of", "effect of",
})
_RESEARCH_KEYWORDS_ZH = frozenset({
    "为什么", "为何", "研究", "分析为什么", "原因", "调查", "探讨", "探究",
})


def is_research_question(text: str) -> bool:
    """Return True if the text looks like an open-ended research question."""
    lower = text.lower()
    if any(kw in lower for kw in _RESEARCH_KEYWORDS_EN):
        return True
    return any(kw in text for kw in _RESEARCH_KEYWORDS_ZH)


def run_research(question: str) -> None:
    """Execute a research session with live streaming output and persist the result."""
    traj_store = TrajectoryStore()
    session = ResearchSession(trajectory_store=traj_store)

    print(format_research_header(question, session._max_rounds))

    collected: list = []
    for round_num, _original, updated in session.stream(question):
        evidence = updated.evidence[0] if updated.evidence else ""
        print(format_research_hypothesis(round_num, updated.hypothesis, updated.status, evidence))
        collected.append(updated)

    if not collected:
        print("  No hypotheses were generated.")
        return

    # Reconstruct ResearchResult from stream output
    agg = EvidenceAggregator()
    rounds_seen = sorted({h.round_number for h in collected})
    rounds: list[ResearchRound] = []
    all_contradictions: list[str] = []
    for rn in rounds_seen:
        round_hyps = [h for h in collected if h.round_number == rn]
        contradictions = agg.detect_contradictions(round_hyps)
        all_contradictions.extend(contradictions)
        rounds.append(ResearchRound(
            round_number=rn,
            hypotheses_tested=[h.hypothesis_id for h in round_hyps],
            new_findings=[
                h.evidence[0] for h in round_hyps
                if h.evidence and h.status == "confirmed"
            ],
            contradictions=contradictions,
        ))

    result = ResearchResult(
        question=question,
        rounds=rounds,
        final_hypotheses=collected,
        conclusion=render_conclusion(question, collected),
        contradictions_detected=all_contradictions,
        total_rounds=len(rounds),
    )

    print(format_research_summary(result))
    store = ResearchStore()
    store.save(result)
    print(
        f"  [research: {len(collected)} hypotheses, {result.total_rounds} round(s)]"
        f"  [id: {result.research_id}]"
    )


def handle_research_history() -> None:
    results = ResearchStore().list_all()
    print(format_research_history(results))


def export_research(research_id: str, output_dir: Path | None = None) -> None:
    """Export a research result to a Markdown file by research_id."""
    store = ResearchStore()
    result = store.find_by_id(research_id)

    if result is None:
        # Fallback: try keyword search
        candidates = store.find_by_question(research_id, limit=1)
        if candidates:
            result = candidates[0]

    if result is None:
        print(f"  Research not found: '{research_id}'")
        print("  Use --research-history to see available research IDs.")
        sys.exit(1)

    reporter = ResearchReporter()
    md = reporter.render(result)

    dest_dir = output_dir or Path(".")
    filename = f"research_{result.research_id}.md"
    out_path = dest_dir / filename
    out_path.write_text(md, encoding="utf-8")
    print(f"  Exported: {out_path}")
