"""Cheap gold-free probes + verifiers that produce VerifiedFailure.

Environment-agnostic: the host passes its request log / step records and a
read-only callback into its environment. Evidence is redacted (shapes and
counts, never values), because it is what the distiller sees.

    pagination      probe: the agent finished, and a paginated read's last page was full
                    (len == page_limit). verifier: the next page is non-empty.
    login identity  probe: a login(...) call in the episode raised (L0).
                    verifier: replaying identity forms shows which fail and which works.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from reforge.experience.cards import VerifiedFailure

_PAGING = {"page_index", "page_limit"}


@dataclass(frozen=True)
class Endpoint:
    app: str
    api: str
    pattern: re.Pattern
    default_limit: int = 5


@dataclass(frozen=True)
class PaginatedRead:
    app: str
    api: str
    params: dict                  # without paging args; may carry the host's auth token for re-calls
    pages: tuple[int, ...]
    limit: int


def paginated_reads(calls: list[dict], endpoints: list[Endpoint], *, token_keys: tuple[str, ...] = ("access_token",)
                    ) -> list[PaginatedRead]:
    """Group GET calls to paginated endpoints by signature (params minus paging and auth)."""
    groups: dict[str, dict] = {}
    for c in calls:
        if c.get("method", "").lower() != "get":
            continue
        for e in endpoints:
            m = e.pattern.match(c["url"])
            if not m:
                continue
            data = dict(c.get("data") or {})
            params = {**m.groupdict(), **{k: v for k, v in data.items() if k not in _PAGING}}
            sig = json.dumps([e.app, e.api, {k: v for k, v in params.items() if k not in token_keys}], sort_keys=True)
            g = groups.setdefault(sig, {"app": e.app, "api": e.api, "params": params, "pages": set(), "limit": 0})
            g["pages"].add(int(data.get("page_index") or 0))
            g["limit"] = max(g["limit"], int(data.get("page_limit") or e.default_limit))
            break
    return [PaginatedRead(g["app"], g["api"], g["params"], tuple(sorted(g["pages"])), g["limit"]) for g in groups.values()]


def confirm_incomplete_reads(reads: list[PaginatedRead], refetch: Callable[[PaginatedRead, int, int], int], *,
                             agent_finished: bool, source_task: str, source_run: str = "") -> VerifiedFailure | None:
    """refetch(read, page_index, page_limit) -> number of items (read-only call in the same world)."""
    if not agent_finished:
        return None
    ev = []
    for r in reads:
        last = max(r.pages)
        this_len, next_len = refetch(r, last, r.limit), refetch(r, last + 1, r.limit)
        if this_len == r.limit and next_len > 0:
            ev.append({"kind": "list API", "last_page_read": last, "page_limit": r.limit,
                       "items_on_that_page": this_len, "next_page_items": next_len, "agent_finished": True})
    return VerifiedFailure("pagination", "next_page_nonempty", tuple(ev), source_task, source_run) if ev else None


_LOGIN = re.compile(r"apis\.(\w+)\.login\((\w+)=")


def login_failures(steps: list[dict]) -> list[dict]:
    """Steps whose code called <app>.login(...) and whose output reports a failure (L0)."""
    out = []
    for s in steps:
        m = _LOGIN.search(s.get("code", ""))
        if m and str(s.get("output", "")).startswith("Execution failed"):
            out.append({"step": s.get("step"), "app": m.group(1), "param": m.group(2)})
    return out


def confirm_login_identity(app: str, replay: dict[str, str], *, source_task: str,
                           source_run: str = "") -> VerifiedFailure | None:
    """replay: identity form label (values redacted, e.g. 'username=<account email>') -> 'ok' | 'error <code>'."""
    if any(v == "ok" for v in replay.values()) and any(v != "ok" for v in replay.values()):
        return VerifiedFailure("login_identity", "identity_replay",
                               ({"kind": "login", "app": app, "results": dict(replay)},), source_task, source_run)
    return None
