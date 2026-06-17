"""Tests for ASTGuard and RetryIntegrityGuard."""

from reforge.runtime.orchestration.ast_guard import ASTGuard
from reforge.runtime.orchestration.integrity_guard import RetryIntegrityGuard


class TestASTGuard:
    def test_allow_normal_code(self):
        r = ASTGuard().analyze("import pandas as pd\nprint('hello')")
        assert r.allow

    def test_detect_os_system(self):
        r = ASTGuard().analyze("import os\nos.system('rm -rf /')")
        assert not r.allow
        assert any("os" in v for v in r.violations)

    def test_detect_subprocess_popen(self):
        r = ASTGuard().analyze("from subprocess import Popen\nPopen(['ls'])")
        assert not r.allow

    def test_detect_eval(self):
        r = ASTGuard().analyze("eval('1+1')")
        assert not r.allow

    def test_detect_exec(self):
        r = ASTGuard().analyze("exec('import os')")
        assert not r.allow

    def test_detect_getattr_os_system(self):
        r = ASTGuard().analyze("import os\nf = getattr(os, 'system')\nf('rm -rf /')")
        # getattr itself is flagged
        assert any("getattr" in v for v in r.violations)

    # --- Bare imports of function-level-dangerous modules are allowed ---

    def test_bare_import_os_for_path_use_is_allowed(self):
        """Regression: `import os` to call `os.path.exists()` is legitimate.
        The previous rule flagged the import itself, blocking attempt-#2-style
        fallback code that checked file existence before synthesizing data.
        """
        code = (
            "import os\n"
            "if not os.path.exists('orders.csv'):\n"
            "    print('missing — using sample data')\n"
        )
        r = ASTGuard().analyze(code)
        assert r.allow, f"unexpected violations: {r.violations}"

    def test_bare_import_subprocess_alone_is_allowed(self):
        # Importing the module without calling a dangerous attribute is fine.
        r = ASTGuard().analyze("import subprocess\nprint(subprocess.__name__)")
        assert r.allow

    def test_bare_import_shutil_for_safe_use(self):
        r = ASTGuard().analyze("import shutil\nprint(shutil.disk_usage('.').free)")
        assert r.allow

    # --- Wildcard-dangerous modules still get blocked at the import ---

    def test_import_ctypes_is_blocked_at_import_time(self):
        r = ASTGuard().analyze("import ctypes")
        assert not r.allow
        assert any("ctypes" in v for v in r.violations)

    def test_import_socket_is_blocked_at_import_time(self):
        r = ASTGuard().analyze("import socket")
        assert not r.allow

    # --- Attribute check still catches dangerous calls on allowed imports ---

    def test_os_remove_is_caught_via_attribute_check(self):
        """Even though `import os` is now allowed, `os.remove('x')` must still
        be flagged — the attribute set is auto-derived from _DANGEROUS_IMPORTS
        so it covers every function listed there, not just the hand-picked few.
        """
        r = ASTGuard().analyze("import os\nos.remove('/tmp/x')")
        assert not r.allow
        assert any("os.remove" in v for v in r.violations)

    def test_subprocess_run_is_caught_via_attribute_check(self):
        r = ASTGuard().analyze("import subprocess\nsubprocess.run(['ls'])")
        assert not r.allow
        assert any("subprocess.run" in v for v in r.violations)

    def test_shutil_rmtree_is_caught_via_attribute_check(self):
        r = ASTGuard().analyze("import shutil\nshutil.rmtree('/tmp/x')")
        assert not r.allow
        assert any("shutil.rmtree" in v for v in r.violations)

    def test_from_os_import_path_is_allowed(self):
        # `from os import path` — path is not on the dangerous list
        r = ASTGuard().analyze("from os import path\nprint(path.exists('x'))")
        assert r.allow

    def test_from_os_import_system_is_blocked(self):
        r = ASTGuard().analyze("from os import system\nsystem('ls')")
        assert not r.allow


class TestRetryIntegrityGuard:
    def test_clean_code(self):
        r = RetryIntegrityGuard().check("print('hello')")
        assert r.clean

    def test_detect_blank_except_pass(self):
        r = RetryIntegrityGuard().check("try:\n    risky()\nexcept:\n    pass")
        assert not r.clean

    def test_clean_except_with_recovery(self):
        r = RetryIntegrityGuard().check("try:\n    risky()\nexcept KeyError:\n    print('fixed')")
        assert r.clean

    def test_detect_fake_traceback(self):
        # traceback.print_exc() in except = swallowed exception
        r = RetryIntegrityGuard().check(
            "import traceback\ntry:\n    risky()\nexcept:\n    traceback.print_exc()\n    print('all good')"
        )
        assert not r.clean
