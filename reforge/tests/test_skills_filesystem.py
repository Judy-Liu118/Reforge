"""Tests for the file-system skills (P1): Read / Grep / Glob / Edit.

Each skill is tested for:
  - happy path
  - missing/invalid params
  - workspace escape protection
  - relevant edge cases
"""

from __future__ import annotations

from pathlib import Path

import pytest

from reforge.runtime.skills import Skill, SkillContext, SkillRegistry
from reforge.runtime.skills.builtin import (
    EditSkill,
    GlobSkill,
    GrepSkill,
    ReadSkill,
    default_skill_registry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(tmp_path: Path) -> SkillContext:
    return SkillContext(session_id="t", workspace=tmp_path, timeout_s=10)


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Protocol conformance for all builtin skills
# ---------------------------------------------------------------------------


class TestAllBuiltinsConformToProtocol:
    @pytest.mark.parametrize("skill_cls", [ReadSkill, GrepSkill, GlobSkill, EditSkill])
    def test_satisfies_skill_protocol(self, skill_cls) -> None:
        assert isinstance(skill_cls(), Skill)

    def test_default_registry_wires_all(self) -> None:
        # Pin the always-on baseline. Optional skills (web_search,
        # vision_describe, web_screenshot, compare_images) have their own
        # auto-registration tests; force them off here so this assertion
        # is independent of the developer's local .env / installed deps.
        reg = default_skill_registry(
            include_web_search=False,
            include_vision=False,
            include_web_screenshot=False,
            include_image_compare=False,
        )
        assert isinstance(reg, SkillRegistry)
        assert set(reg.names()) == {
            "python_sandbox", "read", "grep", "glob", "edit",
        }


# ---------------------------------------------------------------------------
# ReadSkill
# ---------------------------------------------------------------------------


class TestReadSkill:
    def test_reads_with_line_numbers(self, tmp_path: Path) -> None:
        _write(tmp_path / "foo.py", "alpha\nbeta\ngamma\n")
        skill = ReadSkill()
        r = skill.invoke({"path": "foo.py"}, _ctx(tmp_path))
        assert r.success
        assert "     1\talpha" in r.output
        assert "     3\tgamma" in r.output
        assert r.metadata["total_lines"] == 3

    def test_offset_and_limit(self, tmp_path: Path) -> None:
        _write(tmp_path / "f.txt", "\n".join(f"line{i}" for i in range(100)) + "\n")
        r = ReadSkill().invoke(
            {"path": "f.txt", "offset": 10, "limit": 5}, _ctx(tmp_path)
        )
        assert r.success
        assert r.metadata["returned_lines"] == 5
        assert "    11\tline10" in r.output
        assert "    15\tline14" in r.output
        assert "line15" not in r.output

    def test_missing_file(self, tmp_path: Path) -> None:
        r = ReadSkill().invoke({"path": "nope.py"}, _ctx(tmp_path))
        assert not r.success and "not found" in r.error

    def test_missing_path_param(self, tmp_path: Path) -> None:
        r = ReadSkill().invoke({}, _ctx(tmp_path))
        assert not r.success and "path" in r.error.lower()

    def test_workspace_escape_blocked(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "secret.txt"
        outside.write_text("nope", encoding="utf-8")
        try:
            r = ReadSkill().invoke({"path": str(outside)}, _ctx(tmp_path))
            assert not r.success and "outside workspace" in r.error
        finally:
            outside.unlink(missing_ok=True)

    def test_workspace_escape_allowed_when_opted_out(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "ok.txt"
        outside.write_text("readable", encoding="utf-8")
        try:
            r = ReadSkill(restrict_to_workspace=False).invoke(
                {"path": str(outside)}, _ctx(tmp_path)
            )
            assert r.success
            assert "readable" in r.output
        finally:
            outside.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# GlobSkill
# ---------------------------------------------------------------------------


class TestGlobSkill:
    def test_matches_py_files(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.py", "x")
        _write(tmp_path / "sub" / "b.py", "y")
        _write(tmp_path / "c.md", "z")
        r = GlobSkill().invoke({"pattern": "**/*.py"}, _ctx(tmp_path))
        assert r.success
        assert r.metadata["total_matches"] == 2
        assert "a.py" in r.output and "b.py" in r.output

    def test_no_match(self, tmp_path: Path) -> None:
        r = GlobSkill().invoke({"pattern": "**/*.nope"}, _ctx(tmp_path))
        assert r.success
        assert r.output == ""
        assert r.metadata["total_matches"] == 0

    def test_missing_pattern(self, tmp_path: Path) -> None:
        r = GlobSkill().invoke({}, _ctx(tmp_path))
        assert not r.success

    def test_limit_truncation(self, tmp_path: Path) -> None:
        for i in range(5):
            _write(tmp_path / f"f{i}.py", "x")
        r = GlobSkill().invoke({"pattern": "*.py", "limit": 3}, _ctx(tmp_path))
        assert r.success
        assert r.metadata["returned"] == 3
        assert r.metadata["truncated"] is True


# ---------------------------------------------------------------------------
# GrepSkill
# ---------------------------------------------------------------------------


class TestGrepSkill:
    def test_content_mode_with_line_numbers(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.py", "import os\nfoo = 1\nfoobar = 2\n")
        _write(tmp_path / "b.py", "no match here\n")
        r = GrepSkill().invoke(
            {"pattern": r"foo", "output_mode": "content"}, _ctx(tmp_path)
        )
        assert r.success
        # Both foo and foobar match; line numbers shown
        assert ":2:foo = 1" in r.output
        assert ":3:foobar = 2" in r.output
        assert "b.py" not in r.output

    def test_files_with_matches_default(self, tmp_path: Path) -> None:
        _write(tmp_path / "match.py", "needle")
        _write(tmp_path / "nope.py", "haystack")
        r = GrepSkill().invoke({"pattern": "needle"}, _ctx(tmp_path))
        assert r.success
        assert "match.py" in r.output
        assert "nope.py" not in r.output

    def test_count_mode(self, tmp_path: Path) -> None:
        _write(tmp_path / "x.py", "foo\nfoo\nbar\nfoo\n")
        r = GrepSkill().invoke(
            {"pattern": "foo", "output_mode": "count"}, _ctx(tmp_path)
        )
        assert r.success
        assert r.output.startswith("3\t")

    def test_case_insensitive(self, tmp_path: Path) -> None:
        _write(tmp_path / "x.py", "Hello World\n")
        r = GrepSkill().invoke(
            {"pattern": "hello", "case_insensitive": True}, _ctx(tmp_path)
        )
        assert r.success and "x.py" in r.output

    def test_glob_filter(self, tmp_path: Path) -> None:
        _write(tmp_path / "keep.py", "needle")
        _write(tmp_path / "skip.txt", "needle")
        r = GrepSkill().invoke(
            {"pattern": "needle", "glob": "*.py"}, _ctx(tmp_path)
        )
        assert r.success
        assert "keep.py" in r.output and "skip.txt" not in r.output

    def test_invalid_regex(self, tmp_path: Path) -> None:
        r = GrepSkill().invoke({"pattern": "[unclosed"}, _ctx(tmp_path))
        assert not r.success and "regex" in r.error

    def test_skips_excluded_dirs(self, tmp_path: Path) -> None:
        _write(tmp_path / "src" / "a.py", "needle")
        _write(tmp_path / ".venv" / "b.py", "needle")
        _write(tmp_path / "__pycache__" / "c.py", "needle")
        r = GrepSkill().invoke({"pattern": "needle"}, _ctx(tmp_path))
        assert r.success
        assert "src" in r.output
        assert ".venv" not in r.output
        assert "__pycache__" not in r.output


# ---------------------------------------------------------------------------
# EditSkill
# ---------------------------------------------------------------------------


class TestEditSkill:
    def test_unique_replacement(self, tmp_path: Path) -> None:
        f = _write(tmp_path / "x.py", "alpha\nbeta\ngamma\n")
        r = EditSkill().invoke(
            {"path": "x.py", "old_string": "beta", "new_string": "BETA"},
            _ctx(tmp_path),
        )
        assert r.success
        assert f.read_text(encoding="utf-8") == "alpha\nBETA\ngamma\n"
        assert r.metadata["replacements"] == 1

    def test_rejects_non_unique_without_replace_all(self, tmp_path: Path) -> None:
        _write(tmp_path / "x.py", "foo\nfoo\nfoo\n")
        r = EditSkill().invoke(
            {"path": "x.py", "old_string": "foo", "new_string": "bar"},
            _ctx(tmp_path),
        )
        assert not r.success
        assert "occurs 3 times" in r.error

    def test_replace_all(self, tmp_path: Path) -> None:
        f = _write(tmp_path / "x.py", "foo\nfoo\nfoo\n")
        r = EditSkill().invoke(
            {
                "path": "x.py",
                "old_string": "foo",
                "new_string": "bar",
                "replace_all": True,
            },
            _ctx(tmp_path),
        )
        assert r.success
        assert f.read_text(encoding="utf-8") == "bar\nbar\nbar\n"
        assert r.metadata["replacements"] == 3

    def test_old_not_found(self, tmp_path: Path) -> None:
        _write(tmp_path / "x.py", "alpha")
        r = EditSkill().invoke(
            {"path": "x.py", "old_string": "missing", "new_string": "x"},
            _ctx(tmp_path),
        )
        assert not r.success and "not found" in r.error

    def test_same_old_and_new_rejected(self, tmp_path: Path) -> None:
        _write(tmp_path / "x.py", "same")
        r = EditSkill().invoke(
            {"path": "x.py", "old_string": "same", "new_string": "same"},
            _ctx(tmp_path),
        )
        assert not r.success and "differ" in r.error

    def test_file_not_found(self, tmp_path: Path) -> None:
        r = EditSkill().invoke(
            {"path": "nope.py", "old_string": "x", "new_string": "y"},
            _ctx(tmp_path),
        )
        assert not r.success and "not found" in r.error

    def test_workspace_escape_blocked(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "evil.txt"
        outside.write_text("safe", encoding="utf-8")
        try:
            r = EditSkill().invoke(
                {"path": str(outside), "old_string": "safe", "new_string": "hacked"},
                _ctx(tmp_path),
            )
            assert not r.success and "outside workspace" in r.error
            # File untouched
            assert outside.read_text(encoding="utf-8") == "safe"
        finally:
            outside.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Integration: codegen-style scenario
# ---------------------------------------------------------------------------


class TestEndToEndCodeMaintenanceFlow:
    """Simulate the path-1 demo flow: glob → read → edit → verify."""

    def test_glob_then_read_then_edit_chain(self, tmp_path: Path) -> None:
        _write(tmp_path / "bug.py", "def add(a, b):\n    return a - b\n")
        ctx = _ctx(tmp_path)
        reg = default_skill_registry()

        glob_r = reg.get("glob").invoke({"pattern": "*.py"}, ctx)
        assert glob_r.success and "bug.py" in glob_r.output

        read_r = reg.get("read").invoke({"path": "bug.py"}, ctx)
        assert read_r.success and "return a - b" in read_r.output

        edit_r = reg.get("edit").invoke(
            {"path": "bug.py", "old_string": "a - b", "new_string": "a + b"},
            ctx,
        )
        assert edit_r.success

        # python_sandbox verifies the fix
        py_r = reg.get("python_sandbox").invoke(
            {"code": "import sys; sys.path.insert(0, '.'); from bug import add; print(add(2, 3))"},
            ctx,
        )
        assert py_r.success and "5" in py_r.output
