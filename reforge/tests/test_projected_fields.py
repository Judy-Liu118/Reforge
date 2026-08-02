"""Contract tests for PROJECTED_FIELDS — the runtime's projection registry.

Sibling to test_state_no_flat_fields.py: that file is a deny-list guarding
RuntimeState's TOP-LEVEL shape; this file governs DERIVED fields INSIDE the
nested sub-states. A projected field is one that is fully reconstructable from
the event log (its source of truth) — a discardable cache, not an independent
authority.

The authoritative rule (the three conditions, the projection / mirrored /
dual-write taxonomy, and the assembly-time premise) lives in OWNERSHIP.md,
section "Projection vs Mirror vs Dual-Write". This file is its executable half:
a registry of the fields that qualify, plus tests that each registered field
really is reconstructable and really is a discardable cache.

Scope honesty: these tests lock DOWN already-registered fields. They CANNOT
detect a newly added, unregistered projection-shaped field — that requires
semantic judgement and is a process/review responsibility (see the process
requirement in OWNERSHIP.md), not something the contract test can enforce. Do
NOT read a green run here as "no unregistered projections exist".
"""

from __future__ import annotations

from dataclasses import dataclass

from reforge.runtime.domain.state.models import RuntimeState
from reforge.runtime.events.log import ExecutionEventLog
from reforge.runtime.events.models import recovery_attempted
from reforge.runtime.events.projection import project_state
from reforge.tests._consistency import check_state_consistency


@dataclass(frozen=True)
class ProjectedField:
    """One field whose value is a projection of the event log.

    path            dotted path from RuntimeState to the field
    source_event    the ExecutionEvent kind it is rebuilt from
    reconstruction  how the value is recomputed from that event stream
    guaranteed_by   the assembly point that guarantees the source exists
    """

    path: str
    source_event: str
    reconstruction: str
    guaranteed_by: str


# The registry. Strict definition (see OWNERSHIP.md): a member must be
# reconstructable from its source event ALONE. Today that is exactly one field
# — a fact about this codebase, not a placeholder awaiting more entries.
#
# Mirrored fields (control_state.retry_decision_action,
# semantic_state.reflection_summary, semantic_state.evaluation_result) are
# deliberately NOT here: each is written from the same local variable as its
# event and cannot be rebuilt from the log, so it fails condition 2
# (reconstructability). See OWNERSHIP.md for that taxonomy.
PROJECTED_FIELDS: tuple[ProjectedField, ...] = (
    ProjectedField(
        path="control_state.retry_count",
        source_event="RECOVERY_ATTEMPTED",
        reconstruction=(
            "len(event_log.query(kind='RECOVERY_ATTEMPTED', session_id=...)) — "
            "written at emitters.py:349-351, independently recomputed at "
            "projection.py:85-86"
        ),
        guaranteed_by=(
            "RuntimeRunner.__init__ (runner.py:46) always-active log; degrades "
            "to a node-local count under build_graph(event_log=None) legacy mode"
        ),
    ),
)


def _resolve_path(path: str) -> None:
    """Assert *path* resolves to a declared model field, descending sub-states."""
    model: type = RuntimeState
    parts = path.split(".")
    for i, part in enumerate(parts):
        fields = getattr(model, "model_fields", {})
        assert part in fields, (
            f"PROJECTED_FIELDS path {path!r}: {part!r} is not a field of "
            f"{getattr(model, '__name__', model)!r}"
        )
        if i < len(parts) - 1:
            model = fields[part].annotation


def test_registry_is_not_empty_and_contains_retry_count() -> None:
    """Guard against the registry being silently emptied — which would make the
    reconstruction test below vacuously pass."""
    paths = {f.path for f in PROJECTED_FIELDS}
    assert "control_state.retry_count" in paths


def test_projected_field_paths_resolve() -> None:
    """T3: every registered path resolves to a real model field.

    Guards registry/schema drift — a renamed field left behind a stale registry
    entry is worse than no registry. Does NOT guard the rule itself.
    """
    for f in PROJECTED_FIELDS:
        _resolve_path(f.path)


def test_retry_count_reconstructs_from_events() -> None:
    """T1: retry_count is fully rebuildable from RECOVERY_ATTEMPTED alone."""
    assert any(f.path == "control_state.retry_count" for f in PROJECTED_FIELDS)
    log = ExecutionEventLog()
    for i in range(3):
        log.append(recovery_attempted("s1", "task", strategy="llm_retry", attempt=i + 1))
    proj = project_state("s1", log)
    assert proj.retry_count == 3


def test_retry_count_is_discardable_cache_events_win() -> None:
    """T2: on disagreement the event log is authoritative; the field is a cache.

    A corrupted stored retry_count does not survive reconstruction, and the
    consistency check flags the disagreement (report-only — no runtime arbiter
    corrects it; see OWNERSHIP.md condition 3).
    """
    log = ExecutionEventLog()
    for i in range(2):
        log.append(recovery_attempted("s1", "task", strategy="llm_retry", attempt=i + 1))

    corrupted = RuntimeState(user_request="x")
    corrupted.control_state.retry_count = 999

    proj = project_state("s1", log)
    assert proj.retry_count == 2  # rebuilt from events, ignores the stored 999

    report = check_state_consistency(proj, corrupted)
    assert "retry_count" in report.mismatch_fields()
