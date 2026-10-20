"""Constraint cards for AppWorld: pagination probe, verifier, distiller, gated bank.

Gold-free throughout. The evidence is AppWorld's own request log
(api_calls.jsonl, what the agent asked for) plus read-only re-calls of the same
list endpoint in the same world (what the next page holds). Nothing here
imports appworld_gold or calls the evaluate endpoint.

    probe     the agent ended with complete_task, and a paginated GET's last page read was full (len == page_limit)
    verify    re-read that page and the next one; next page non-empty -> confirmed "did not read all pages"
    distill   LLM turns the confirmed evidence into {trigger, guardrail}; lint keeps it abstract
    CardBank  admit() refuses any card whose source failure was not verifier-confirmed
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from appworld_client import AppWorldClient

APPWORLD_ROOT = Path(os.environ.get("APPWORLD_ROOT", r"D:\appworld_root"))
DOCS = APPWORLD_ROOT / "data" / "api_docs" / "standard"
_PAGING = {"page_index", "page_limit"}


def paginated_endpoints() -> list[dict]:
    """Every GET API whose doc has page_index: {app, api, regex over the logged url, default page_limit}."""
    eps = []
    for f in sorted(DOCS.glob("*.json")):
        app = f.stem
        for api, d in json.loads(f.read_text(encoding="utf-8")).items():
            params = {p["name"]: p for p in d.get("parameters", [])}
            if d.get("method", "").upper() != "GET" or "page_index" not in params:
                continue
            rx = re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", d["path"])
            eps.append({"app": app, "api": api, "rx": re.compile(f"^{rx}$"),
                        "default_limit": params.get("page_limit", {}).get("default") or 5})
    return eps


def read_api_calls(client: AppWorldClient, task_id: str, experiment: str) -> list[dict]:
    client.save_logs(task_id)
    p = APPWORLD_ROOT / "experiments" / "outputs" / experiment / "tasks" / task_id / "logs" / "api_calls.jsonl"
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def list_reads(calls: list[dict], eps: list[dict] | None = None) -> list[dict]:
    """Group the agent's paginated GETs by signature (params minus paging and token)."""
    eps = eps if eps is not None else paginated_endpoints()
    groups: dict[str, dict] = {}
    for c in calls:
        if c["method"].lower() != "get":
            continue
        for e in eps:
            m = e["rx"].match(c["url"])
            if not m:
                continue
            data = dict(c.get("data") or {})
            params = {**m.groupdict(), **{k: v for k, v in data.items() if k not in _PAGING}}
            sig = json.dumps([e["app"], e["api"], {k: v for k, v in params.items() if k != "access_token"}], sort_keys=True)
            g = groups.setdefault(sig, {"app": e["app"], "api": e["api"], "params": params, "pages": set(), "limits": set()})
            g["pages"].add(int(data.get("page_index") or 0))
            g["limits"].add(int(data.get("page_limit") or e["default_limit"]))
            break
    return [{**g, "pages": sorted(g["pages"]), "limits": sorted(g["limits"])} for g in groups.values()]


def behaviour_markers(reads: list[dict]) -> dict:
    return {"paginated_reads": len(reads),
            "read_beyond_page0": any(max(r["pages"]) >= 1 for r in reads),
            "used_sort_by": any("sort_by" in r["params"] for r in reads),
            "reads": [{"api": f"{r['app']}.{r['api']}", "pages": r["pages"], "limits": r["limits"],
                       "sort_by": r["params"].get("sort_by")} for r in reads]}


def verify(client: AppWorldClient, task_id: str, reads: list[dict], completed: bool) -> list[dict]:
    """Probe + verifier. Read-only re-calls in the still-open world; returns confirmed evidence rows."""
    if not completed:
        return []
    confirmed = []
    for r in reads:
        last, limit = max(r["pages"]), max(r["limits"])
        kw = ", ".join(f"{k}={v!r}" for k, v in r["params"].items())
        code = (f"import json\n_f = apis.{r['app']}.{r['api']}\n"
                f"print(json.dumps([len(_f({kw}, page_index={last}, page_limit={limit})), "
                f"len(_f({kw}, page_index={last + 1}, page_limit={limit}))]))")
        out = client.execute(task_id, code)
        try:
            this_len, next_len = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            continue
        if this_len == limit and next_len > 0:          # probe: full page; verifier: more pages exist
            confirmed.append({"kind": "list API", "last_page_read": last, "page_limit": limit,
                              "items_on_that_page": this_len, "next_page_items": next_len, "agent_finished": True})
    return confirmed


DISTILL_PROMPT = """An agent works in a simulated world of apps through Python API calls. In one episode it submitted
its final answer, and a verifier then confirmed the following about the list/search API calls it relied on:
{evidence}
Write ONE reusable constraint card that would prevent this class of mistake in any future task in this environment.
Rules:
- abstract: do not name any app, API, function, entity, id or value from the episode;
- concrete anchor: state the default page size ({limit}) as a number and use the parameter names page_limit and page_index;
- checkable trigger: the card must fire on something the agent can see, i.e. a call returning exactly page_limit
  (default {limit}) items, and say to keep requesting page_index+1 until a call returns fewer than page_limit items
  before aggregating or answering;
- state what to do, not code.
Return JSON only: {{"trigger": "<when the card applies>", "guardrail": "<what to do>"}}"""


def _lint(card: dict, banned: set[str], required: set[str]) -> list[str]:
    text = f"{card.get('trigger', '')} {card.get('guardrail', '')}".lower()
    def has(w: str) -> bool:
        return re.search(r"(?<![\w])" + re.escape(w.lower()) + r"(?![\w])", text) is not None

    bad = [w for w in banned if has(w)]
    bad += [f"<missing {w}>" for w in required if not has(w)]
    if not card.get("trigger") or not card.get("guardrail"):
        bad.append("<missing field>")
    return bad


def distill(evidence: list[dict], model: str | None = None) -> tuple[dict | None, list[dict]]:
    """→ (card or None, attempts). Banned: every app and API name in the docs. Required: the page size, page_index."""
    from openai import OpenAI

    banned = {f.stem for f in DOCS.glob("*.json")} | {e["api"] for e in paginated_endpoints()}
    limit = evidence[0]["page_limit"]
    required = {str(limit), "page_index", "page_limit"}
    c = OpenAI(base_url=os.environ["LLM_BASE_URL"], api_key=os.environ["LLM_API_KEY"], timeout=300)
    model = model or os.environ["LLM_MODEL"]
    prompt = DISTILL_PROMPT.format(evidence=json.dumps(evidence, indent=1), limit=limit)
    attempts = []
    for _ in range(2):
        r = c.chat.completions.create(model=model, temperature=0, messages=[{"role": "user", "content": prompt}])
        raw = r.choices[0].message.content or ""
        m = re.search(r"\{.*\}", raw, flags=re.S)
        try:
            card = json.loads(m.group(0)) if m else {}
        except ValueError:
            card = {}
        bad = _lint(card, banned, required)
        attempts.append({"raw": raw, "lint": bad, "prompt_tokens": r.usage.prompt_tokens,
                         "completion_tokens": r.usage.completion_tokens})
        if not bad:
            return {"trigger": card["trigger"], "guardrail": card["guardrail"]}, attempts
    return None, attempts


class CardBank:
    """Env-wide cards. Recall = every card whose scope matches (pagination is an environment-wide convention)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.cards: list[dict] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

    def admit(self, card: dict, evidence: list[dict], source_task: str) -> dict:
        if not evidence:
            raise ValueError("verifier-gated bank: no confirmed failure behind this card")
        row = {"id": f"card{len(self.cards) + 1}", "scope": "appworld", **card,
               "source_task": source_task, "evidence": evidence}
        self.cards.append(row)
        self.path.write_text(json.dumps(self.cards, ensure_ascii=False, indent=1), encoding="utf-8")
        return row

    def recall(self, scope: str = "appworld") -> list[dict]:
        return [c for c in self.cards if c["scope"] == scope]
