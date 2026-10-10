"""PlannerMemoryContext — retrieves and formats past experiences for the planner.

Keeps memory-retrieval logic out of graph nodes (per OWNERSHIP.md).
Returns an empty string when no relevant memory exists, so the planner
behaves identically to pre-P8 when memory is cold.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from reforge.memory.substrate import CompositeMemorySubstrate, MemorySubstrate
from reforge.memory.writer import distilled_memory_enabled

if TYPE_CHECKING:
    from reforge.runtime.infrastructure.trajectory.store import TrajectoryStore


class PlannerMemoryContext:
    """Builds a formatted memory context prefix for the planner prompt."""

    def __init__(
        self,
        substrate: MemorySubstrate | None = None,
        trajectory_store: "TrajectoryStore | None" = None,
    ) -> None:
        self._substrate = substrate or CompositeMemorySubstrate()
        self._trajectory_store = trajectory_store

    def build(self, user_request: str) -> str:
        """Return a formatted context string, or '' if nothing relevant found."""
        lines: list[str] = []

        # Experiment (REFORGE_EXP_DISTILLED_MEMORY=1): inject the top-1
        # distilled strategy card instead of the raw record lines. Planning-
        # time is the only place memory can act on silent-wrong tasks — the
        # reactive repair_hint path needs an exception to fire.
        recall_distilled = getattr(self._substrate, "recall_distilled", None)
        if distilled_memory_enabled() and recall_distilled is not None:
            records = []
            for r in recall_distilled(user_request, limit=1):
                card = r.distilled_strategy
                kind = "成功经验" if card.get("polarity") == "success" else "失败教训"
                lines.append("--- Distilled strategy from past experience ---")
                lines.append(f"[{kind}] {card.get('title', '')}")
                lines.append(f"适用场景: {card.get('description', '')}")
                lines.append(str(card.get("content", "")))
        else:
            # Past successes and recoveries from MemoryStore
            records = self._substrate.recall_for_planning(user_request, limit=3)
        if records:
            lines.append("--- Past execution experience ---")
            for r in records:
                fix = (r.recovery_action or "").strip()[:60] or "N/A"
                lines.append(
                    f"[{r.memory_type.value}] {r.user_request[:60]} "
                    f"→ {r.outcome} | Fix: {fix}"
                )

        # Similar sessions from TrajectoryStore
        if self._trajectory_store:
            similar = self._trajectory_store.find_similar(user_request, limit=2)
            if similar:
                lines.append("--- Past trajectory patterns ---")
                for t in similar:
                    chain = " → ".join(t.recovery_chain) if t.recovery_chain else "none"
                    lines.append(
                        f"[{t.final_outcome}] {t.user_request[:60]} "
                        f"| {t.total_attempts} attempt(s) | errors: {chain}"
                    )

        return "\n".join(lines) if lines else ""
