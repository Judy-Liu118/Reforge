"""Real agent / distiller / task streams for scripts/run_online_loop.py.

RuntimeAgent is run_distill_ablation.run_one with the gold removed: it runs
the unmodified RuntimeRunner in an isolated dir, hands the planner the cards
the loop chose (via recall_distilled), and returns the trajectory. It never
scores the answer. RuntimeDistiller calls writer.distill_strategy unmodified,
with polarity "wrong" and the verifier's diagnosis as feedback.

Canary split (fixed here, before the first stream task runs, so the stream
never touches a canary table):
    8 trap cases on hotel_data / titanic / auto-mpg (trap types all also
      present in the stream, on other tables)
    4 natural InfiAgent questions: easy/medium, lowest ids, distinct tables
      outside every trap table
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from reforge.online.loop import Trajectory  # noqa: E402
from reforge.online.tasks import (  # noqa: E402
    bird_tasks, csv_dir_tasks, infiagent_tasks, last_number, split_by_table, trap_tasks,
)

CANARY_TRAP_TABLES = frozenset({"hotel_data.csv", "titanic.csv", "auto-mpg.csv"})
CANARY_NATURAL_N = 4


def configure(agent_model: str, thinking_budget: int) -> None:
    """Model routing as in run_learning_loop.py; must run before reforge.config is imported."""
    import run_distill_ablation as D
    D._configure_models(agent_model)          # also sets REFORGE_EXP_DISTILLED_MEMORY=1

    import reforge.models.adapters.llm_client as _lc
    real, agent_base = _lc.OpenAI, os.environ["LLM_BASE_URL"]

    def capped(*a, **k):
        if k.get("base_url") != agent_base or not thinking_budget:
            return real(*a, **k)
        client = real(*a, **{**k, "timeout": 300.0, "max_retries": 1})
        orig = client.chat.completions.create

        def create(*ca, **ck):
            eb = dict(ck.pop("extra_body", None) or {})
            eb.update(enable_thinking=True, thinking_budget=thinking_budget)
            return orig(*ca, extra_body=eb, **ck)
        client.chat.completions.create = create
        return client
    _lc.OpenAI = capped


def forbidden_terms(request: str, data_dir: Path) -> tuple[str, ...]:
    """Surface tokens a card must not carry: file names, CSV headers, quoted request terms."""
    terms: set[str] = set()
    for f in Path(data_dir).iterdir():
        terms.update({f.name, f.stem})
        if f.suffix.lower() == ".csv":
            with open(f, encoding="utf-8", errors="replace") as fh:
                terms.update(h.strip() for h in fh.readline().strip().split(","))
    terms.update(re.findall(r"'([^']+)'", request))
    return tuple(sorted(t for t in terms if len(t) >= 2))


class RuntimeAgent:
    def run(self, task, data_dir: Path, run_dir: Path, cards: list[dict]) -> Trajectory:
        from run_distill_ablation import _scoped

        from reforge.benchmark.runner import _CountingSubstrate
        from reforge.memory.models import MemoryRecord
        from reforge.memory.store import MemoryStore
        from reforge.memory.substrate import CompositeMemorySubstrate
        from reforge.observability.llm_events import token_accounting
        from reforge.runtime.orchestration.engine.runner import RuntimeRunner

        store = MemoryStore(base_dir=run_dir / "sub")
        for rec in cards:
            store.save(MemoryRecord.model_validate(rec))
        inner = CompositeMemorySubstrate(store)

        class _Counting(_CountingSubstrate):
            distilled_hits = 0

            def recall_distilled(self, *a, **k):
                out = inner.recall_distilled(*a, **k)
                self.distilled_hits += len(out)
                return out

            def recall_repair_pattern(self, *a, **k):
                return inner.recall_repair_pattern(*a, **k)

            def find_by_error(self, *a, **k):
                return inner.find_by_error(*a, **k)

        counting = _Counting(inner)
        terms = forbidden_terms(task.request, data_dir)
        final, error, ledger = None, "", None
        start = time.perf_counter()
        try:
            with _scoped(data_dir, REFORGE_PROJECT_DIR=str(run_dir / "proj"), REFORGE_HOME=str(run_dir / "home")), \
                    token_accounting(case_id=task.id, seed=0) as ledger:
                for _node, state in RuntimeRunner(memory_substrate=counting).stream(task.request):
                    final = state
        except Exception as exc:  # harness failure, not a task failure
            error = f"{type(exc).__name__}: {exc}"
        stdout = (getattr(final.exec_state, "stdout", "") or "") if final else ""
        return Trajectory(
            stdout_tail=stdout[-2000:],
            code_tail=((final.generated_code or "") if final else "")[-8000:],
            printed=last_number(stdout),
            outcome=str(final.outcome_state.task_outcome) if final else "HARNESS_ERROR",
            plan_head=((final.semantic_state.plan or "") if final else "")[:6000],
            attempts=(final.control_state.retry_count + 1) if final else 0,
            exit_codes=[int((a.get("exit_code") if isinstance(a, dict) else getattr(a, "exit_code", 0)) or 0)
                        for a in final.attempts] if final else [],
            error_hint=((final.traceback or "") if final else "")[-300:],
            card_injected=counting.distilled_hits > 0,
            forbidden_terms=terms,
            tokens_prompt=ledger.prompt_tokens if ledger else 0,
            tokens_completion=ledger.completion_tokens if ledger else 0,
            llm_calls=ledger.calls if ledger else 0,
            duration_ms=round((time.perf_counter() - start) * 1000, 1),
            error=error or ("" if final else "graph produced no state"),
            state=final,
        )


class RuntimeDistiller:
    def __init__(self) -> None:
        self.model = os.environ.get("REFORGE_DISTILL_MODEL", "")

    def distill(self, traj: Trajectory, feedback: str, forbidden: tuple[str, ...]) -> tuple[dict, int]:
        import reforge.memory.writer as W
        from reforge.observability.llm_events import token_accounting

        with token_accounting(case_id="distill", seed=0) as led:
            card = W.distill_strategy(traj.state, W.DistillLabel(correct=False, feedback=feedback,
                                                                 forbidden_terms=forbidden))
        return card, led.prompt_tokens + led.completion_tokens


# ---------------------------------------------------------------------------
# Streams + canary split
# ---------------------------------------------------------------------------

def _trap_cases():
    from learning_loop_cases import TRAPS
    return [c for cs in TRAPS.values() for c in cs]


def canary_and_stream_sources(infiagent_dir: Path, *, csv_dir: Path | None = None,
                              bird_dir: Path | None = None, bird_limit: int = 3) -> tuple[list, dict, set]:
    """(canary pairs, {source: stream pairs}, holdout tables)."""
    traps = trap_tasks(_trap_cases())
    trap_tables = {t for s, _ in traps for t in s.tables}
    natural = sorted(infiagent_tasks(Path(infiagent_dir), levels={"easy", "medium"}), key=lambda p: p[0].id)
    canary_nat, seen = [], set()
    for p in natural:
        t = p[0].tables[0]
        if t not in trap_tables and t not in seen:
            canary_nat.append(p)
            seen.add(t)
        if len(canary_nat) == CANARY_NATURAL_N:
            break
    holdout = set(CANARY_TRAP_TABLES) | seen
    trap_stream, canary_trap = split_by_table(traps, holdout)
    nat_stream, _ = split_by_table(infiagent_tasks(Path(infiagent_dir)), holdout)
    sources = {"trap": trap_stream, "infiagent": nat_stream}
    if csv_dir:
        sources["csv"] = split_by_table(csv_dir_tasks(Path(csv_dir)), holdout)[0]
    if bird_dir:
        sources["bird"] = bird_tasks(Path(bird_dir), limit=bird_limit)
    return canary_trap + canary_nat, sources, holdout
