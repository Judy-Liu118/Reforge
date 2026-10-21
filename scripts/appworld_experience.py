"""AppWorld adapter onto reforge.experience — the experiment scripts now mint, gate, store and recall
through the production component instead of their own card bank.

What stays AppWorld-specific (here and in appworld_cards.py): reading AppWorld's request log, the
read-only re-calls / login replays inside a world, and the LLM distillers. Everything the component
owns — VerifiedFailure, the gate, quarantine → canary → promote, scope recall, rendering — is the
component's. Gold-free: nothing here imports appworld_gold.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import appworld_cards as ac  # noqa: E402
from appworld_client import AppWorldClient  # noqa: E402
from reforge.experience import ExperienceConfig, ExperienceLoop, TaskContext, VerifiedFailure  # noqa: E402
from reforge.experience import probes  # noqa: E402

ENV = "appworld"
BANK = Path(__file__).resolve().parents[1] / "runs" / "appworld_experience" / "loop.jsonl"   # one bank per env
APPS = ("amazon", "file_system", "gmail", "phone", "simple_note", "splitwise", "spotify", "todoist", "venmo")


def endpoints() -> list[probes.Endpoint]:
    return [probes.Endpoint(e["app"], e["api"], e["rx"], e["default_limit"]) for e in ac.paginated_endpoints()]


def make_loop(log_path: Path) -> ExperienceLoop:
    """The gate settings the experiments pre-registered, expressed as component config."""
    api_names = frozenset(e.api for e in endpoints())
    return ExperienceLoop(ExperienceConfig(
        log_path=log_path, env=ENV, known_apps=APPS, enabled=True,          # experiments opt in explicitly
        banned_terms=api_names,
        banned_by_kind={"pagination": frozenset(APPS)},                     # login cards may name the phone app
        required_anchors={"pagination": frozenset({"5", "page_index", "page_limit"}),
                          "login_identity": frozenset({"username"})}))


def pagination_failure(client: AppWorldClient, task_id: str, calls: list[dict], *, finished: bool,
                       source_run: str = "") -> VerifiedFailure | None:
    """Component probe + verifier; the refetch is a read-only call in the still-open world `task_id`."""
    def refetch(r: probes.PaginatedRead, page: int, limit: int) -> int:
        kw = ", ".join(f"{k}={v!r}" for k, v in r.params.items())
        out = client.execute(task_id, f"print(len(apis.{r.app}.{r.api}({kw}, page_index={page}, page_limit={limit})))")
        try:
            return int(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return -1
    return probes.confirm_incomplete_reads(probes.paginated_reads(calls, endpoints()), refetch,
                                           agent_finished=finished, source_task=task_id, source_run=source_run)


def login_failure(client: AppWorldClient, task_id: str, app: str, steps: list[dict],
                  experiment: str) -> VerifiedFailure | None:
    """L0 probe on the recorded steps, then replay of the identity forms in a fresh world."""
    if not [f for f in probes.login_failures(steps) if f["app"] == app]:
        return None
    client.initialize(task_id, experiment, timeout_seconds=None)
    try:
        out = client.execute(task_id, "import re\n" + ac._LOGIN_PROBE.format(app=app))
    finally:
        client.close(task_id)
    try:
        replay = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    return probes.confirm_login_identity(app, replay, source_task=task_id)


def llm_distiller(failure: VerifiedFailure) -> dict:
    """The experiments' LLM distillers (deepseek), unchanged; one attempt per call (the loop retries)."""
    fn = ac.distill if failure.kind == "pagination" else ac.distill_login
    card, attempts = fn(list(failure.evidence))
    return card or json.loads(re.search(r"\{.*\}", attempts[-1]["raw"], flags=re.S).group(0))


def task(task_id: str, instruction: str, run_id: str = "") -> TaskContext:
    return TaskContext(task_id=task_id, env=ENV, instruction=instruction, run_id=run_id)


def read_steps(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
