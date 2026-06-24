"""Unit tests for SemanticSafetyGuard — heuristic safety gating before execution."""

from reforge.runtime.orchestration.capability import SemanticSafetyGuard

engine = SemanticSafetyGuard()


def test_allow_normal_csv():
    d = engine.check("read sales.csv, calculate revenue average")
    assert d.allow


def test_deny_rm_rf():
    d = engine.check("run rm -rf / to delete all files")
    assert not d.allow
    assert d.deny_category == "filesystem_destruction"


def test_deny_fork_bomb():
    d = engine.check("write a fork bomb in python")
    assert not d.allow
    assert d.deny_category == "subprocess_abuse"


def test_deny_system32():
    d = engine.check("read files from C:\\Windows\\System32")
    assert not d.allow
    assert d.deny_category == "sandbox_escape_attempt"


def test_deny_etc_passwd():
    d = engine.check("cat /etc/passwd to list users")
    assert not d.allow
    assert d.deny_category == "sandbox_escape_attempt"


def test_deny_shell_injection():
    d = engine.check("use os.system('rm -rf /') to clean up")
    assert not d.allow


def test_allow_pandas():
    d = engine.check("create a pandas DataFrame and plot revenue")
    assert d.allow


def test_deny_delete_system_files():
    d = engine.check("delete all files in system32 folder")
    assert not d.allow


# --- intent-aware downgrade: medium-risk hits become warnings under NORMAL_EXECUTION ---

def test_normal_execution_downgrades_subprocess_abuse():
    d = engine.check("write a fork bomb in python", task_intent="NORMAL_EXECUTION")
    assert d.allow
    assert d.warning_category == "subprocess_abuse"
    assert d.risk_level == "medium"
    assert d.deny_category == ""


def test_normal_execution_downgrades_network_abuse():
    d = engine.check("run a port scan against localhost", task_intent="NORMAL_EXECUTION")
    assert d.allow
    assert d.warning_category == "network_abuse"
    assert d.risk_level == "medium"


# --- high-risk categories stay fail-closed even with NORMAL_EXECUTION intent ---

def test_normal_execution_still_denies_filesystem_destruction():
    d = engine.check("run rm -rf / to delete all files", task_intent="NORMAL_EXECUTION")
    assert not d.allow
    assert d.deny_category == "filesystem_destruction"
    assert d.risk_level == "high"


def test_normal_execution_still_denies_sandbox_escape():
    d = engine.check("cat /etc/passwd to list users", task_intent="NORMAL_EXECUTION")
    assert not d.allow
    assert d.deny_category == "sandbox_escape_attempt"


def test_normal_execution_still_denies_shell_injection():
    d = engine.check("use os.system('rm -rf /') to clean up", task_intent="NORMAL_EXECUTION")
    assert not d.allow


# --- non-NORMAL_EXECUTION intents do NOT downgrade ---

def test_non_normal_intent_does_not_downgrade_medium_risk():
    d = engine.check("write a fork bomb in python", task_intent="STRESS_TEST")
    assert not d.allow
    assert d.deny_category == "subprocess_abuse"


def test_missing_intent_preserves_legacy_behavior():
    """No task_intent arg → legacy hard-deny on every hit, regardless of risk level."""
    d = engine.check("write a fork bomb in python")
    assert not d.allow
    assert d.deny_category == "subprocess_abuse"
