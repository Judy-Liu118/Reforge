"""Tests for memory CLI handlers (--memory-list, --memory-show, --memory-stats)."""

from __future__ import annotations

from pathlib import Path

import pytest

from reforge.memory.models import MemoryRecord, MemoryType
from reforge.memory.sqlite_substrate import SqliteMemorySubstrate
from reforge.cli.commands.memory import (
    handle_memory_list,
    handle_memory_show,
    handle_memory_stats,
    _normalize_type,
    _fmt_type,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> SqliteMemorySubstrate:
    return SqliteMemorySubstrate(db_path=tmp_path / "test.db")


def _recovery(mid: str, error_type: str = "ZeroDivisionError") -> MemoryRecord:
    return MemoryRecord(
        memory_id=mid,
        session_id="s1",
        timestamp="2026-06-13T10:00:00",
        memory_type=MemoryType.RECOVERY,
        user_request="compute something",
        outcome="RECOVERED",
        error_type=error_type,
        retry_count=1,
    )


def _success(mid: str, request: str = "write fibonacci") -> MemoryRecord:
    return MemoryRecord(
        memory_id=mid,
        session_id="s2",
        timestamp="2026-06-13T11:00:00",
        memory_type=MemoryType.SUCCESS_PATTERN,
        user_request=request,
        outcome="SUCCESS",
    )


# ---------------------------------------------------------------------------
# _normalize_type
# ---------------------------------------------------------------------------


def test_normalize_type_aliases() -> None:
    assert _normalize_type("recovery") == "RECOVERY"
    assert _normalize_type("success") == "SUCCESS_PATTERN"
    assert _normalize_type("pattern") == "SUCCESS_PATTERN"
    assert _normalize_type("FAILURE") == "FAILURE"
    assert _normalize_type("success_pattern") == "SUCCESS_PATTERN"


# ---------------------------------------------------------------------------
# _fmt_type
# ---------------------------------------------------------------------------


def test_fmt_type_strips_pattern_suffix() -> None:
    assert _fmt_type(MemoryType.SUCCESS_PATTERN) == "SUCCESS"
    assert _fmt_type(MemoryType.RECOVERY) == "RECOVERY"
    assert _fmt_type("SUCCESS_PATTERN") == "SUCCESS"


# ---------------------------------------------------------------------------
# SqliteMemorySubstrate.find()
# ---------------------------------------------------------------------------


def test_find_returns_record(db: SqliteMemorySubstrate, tmp_path: Path) -> None:
    rec = _success("find-test-id")
    db.write(rec)
    result = db.find("find-test-id")
    assert result is not None
    assert result.memory_id == "find-test-id"
    db.close()


def test_find_returns_none_for_missing(db: SqliteMemorySubstrate, tmp_path: Path) -> None:
    assert db.find("nonexistent-id") is None
    db.close()


# ---------------------------------------------------------------------------
# SqliteMemorySubstrate.stats()
# ---------------------------------------------------------------------------


def test_stats_empty(db: SqliteMemorySubstrate) -> None:
    s = db.stats()
    assert s["total"] == 0
    assert s["by_type"] == {}
    assert s["top_errors"] == []
    db.close()


def test_stats_counts_by_type(db: SqliteMemorySubstrate) -> None:
    db.write(_recovery("r1"))
    db.write(_recovery("r2"))
    db.write(_success("s1"))
    s = db.stats()
    assert s["total"] == 3
    assert s["by_type"]["RECOVERY"] == 2
    assert s["by_type"]["SUCCESS_PATTERN"] == 1
    db.close()


def test_stats_top_errors(db: SqliteMemorySubstrate) -> None:
    db.write(_recovery("r1", "ZeroDivisionError"))
    db.write(_recovery("r2", "ZeroDivisionError"))
    db.write(_recovery("r3", "FileNotFoundError"))
    s = db.stats()
    errors = dict(s["top_errors"])  # type: ignore[arg-type]
    assert errors["ZeroDivisionError"] == 2
    assert errors["FileNotFoundError"] == 1
    db.close()


# ---------------------------------------------------------------------------
# handle_memory_list
# ---------------------------------------------------------------------------


def test_memory_list_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(
        "reforge.cli.commands.memory.SqliteMemorySubstrate",
        lambda: SqliteMemorySubstrate(db_path=tmp_path / "empty.db"),
    )
    handle_memory_list()
    out = capsys.readouterr().out
    assert "No memory records" in out


def test_memory_list_shows_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    s = SqliteMemorySubstrate(db_path=tmp_path / "m.db")
    s.write(_success("aabbccdd", "write fibonacci function"))
    s.close()

    monkeypatch.setattr(
        "reforge.cli.commands.memory.SqliteMemorySubstrate",
        lambda: SqliteMemorySubstrate(db_path=tmp_path / "m.db"),
    )
    handle_memory_list()
    out = capsys.readouterr().out
    assert "aabbccdd" in out
    assert "SUCCESS" in out
    assert "fibonacci" in out


def test_memory_list_filtered_by_type(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    s = SqliteMemorySubstrate(db_path=tmp_path / "f.db")
    s.write(_recovery("rec-1"))
    s.write(_success("suc-1"))
    s.close()

    monkeypatch.setattr(
        "reforge.cli.commands.memory.SqliteMemorySubstrate",
        lambda: SqliteMemorySubstrate(db_path=tmp_path / "f.db"),
    )
    handle_memory_list("recovery")
    out = capsys.readouterr().out
    assert "rec-1" in out
    assert "suc-1" not in out


# ---------------------------------------------------------------------------
# handle_memory_show
# ---------------------------------------------------------------------------


def test_memory_show_full_detail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    s = SqliteMemorySubstrate(db_path=tmp_path / "s.db")
    s.write(_recovery("showme12", "KeyError"))
    s.close()

    monkeypatch.setattr(
        "reforge.cli.commands.memory.SqliteMemorySubstrate",
        lambda: SqliteMemorySubstrate(db_path=tmp_path / "s.db"),
    )
    handle_memory_show("showme12")
    out = capsys.readouterr().out
    assert "showme12" in out
    assert "RECOVERY" in out
    assert "KeyError" in out


def test_memory_show_prefix_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    s = SqliteMemorySubstrate(db_path=tmp_path / "p.db")
    s.write(_success("abcdef1234567890", "parse csv"))
    s.close()

    monkeypatch.setattr(
        "reforge.cli.commands.memory.SqliteMemorySubstrate",
        lambda: SqliteMemorySubstrate(db_path=tmp_path / "p.db"),
    )
    handle_memory_show("abcdef12")  # prefix
    out = capsys.readouterr().out
    assert "abcdef1234567890" in out


def test_memory_show_not_found_exits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "reforge.cli.commands.memory.SqliteMemorySubstrate",
        lambda: SqliteMemorySubstrate(db_path=tmp_path / "empty.db"),
    )
    with pytest.raises(SystemExit):
        handle_memory_show("nonexistent")


# ---------------------------------------------------------------------------
# handle_memory_stats
# ---------------------------------------------------------------------------


def test_memory_stats_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    s = SqliteMemorySubstrate(db_path=tmp_path / "st.db")
    s.write(_recovery("r1", "ZeroDivisionError"))
    s.write(_success("s1", "compute fibonacci"))
    s.close()

    monkeypatch.setattr(
        "reforge.cli.commands.memory.SqliteMemorySubstrate",
        lambda: SqliteMemorySubstrate(db_path=tmp_path / "st.db"),
    )
    handle_memory_stats()
    out = capsys.readouterr().out
    assert "Total Records" in out
    assert "2" in out
    assert "RECOVERY" in out
    assert "ZeroDivisionError" in out


def test_memory_stats_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(
        "reforge.cli.commands.memory.SqliteMemorySubstrate",
        lambda: SqliteMemorySubstrate(db_path=tmp_path / "empty.db"),
    )
    handle_memory_stats()
    out = capsys.readouterr().out
    assert "Total Records" in out
    assert "0" in out
