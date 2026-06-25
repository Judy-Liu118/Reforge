"""Tests for the web dashboard (P4)."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest

from reforge.observability.dashboard import DashboardServer
from reforge.observability.dashboard.server import (
    _load_memory_records,
    _skills_payload,
)
from reforge.runtime.events.log import ExecutionEventLog
from reforge.runtime.events.models import (
    execution_failed,
    execution_started,
    execution_succeeded,
    policy_decided,
    task_completed,
)
from reforge.runtime.skills.registry import SkillRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _http_get(url: str) -> tuple[int, str, dict]:
    with urllib.request.urlopen(url, timeout=5) as resp:
        body = resp.read().decode("utf-8")
        return resp.status, body, dict(resp.headers)


@pytest.fixture
def populated_log() -> ExecutionEventLog:
    log = ExecutionEventLog()
    log.append(execution_started("abc12345", "fix CSV bug"))
    log.append(execution_failed(
        "abc12345", "fix CSV bug",
        category="runtime_error", recoverable=True, error="KeyError: profit",
    ))
    log.append(policy_decided("abc12345", decision="RETRY", reason="execution_failed"))
    log.append(execution_succeeded("abc12345", "fix CSV bug", output_summary="value=42"))
    log.append(task_completed("abc12345", outcome="RECOVERED", reason="execution_recovered"))
    log.append(execution_started("def67890", "plot revenue"))
    log.append(task_completed("def67890", outcome="SUCCESS", reason="execution_succeeded"))
    return log


@pytest.fixture
def server(populated_log: ExecutionEventLog) -> DashboardServer:
    srv = DashboardServer(populated_log, port=0)
    srv.start()
    try:
        yield srv
    finally:
        srv.stop()


# ---------------------------------------------------------------------------
# HTML pages
# ---------------------------------------------------------------------------


class TestHTMLPages:
    def test_home_page(self, server: DashboardServer) -> None:
        status, body, headers = _http_get(f"{server.base_url}/")
        assert status == 200
        assert "text/html" in headers["Content-Type"]
        assert "Reforge" in body
        assert "Dashboard" in body
        assert "Live event stream" in body

    def test_session_page(self, server: DashboardServer) -> None:
        status, body, _ = _http_get(f"{server.base_url}/sessions/abc12345")
        assert status == 200
        # The page is templated; session id is read from window.location on the client
        assert "<title>Reforge Runtime Dashboard</title>" in body
        assert "Session" in body

    def test_session_page_contains_new_panels(self, server: DashboardServer) -> None:
        """Retry timeline + policy decision trace + raw events all render."""
        _, body, _ = _http_get(f"{server.base_url}/sessions/abc12345")
        assert "Retry timeline" in body
        assert "Policy decision trace" in body
        assert "Raw events" in body
        # The client-side projection helper must be present.
        assert "buildRetryTimeline" in body
        # Decision-style mapping must be wired so RETRY/ACCEPT/STOP all get
        # distinct pill colors (governor-voice trace must be readable at a glance).
        assert "'RETRY'" in body
        assert "'ACCEPT'" in body
        assert "'STOP'" in body

    def test_memory_page(self, server: DashboardServer) -> None:
        status, body, _ = _http_get(f"{server.base_url}/memory")
        assert status == 200
        assert "Memory Substrate" in body

    def test_skills_page(self, server: DashboardServer) -> None:
        status, body, _ = _http_get(f"{server.base_url}/skills")
        assert status == 200
        assert "Skill Registry" in body
        assert "MCP" in body  # MCP badge is in the page


# ---------------------------------------------------------------------------
# Inherited JSON APIs still work
# ---------------------------------------------------------------------------


class TestInheritedAPIs:
    def test_summary_includes_kinds(self, server: DashboardServer) -> None:
        status, body, _ = _http_get(f"{server.base_url}/api/summary")
        assert status == 200
        data = json.loads(body)
        assert data["total_events"] == 7
        assert data["session_count"] == 2
        assert data["by_kind"]["EXECUTION_FAILED"] == 1
        assert data["by_kind"]["TASK_COMPLETED"] == 2

    def test_sessions_endpoint(self, server: DashboardServer) -> None:
        status, body, _ = _http_get(f"{server.base_url}/api/sessions")
        assert status == 200
        assert json.loads(body) == ["abc12345", "def67890"]

    def test_events_filtered_by_session(self, server: DashboardServer) -> None:
        status, body, _ = _http_get(
            f"{server.base_url}/api/events?session_id=abc12345"
        )
        assert status == 200
        events = json.loads(body)
        assert len(events) == 5
        assert all(e["session_id"] == "abc12345" for e in events)


# ---------------------------------------------------------------------------
# New /api/skills endpoint
# ---------------------------------------------------------------------------


class TestSkillsAPI:
    def test_default_registry_returned_when_none_injected(
        self, server: DashboardServer
    ) -> None:
        status, body, _ = _http_get(f"{server.base_url}/api/skills")
        assert status == 200
        data = json.loads(body)
        names = {s["name"] for s in data["skills"]}
        # default registry contains the file-system skills
        assert {"python_sandbox", "read", "grep", "glob", "edit"}.issubset(names)
        assert data["count"] >= 5

    def test_injected_registry_used(
        self, populated_log: ExecutionEventLog
    ) -> None:
        from reforge.runtime.skills.builtin import ReadSkill

        reg = SkillRegistry()
        reg.register(ReadSkill())
        srv = DashboardServer(populated_log, skill_registry=reg, port=0)
        srv.start()
        try:
            status, body, _ = _http_get(f"{srv.base_url}/api/skills")
            assert status == 200
            data = json.loads(body)
            assert data["count"] == 1
            assert data["skills"][0]["name"] == "read"
        finally:
            srv.stop()

    def test_mcp_flag_set_for_mcp_skills(self) -> None:
        """`_skills_payload` flags mcp.* skills via `is_mcp`."""
        from reforge.runtime.skills.builtin import ReadSkill

        class FakeMcp:
            name = "mcp.demo.echo"
            description = "fake"
            input_schema = {"type": "object"}
            prompt_fragment = ""

            def invoke(self, params, ctx):
                ...

        reg = SkillRegistry()
        reg.register(ReadSkill())
        reg.register(FakeMcp())
        payload = _skills_payload(reg)
        by_name = {s["name"]: s for s in payload["skills"]}
        assert by_name["read"]["is_mcp"] is False
        assert by_name["mcp.demo.echo"]["is_mcp"] is True


# ---------------------------------------------------------------------------
# New /api/memory endpoint
# ---------------------------------------------------------------------------


class TestMemoryAPI:
    def test_no_memory_dir_returns_empty(self, server: DashboardServer) -> None:
        status, body, _ = _http_get(f"{server.base_url}/api/memory")
        assert status == 200
        data = json.loads(body)
        assert data == {"records": [], "counts": {}}

    def test_with_memory_dir_reads_files(
        self, populated_log: ExecutionEventLog, tmp_path: Path
    ) -> None:
        mem = tmp_path / "memory"
        mem.mkdir()
        (mem / "recovery.json").write_text(
            json.dumps([
                {"user_request": "fix CSV", "outcome": "RECOVERED", "error_type": "KeyError"},
            ]),
            encoding="utf-8",
        )
        (mem / "failures.json").write_text(
            json.dumps([{"user_request": "boom", "outcome": "FAILED"}]),
            encoding="utf-8",
        )
        (mem / "success_patterns.json").write_text(json.dumps([]), encoding="utf-8")

        srv = DashboardServer(populated_log, memory_dir=mem, port=0)
        srv.start()
        try:
            status, body, _ = _http_get(f"{srv.base_url}/api/memory")
            data = json.loads(body)
            assert data["counts"]["RECOVERY"] == 1
            assert data["counts"]["FAILURE"] == 1
            assert data["counts"]["SUCCESS_PATTERN"] == 0
            types = {r["_memory_type"] for r in data["records"]}
            assert types == {"RECOVERY", "FAILURE"}
        finally:
            srv.stop()

    def test_filter_by_type(
        self, populated_log: ExecutionEventLog, tmp_path: Path
    ) -> None:
        mem = tmp_path / "memory"
        mem.mkdir()
        (mem / "recovery.json").write_text(
            json.dumps([{"user_request": "fix"}]), encoding="utf-8"
        )
        (mem / "failures.json").write_text(
            json.dumps([{"user_request": "boom"}]), encoding="utf-8"
        )

        srv = DashboardServer(populated_log, memory_dir=mem, port=0)
        srv.start()
        try:
            _, body, _ = _http_get(f"{srv.base_url}/api/memory?type=FAILURE")
            data = json.loads(body)
            assert len(data["records"]) == 1
            assert data["records"][0]["_memory_type"] == "FAILURE"
        finally:
            srv.stop()


# ---------------------------------------------------------------------------
# Unit-level helpers
# ---------------------------------------------------------------------------


class TestMemoryRecordsLoader:
    def test_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        result = _load_memory_records(tmp_path / "nope", None)
        assert result == {"records": [], "counts": {}}

    def test_handles_corrupted_json(self, tmp_path: Path) -> None:
        mem = tmp_path / "memory"
        mem.mkdir()
        (mem / "recovery.json").write_text("not json", encoding="utf-8")
        result = _load_memory_records(mem, None)
        assert result["counts"]["RECOVERY"] == 0


# ---------------------------------------------------------------------------
# 404 path
# ---------------------------------------------------------------------------


class TestUnknownRoutes:
    def test_unknown_api_path_404(self, server: DashboardServer) -> None:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _http_get(f"{server.base_url}/api/nope")
        assert exc.value.code == 404


import urllib.error  # noqa: E402  (used in the 404 test above)
