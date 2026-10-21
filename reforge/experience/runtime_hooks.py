"""Hooks that attach the experience loop to Reforge's main runtime workflow.

Two hooks, both no-ops unless REFORGE_EXPERIENCE_CARDS=1:

    codegen_guidance(state, session_id)   before execution — called from the code-generation node;
                                          returns the rendered card block appended to the system
                                          prompt ("" when off or nothing recalled)
    after_execution(state, session_id)    after a round of execution — called from the evaluation
                                          wrapper; runs the domain probes, confirms with gold-free
                                          verifiers, mints into QUARANTINE. Never mutates state,
                                          never raises into the graph.

Cards minted here are quarantined: they reach a prompt only after promote() with a passing
canary report (an offline step), exactly like every other card. Distillation in this domain is a
deterministic template per failure kind (no LLM call, no tokens); a host can pass its own.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

from reforge.experience.cards import VerifiedFailure
from reforge.experience.loop import FLAG, ExperienceConfig, ExperienceLoop
from reforge.experience.recall import TaskContext
from reforge.experience import runtime_probes

logger = logging.getLogger(__name__)

ENV = "reforge"
LOG_ENV = "REFORGE_EXPERIENCE_LOG"

TEMPLATES = {
    "swallowed_exception": {
        "trigger": "When the code wraps a step in try/except.",
        "guardrail": "Do not catch an exception and carry on with pass, print or return: an error that is "
                     "swallowed makes a failed step look like success. Let it raise, or handle it with a real "
                     "recovery that still produces the correct result.",
    },
    "missing_return": {
        "trigger": "When the code defines a function whose result is printed or used.",
        "guardrail": "Make sure every such function returns its value with an explicit return statement; a "
                     "function without one returns None and the printed result is None.",
    },
}


def template_distiller(failure: VerifiedFailure) -> dict:
    return dict(TEMPLATES.get(failure.kind, {}))


def enabled() -> bool:
    return os.environ.get(FLAG, "0") == "1"


def _log_path() -> Path:
    override = os.environ.get(LOG_ENV)
    if override:
        return Path(override).expanduser()
    from reforge.paths import global_dir
    return global_dir() / "experience" / "loop.jsonl"


@lru_cache(maxsize=4)
def _loop_for(path: str) -> ExperienceLoop:
    return ExperienceLoop(ExperienceConfig(log_path=Path(path), env=ENV, enabled=True,
                                           required_anchors={"swallowed_exception": frozenset({"raise"}),
                                                             "missing_return": frozenset({"return"})}))


def runtime_loop() -> ExperienceLoop:
    return _loop_for(str(_log_path()))


def _task(state, session_id: str) -> TaskContext:
    attempt = state.control_state.retry_count + 1
    sid = session_id or "local"
    return TaskContext(task_id=sid, env=ENV, instruction=state.user_request, run_id=f"{sid}:{attempt}")


def codegen_guidance(state, session_id: str = "") -> str:
    if not enabled():
        return ""
    try:
        loop = runtime_loop()
        return loop.render(loop.recall(_task(state, session_id)))
    except Exception:                                   # recall must never break code generation
        logger.warning("experience recall failed; continuing without cards", exc_info=True)
        return ""


def _sandbox_rerun(code: str) -> tuple[int, str]:
    from reforge.config import config
    from reforge.runtime.infrastructure.execution.sandbox import SandboxExecutor
    r = SandboxExecutor(timeout=config.execution_timeout).execute(code)
    return r.exit_code, r.stderr or ""


def after_execution(state, session_id: str = "", *, rerun: Callable[[str], tuple[int, str]] | None = None,
                    distill=template_distiller) -> list[dict]:
    """→ one row per probe that fired: {kind, verified, minted|reasons}. [] when off."""
    if not enabled() or state.execution_output is None:
        return []
    rows: list[dict] = []
    try:
        loop = runtime_loop()
        t = _task(state, session_id)
        code, out = state.generated_code, state.execution_output
        checked: list[tuple[str, VerifiedFailure | None]] = []
        if runtime_probes.swallowed_exception_probe(code, out.exit_code):
            checked.append(("swallowed_exception", runtime_probes.confirm_swallowed_exception(
                code, out.exit_code, rerun or _sandbox_rerun, source_task=t.task_id, source_run=t.run_id)))
        if runtime_probes.missing_return_probe(out.stdout):
            checked.append(("missing_return", runtime_probes.confirm_missing_return(
                code, out.stdout, source_task=t.task_id, source_run=t.run_id)))
        for kind, f in checked:
            row = {"kind": kind, "verified": f is not None}
            if f is not None:
                res = loop.mint(f, distill)
                row.update(minted=res.card.card_id if res.card else None, reasons=list(res.reasons))
            rows.append(row)
        rows += [{"kind": s.kind, "verified": False, "probe_only": True}
                 for s in runtime_probes.probe_only_suspicions(state.user_request, out.stdout, out.exit_code)]
    except Exception:                                   # learning must never break the run it learns from
        logger.warning("experience mint failed; run continues unchanged", exc_info=True)
    return rows


def with_after_execution(node, session_id: str = ""):
    """Wrap the evaluation node: run after_execution on the post-execution state, then the node.
    The node's return value is passed through untouched; the hook only writes the experience log."""
    def _wrapped(state):
        after_execution(state, session_id)
        return node(state)
    return _wrapped
