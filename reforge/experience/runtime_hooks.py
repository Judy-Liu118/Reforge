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
CANARY_ENV = "REFORGE_EXPERIENCE_CANARY"      # comma-separated quarantined card ids, canary arms only

# Observers called after every after_execution with (state, rows) — observability for measurement
# harnesses (probe precision / recall). They see what the hook saw; they cannot change the run.
PROBE_OBSERVERS: list[Callable] = []

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
    # judgment-confirmed kinds (L2 critic)
    "coerce_drop": {
        "trigger": "When the code converts values with errors='coerce' (to_numeric, to_datetime) before aggregating.",
        "guardrail": "Coerce turns every unparseable value into a missing value silently. First clean the formats "
                     "the data actually uses (thousand separators, currency symbols, units, whitespace), then "
                     "convert, and check how many values still became missing before trusting the result.",
    },
    "suspicious_output": {
        "trigger": "When the printed result is empty, NaN or infinite.",
        "guardrail": "Treat it as a failed computation, not an answer: find the step that produced no data "
                     "(an empty filter, a failed parse, a division by zero) and fix it before reporting.",
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


@lru_cache(maxsize=8)
def _loop_for(path: str, canary: str = "") -> ExperienceLoop:
    return ExperienceLoop(ExperienceConfig(log_path=Path(path), env=ENV, enabled=True,
                                           canary_card_ids=frozenset(x for x in canary.split(",") if x),
                                           required_anchors={"swallowed_exception": frozenset({"raise"}),
                                                             "missing_return": frozenset({"return"}),
                                                             "coerce_drop": frozenset({"coerce"}),
                                                             "suspicious_output": frozenset({"nan"})}))


@lru_cache(maxsize=1)
def _critic():
    """The L2 critic from env, or None when misconfigured (e.g. same model as the generator) — logged once."""
    from reforge.experience.critic import Critic
    try:
        return Critic.from_env()
    except ValueError as e:
        logger.warning("experience critic disabled: %s", e)
        return None


def runtime_loop() -> ExperienceLoop:
    return _loop_for(str(_log_path()), os.environ.get(CANARY_ENV, ""))


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
        suspicions = runtime_probes.probe_only_suspicions(state.user_request, out.stdout, out.exit_code, code)
        rows += [{"kind": s.kind, "verified": False, "probe_only": True} for s in suspicions]
        if suspicions:
            judged = _critic_tier(loop, t, state, code, out, suspicions, distill)
            if judged:
                rows.append(judged)
    except Exception:                                   # learning must never break the run it learns from
        logger.warning("experience mint failed; run continues unchanged", exc_info=True)
    for obs in PROBE_OBSERVERS:
        try:
            obs(state, rows)
        except Exception:                               # an observer must never break the run either
            logger.warning("experience probe observer failed", exc_info=True)
    return rows


def _critic_tier(loop, t, state, code, out, suspicions, distill) -> dict | None:
    """L2: one critic call per round, only on deterministic-unconfirmable signals, only when its flag is on."""
    from reforge.experience import critic as critic_mod
    if not critic_mod.enabled():
        return None
    crit = _critic()
    if crit is None:
        return None
    kinds = {s.kind for s in suspicions}
    kind = "coerce_drop" if "coerce_drop" in kinds else "suspicious_output"
    profile = critic_mod.profile_text(critic_mod.data_paths(state.user_request))
    j = crit.judge(state.user_request, profile, code, out.stdout)
    loop.store.record_judgment(task_id=t.task_id, run_id=t.run_id, signals=sorted(kinds), failure_kind=kind,
                               model=j.model, verdict=j.verdict, confidence=j.confidence, confirmed=j.confirmed,
                               suspect=j.suspect, prompt_tokens=j.prompt_tokens, completion_tokens=j.completion_tokens,
                               prompt_sha=critic_mod.PROMPT_SHA, error=j.error, parse_ok=j.parse_ok)
    row = {"kind": kind, "tier": "llm_critic", "verdict": j.verdict, "confidence": j.confidence,
           "verified": j.confirmed, "tokens": j.total_tokens}
    if j.confirmed:
        f = VerifiedFailure(kind, "llm_critic", ({"kind": kind, "confirmation": "judgment", "signals": sorted(kinds),
                                                  "critic_model": j.model, "confidence": j.confidence,
                                                  "prompt_sha": critic_mod.PROMPT_SHA},),
                            source_task=t.task_id, source_run=t.run_id)
        res = loop.mint(f, distill)
        row.update(minted=res.card.card_id if res.card else None, reasons=list(res.reasons))
    return row


def with_after_execution(node, session_id: str = ""):
    """Wrap the evaluation node: run after_execution on the post-execution state, then the node.
    The node's return value is passed through untouched; the hook only writes the experience log."""
    def _wrapped(state):
        after_execution(state, session_id)
        return node(state)
    return _wrapped
