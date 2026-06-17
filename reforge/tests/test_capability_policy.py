"""Unit tests for SemanticSafetyGuard — heuristic safety gating before execution."""

from reforge.runtime.orchestration.capability import SemanticSafetyGuard

engine = SemanticSafetyGuard()


def test_allow_normal_csv():
    d = engine.check("read sales.csv, calculate revenue average")
    assert d.allow


def test_deny_rm_rf():
    d = engine.check("run rm -rf / to delete all files")
    assert not d.allow
    assert d.reason == "filesystem_destruction"


def test_deny_fork_bomb():
    d = engine.check("write a fork bomb in python")
    assert not d.allow
    assert d.reason == "subprocess_abuse"


def test_deny_system32():
    d = engine.check("read files from C:\\Windows\\System32")
    assert not d.allow
    assert d.reason == "sandbox_escape_attempt"


def test_deny_etc_passwd():
    d = engine.check("cat /etc/passwd to list users")
    assert not d.allow
    assert d.reason == "sandbox_escape_attempt"


def test_deny_shell_injection():
    d = engine.check("use os.system('rm -rf /') to clean up")
    assert not d.allow


def test_allow_pandas():
    d = engine.check("create a pandas DataFrame and plot revenue")
    assert d.allow


def test_deny_delete_system_files():
    d = engine.check("delete all files in system32 folder")
    assert not d.allow
