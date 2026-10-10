"""Distilled-strategy memory ablation on Silent-Trap corpora — driver.

Question: when a de-specified strategy card distilled from the *seed* task is
injected at planning time, does the agent get the surface-different
*transfer* task right where it otherwise prints a plausible wrong number?

Silent-trap tasks exit 0 and print a wrong number, so (a) the heuristic
evaluator passes them and (b) the reactive repair_hint path never fires.
Grading is therefore gold-only: last number on stdout, |v - gold| <= tol.
Memory acts only through the planning-time card (REFORGE_EXP_DISTILLED_MEMORY).

Per replicate (pair order shuffled; all arms share the order):

    cold_seed      seed,     empty memory
    cold_transfer  transfer, empty memory
    warm_seed      seed,     empty memory, trajectory distilled → card
                   (polarity from gold: right → success prompt,
                    wrong → failure prompt with "printed X / expected Y")
    warm_within    transfer, memory = ONLY this pair's warm-seed card
    warm_cross     transfer, memory = ONLY the next pair's warm-seed card
                   (next in this replicate's shuffled order)
    warm_placebo   transfer, memory = ONE fixed generic card (PLACEBO_CARD):
                   same format and polarity header as this pair's real card,
                   no strategy content. Separates "the distilled content
                   transfers" from "any card makes the agent more careful".

One card in memory means top-1 retrieval is trivial: retrieval is held out
on purpose. This measures whether a strategy *in hand* transfers (an upper
bound on memory-content value), not whether the system retrieves the right
card. Every run gets fresh REFORGE_PROJECT_DIR / REFORGE_HOME, so the card is
the only thing carried between runs.

Models: agent = DashScope qwen-plus by default (--agent-model); distiller =
the LLM_* model in .env (deepseek-v4-pro), via REFORGE_DISTILL_*.

    # smoke
    python scripts/run_distill_ablation.py --pairs S2,M2 --replicates 1 --out runs/distill_smoke.jsonl
    # full
    python scripts/run_distill_ablation.py --replicates 5 --out runs/distill_full.jsonl
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import random
import re
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator

from dotenv import dotenv_values

_ROOT = Path(__file__).resolve().parents[1]


def _configure_models(agent_model: str) -> None:
    """Route agent LLM_* to DashScope and the distiller to .env's LLM_*.

    Must run before anything imports reforge.config (it snapshots env at
    import). load_dotenv there does not override what we set here.
    """
    env = {**dotenv_values(_ROOT / ".env"), **os.environ}
    os.environ["REFORGE_DISTILL_BASE_URL"] = env["LLM_BASE_URL"]
    os.environ["REFORGE_DISTILL_API_KEY"] = env["LLM_API_KEY"]
    os.environ["REFORGE_DISTILL_MODEL"] = env["LLM_MODEL"]
    # A DeepSeek agent model stays on .env's DeepSeek endpoint (self-
    # distillation: agent == distiller); anything else goes to DashScope.
    if not agent_model.startswith("deepseek"):
        os.environ["LLM_BASE_URL"] = env["VISION_LLM_BASE_URL"]
        os.environ["LLM_API_KEY"] = env["VISION_LLM_API_KEY"]
    os.environ["LLM_MODEL"] = agent_model
    os.environ["REFORGE_EXP_DISTILLED_MEMORY"] = "1"


# Pre-registered before the full run; never tuned against results. Generic
# diligence only — no contaminant-specific check (no summary rows, dedupe,
# key normalisation, join cardinality, sentinels, units, separators).
PLACEBO_CARD = {
    "title": "做数据任务时保持仔细",
    "description": "适用于各类读取文件并计算结果的数据处理任务。",
    "content": "- 动手前认真阅读任务要求。\n- 编写代码时保持仔细、有条理。\n"
               "- 计算完成后核对一下结果再输出。\n- 确保输出格式符合任务要求。",
    "violations": [],
}


def _placebo_preload(real: list) -> list:
    """A record carrying PLACEBO_CARD, mirroring the real card's polarity."""
    import uuid

    polarity = real[0]["distilled_strategy"].get("polarity", "failure") if real else "failure"
    base = dict(real[0]) if real else {"memory_type": "FAILURE", "user_request": ""}
    base["memory_id"] = "placebo_" + uuid.uuid4().hex[:6]
    base["distilled_strategy"] = {**PLACEBO_CARD, "polarity": polarity}
    return [base]


@dataclass
class RunRecord:
    replicate: int
    arm: str                  # cold_seed | cold_transfer | warm_seed | warm_within | warm_cross | warm_placebo
    pair_id: str
    role: str                 # seed | transfer
    case_id: str
    order_index: int
    printed: float | None
    gold: float
    tolerance: float
    passed: bool
    outcome: str
    attempts: int
    attempt_exit_codes: list[int] = field(default_factory=list)
    card_source: str = ""     # pair whose seed produced the injected card
    card_polarity: str = ""
    card: dict = field(default_factory=dict)          # injected (transfer) or produced (warm_seed)
    card_injected: bool = False                        # planner actually pulled a card
    stdout_tail: str = ""
    plan_head: str = ""
    code_tail: str = ""
    duration_ms: float = 0.0
    tokens_prompt: int = 0        # all LLM calls in the run, distiller included
    tokens_completion: int = 0
    llm_calls: int = 0
    tokens_unknown: bool = False
    error: str = ""


@contextlib.contextmanager
def _scoped(cwd: Path, **env: str) -> Iterator[None]:
    prev_env = {k: os.environ.get(k) for k in env}
    prev_cwd = os.getcwd()
    os.environ.update(env)
    os.chdir(cwd)
    try:
        yield
    finally:
        os.chdir(prev_cwd)
        for k, v in prev_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def last_number(stdout: str) -> float | None:
    """Gold-checker input: the LAST number printed (comma_trap number regex)."""
    for raw in reversed(_NUMBER_RE.findall(stdout or "")):
        try:
            return float(raw.replace(",", ""))
        except ValueError:
            continue
    return None


def _forbidden_terms(case, data_dir: Path) -> tuple[str, ...]:
    terms: set[str] = set()
    for f in data_dir.iterdir():
        terms.update({f.name, f.stem})
        with open(f, encoding="utf-8") as fh:
            terms.update(h.strip() for h in fh.readline().strip().split(","))
    terms.update(re.findall(r"'([^']+)'", case.request))
    return tuple(sorted(t for t in terms if len(t) >= 2))


def run_one(case, *, pair_id: str, role: str, arm: str, replicate: int,
            order_index: int, run_dir: Path, preload: list | None,
            distill: bool, card_source: str = "") -> tuple[RunRecord, list]:
    """Run *case* once in an isolated dir. Returns (record, records written)."""
    from reforge.benchmark.runner import _CountingSubstrate
    from reforge.memory.models import MemoryRecord
    from reforge.memory.store import MemoryStore
    from reforge.memory.substrate import CompositeMemorySubstrate
    from reforge.memory.writer import DistillLabel, distill_label_oracle
    from reforge.runtime.orchestration.engine.runner import RuntimeRunner

    data_dir = run_dir / "data"
    case.build(str(data_dir))           # sets case.gold deterministically
    store = MemoryStore(base_dir=run_dir / "sub")
    for rec in preload or []:
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
    forbidden = _forbidden_terms(case, data_dir)

    def oracle(state) -> DistillLabel:
        v = last_number(getattr(state.exec_state, "stdout", "") or "")
        ok = v is not None and abs(v - case.gold) <= case.tolerance
        return DistillLabel(
            correct=ok,
            feedback=f"打印值 {v},正确值 {case.gold}" + ("" if ok else "(错误)"),
            forbidden_terms=forbidden,
        )

    from reforge.observability.llm_events import token_accounting

    token = distill_label_oracle.set(oracle if distill else None)
    final = None
    error = ""
    ledger = None
    start = time.perf_counter()
    try:
        with _scoped(data_dir,
                     REFORGE_PROJECT_DIR=str(run_dir / "proj"),
                     REFORGE_HOME=str(run_dir / "home")), \
                token_accounting(case_id=case.id, seed=replicate) as ledger:
            runner = RuntimeRunner(memory_substrate=counting)
            for _node, state in runner.stream(case.request):
                final = state
    except Exception as exc:  # harness failure, not a task failure
        error = f"{type(exc).__name__}: {exc}"
    finally:
        distill_label_oracle.reset(token)
    duration_ms = (time.perf_counter() - start) * 1000

    written = [r.model_dump() for r in store.list_all() if r.distilled_strategy
               and r.memory_id not in {p["memory_id"] for p in preload or []}]

    stdout = (getattr(final.exec_state, "stdout", "") or "") if final else ""
    v = last_number(stdout)
    passed = v is not None and abs(v - case.gold) <= case.tolerance
    injected_card = (preload[0]["distilled_strategy"] if preload else
                     (written[0]["distilled_strategy"] if written else {}))
    rec = RunRecord(
        replicate=replicate, arm=arm, pair_id=pair_id, role=role, case_id=case.id,
        order_index=order_index, printed=v, gold=case.gold, tolerance=case.tolerance,
        passed=passed,
        outcome=str(final.outcome_state.task_outcome) if final else "HARNESS_ERROR",
        attempts=(final.control_state.retry_count + 1) if final else 0,
        # attempts may be AttemptRecord models or plain dicts (execution node)
        attempt_exit_codes=[
            int((a.get("exit_code") if isinstance(a, dict) else getattr(a, "exit_code", 0)) or 0)
            for a in final.attempts
        ] if final else [],
        card_source=card_source,
        card_polarity=injected_card.get("polarity", ""),
        card=injected_card,
        card_injected=counting.distilled_hits > 0,
        stdout_tail=stdout[-300:],
        plan_head=((final.semantic_state.plan or "") if final else "")[:6000],
        code_tail=((final.generated_code or "") if final else "")[-8000:],
        duration_ms=round(duration_ms, 1),
        tokens_prompt=ledger.prompt_tokens if ledger else 0,
        tokens_completion=ledger.completion_tokens if ledger else 0,
        llm_calls=ledger.calls if ledger else 0,
        tokens_unknown=bool(ledger.unknown) if ledger else True,
        error=error,
    )
    return rec, written


def _fill_plan(src_rows: list, rep: int, pairs: list, merged_seed: bool) -> set:
    """(arm, case_id) runs of replicate *rep* that need rerunning.

    Seeds are never rerun — a new seed run would distil a new card and break
    pairing with the transfer runs that already used the old one.
    """
    ok = {(r["arm"], r["case_id"]) for r in src_rows
          if r["replicate"] == rep and not r["error"]}
    seed_arm = "seed" if merged_seed else "warm_seed"
    for p in pairs:
        if (seed_arm, p.seed.id) not in ok:
            sys.exit(f"rep{rep} {p.pair_id}: seed run missing/errored — cannot gap-fill, rerun the replicate")
    want = {("cold_transfer", p.transfer.id) for p in pairs}
    want |= {(a, p.transfer.id) for p in pairs for a in ("warm_within", "warm_cross", "warm_placebo")}
    if not merged_seed:
        want |= {("cold_seed", p.seed.id) for p in pairs}
    return want - ok


def src_cards(src_rows: list, rep: int, pair) -> list:
    """Rebuild the seed's memory record from the card stored on its run record."""
    for r in src_rows:
        if (r["replicate"] == rep and r["case_id"] == pair.seed.id
                and r["arm"] in ("seed", "warm_seed") and not r["error"] and r["card"]):
            card = r["card"]
            return [{
                "memory_id": f"seedcard_{rep}_{pair.pair_id}",
                "memory_type": "SUCCESS_PATTERN" if card.get("polarity") == "success" else "FAILURE",
                "user_request": pair.seed.request,
                "distilled_strategy": card,
            }]
    return []


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pairs", default="", help="comma list, e.g. S2,M2 (default: all 10)")
    ap.add_argument("--replicates", type=int, default=5)
    ap.add_argument("--rep-offset", type=int, default=0, help="first replicate index (for sharding)")
    ap.add_argument("--agent-model", default="qwen-plus")
    ap.add_argument("--out", default="runs/distill_ablation.jsonl")
    ap.add_argument("--keep-tmp", action="store_true")
    ap.add_argument("--merged-seed", action="store_true",
                    help="run the seed once (arm 'seed') and distil from it, instead of "
                         "separate cold_seed + warm_seed runs under identical conditions")
    ap.add_argument("--fill-from", default="",
                    help="gap-fill: rerun only the runs that errored (or are absent) in this "
                         "JSONL, reusing its seed cards so results stay paired with it")
    args = ap.parse_args()

    _configure_models(args.agent_model)
    from reforge.benchmark.silent_trap_cases import SILENT_PAIRS
    from reforge.benchmark.silent_trap_multistep_cases import MULTISTEP_PAIRS

    pairs = list(SILENT_PAIRS) + list(MULTISTEP_PAIRS)
    if args.pairs:
        want = [p.strip() for p in args.pairs.split(",") if p.strip()]
        by_id = {p.pair_id: p for p in pairs}
        pairs = [by_id[w] for w in want]
    if len(pairs) < 2:
        sys.exit("need >= 2 pairs for the cross-pair arm")

    src_rows = []
    if args.fill_from:
        src_rows = [json.loads(x) for x in Path(args.fill_from).read_text(encoding="utf-8").splitlines()
                    if x.strip()]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="reforge_distill_"))
    print(f"agent={os.environ['LLM_MODEL']} distiller={os.environ['REFORGE_DISTILL_MODEL']}")
    print(f"isolation root: {root}", flush=True)

    def emit(fh, rec: RunRecord) -> None:
        fh.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
        fh.flush()
        flag = "PASS" if rec.passed else "FAIL"
        extra = f" card={rec.card_source}:{rec.card_polarity} inj={rec.card_injected}" if rec.card_source else ""
        print(f"  [rep{rec.replicate} {rec.arm:13s} {rec.case_id:28s}] {flag} printed={rec.printed} "
              f"gold={rec.gold} attempts={rec.attempts}{extra} ({rec.duration_ms / 1000:.0f}s)"
              + (f" ERR {rec.error}" if rec.error else ""), flush=True)

    try:
        with open(out, "a", encoding="utf-8") as fh:
            for rep in range(args.rep_offset, args.rep_offset + args.replicates):
                order = list(pairs)
                random.Random(2000 + rep).shuffle(order)
                print(f"\nreplicate {rep}: {' → '.join(p.pair_id for p in order)}", flush=True)
                rdir = root / f"rep{rep}"
                cards: dict[str, list] = {}
                n = 0
                todo = _fill_plan(src_rows, rep, pairs, args.merged_seed) if src_rows else None

                def go(case, **kw):
                    nonlocal n
                    n += 1      # counts skipped runs too: order_index matches the source
                    if todo is not None and (kw["arm"], case.id) not in todo:
                        return None, []
                    rec, written = run_one(case, replicate=rep, order_index=n,
                                           run_dir=rdir / f"{n:03d}_{kw['arm']}_{case.id}", **kw)
                    emit(fh, rec)
                    return rec, written

                for p in order:
                    if not args.merged_seed:
                        go(p.seed, pair_id=p.pair_id, role="seed", arm="cold_seed", preload=None, distill=False)
                    go(p.transfer, pair_id=p.pair_id, role="transfer", arm="cold_transfer", preload=None, distill=False)
                    _, written = go(p.seed, pair_id=p.pair_id, role="seed",
                                    arm="seed" if args.merged_seed else "warm_seed",
                                    preload=None, distill=True)
                    if todo is not None:
                        written = src_cards(src_rows, rep, p)
                    cards[p.pair_id] = written[:1]
                    if not written:
                        print(f"  !! no card distilled for {p.pair_id} seed", flush=True)
                for i, p in enumerate(order):
                    other = order[(i + 1) % len(order)]
                    go(p.transfer, pair_id=p.pair_id, role="transfer", arm="warm_within",
                       preload=cards[p.pair_id], distill=False, card_source=p.pair_id)
                    go(p.transfer, pair_id=p.pair_id, role="transfer", arm="warm_cross",
                       preload=cards[other.pair_id], distill=False, card_source=other.pair_id)
                    go(p.transfer, pair_id=p.pair_id, role="transfer", arm="warm_placebo",
                       preload=_placebo_preload(cards[p.pair_id]), distill=False,
                       card_source="placebo")
        print(f"\nrecords → {out}")
    finally:
        if args.keep_tmp:
            print(f"isolation dirs kept at {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
