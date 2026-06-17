"""FailureFingerprint — structured failure signature extracted from tracebacks.

Replaces keyword-guessing on user_request text with precise traceback parsing.
Only the error class line and surrounding context are analysed: deterministic, O(n).

Usage
-----
    fp = extract_fingerprint(state.traceback, error_type="KeyError")
    record.problem_signature = fp.to_dict()
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field


class FailureFingerprint(BaseModel):
    """Structured failure signature extracted from a Python traceback."""

    error_class: str = Field(default="")       # e.g. "ImportError", "KeyError"
    missing_module: str = Field(default="")    # ModuleNotFoundError / ImportError target
    missing_key: str = Field(default="")       # KeyError target
    missing_file: str = Field(default="")      # FileNotFoundError target
    undefined_name: str = Field(default="")    # NameError target
    execution_phase: str = Field(default="")   # "import" | "runtime" | "syntax"
    domain: str = Field(default="")            # "pandas" | "numpy" | "filesystem" | "python" | "general"

    def to_dict(self) -> dict:
        """Serialise to the problem_signature dict compatible with MemoryRecord scoring.

        Includes backward-compat keys (error_type, root_cause, domain) so existing
        scoring code continues to work alongside new structured keys.
        """
        d: dict = {
            "error_class": self.error_class,
            "error_type": self.error_class,    # backward compat alias
            "execution_phase": self.execution_phase,
            "domain": self.domain,
            "root_cause": _root_cause_from_class(self.error_class),
        }
        if self.missing_module:
            d["missing_module"] = self.missing_module
            d["root_cause"] = "missing_import"
        if self.missing_key:
            d["missing_key"] = self.missing_key
            d["root_cause"] = "missing_key"
        if self.missing_file:
            d["missing_file"] = self.missing_file
            d["root_cause"] = "missing_file"
        if self.undefined_name:
            d["undefined_name"] = self.undefined_name
            d["root_cause"] = "undefined_name"
        return d


def extract_fingerprint(traceback: str, error_type: str = "") -> FailureFingerprint:
    """Parse *traceback* into a FailureFingerprint.

    Falls back gracefully to *error_type* string when traceback is empty.
    Never raises — worst case returns a mostly-empty fingerprint.
    """
    if not traceback.strip():
        return _from_error_type_string(error_type)

    error_line = _last_error_line(traceback)
    error_class, message = _split_error_line(error_line)
    if not error_class and error_type:
        error_class = error_type.split(":")[0].strip()

    fp = FailureFingerprint(
        error_class=error_class,
        execution_phase=_infer_phase(error_class, traceback),
        domain=_infer_domain(error_class, message, traceback),
    )

    if error_class in ("ImportError", "ModuleNotFoundError"):
        fp = fp.model_copy(update={"missing_module": _extract_module(message)})
    elif error_class == "KeyError":
        fp = fp.model_copy(update={"missing_key": _extract_quoted(message)})
    elif error_class == "FileNotFoundError":
        fp = fp.model_copy(update={"missing_file": _extract_file(message)})
    elif error_class == "NameError":
        fp = fp.model_copy(update={"undefined_name": _extract_name(message)})

    return fp


# ---------------------------------------------------------------------------
# Internal parsers
# ---------------------------------------------------------------------------

def _last_error_line(traceback: str) -> str:
    """Return the last line that looks like a Python error."""
    lines = [l.strip() for l in traceback.strip().splitlines() if l.strip()]
    for line in reversed(lines):
        if re.match(r"[A-Za-z][A-Za-z0-9_.]*(?:Error|Exception|Warning)\s*:", line):
            return line
    return lines[-1] if lines else ""


def _split_error_line(line: str) -> tuple[str, str]:
    """'ErrorClass: message' → ('ErrorClass', 'message')."""
    m = re.match(r"^([A-Za-z][A-Za-z0-9_.]*(?:Error|Exception|Warning))\s*:\s*(.*)", line)
    if m:
        return m.group(1), m.group(2)
    return "", line


def _extract_module(message: str) -> str:
    """'No module named 'pandas.core'' → 'pandas'."""
    m = re.search(r"No module named ['\"]([^'\"]+)['\"]", message)
    if m:
        return m.group(1).split(".")[0]
    m = re.search(r"cannot import name ['\"]([^'\"]+)['\"] from ['\"]([^'\"]+)['\"]", message)
    if m:
        return m.group(2).split(".")[0]
    return ""


def _extract_quoted(message: str) -> str:
    """KeyError: 'column_name' → 'column_name'."""
    m = re.search(r"['\"]([^'\"]{1,80})['\"]", message)
    return m.group(1) if m else message[:40].strip()


def _extract_file(message: str) -> str:
    """FileNotFoundError: [Errno 2] No such file or directory: 'data.csv' → 'data.csv'."""
    m = re.search(r"No such file or directory:\s*['\"]([^'\"]+)['\"]", message)
    if m:
        return m.group(1)
    m = re.search(r"['\"]([^'\"]+\.[a-zA-Z0-9]{1,10})['\"]", message)
    return m.group(1) if m else ""


def _extract_name(message: str) -> str:
    """NameError: name 'df' is not defined → 'df'."""
    m = re.search(r"name ['\"]([^'\"]+)['\"] is not defined", message)
    if m:
        return m.group(1)
    m = re.search(r"['\"]([^'\"]+)['\"]", message)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# Phase + domain inference
# ---------------------------------------------------------------------------

def _infer_phase(error_class: str, traceback: str) -> str:
    if error_class == "SyntaxError":
        return "syntax"
    if error_class in ("ImportError", "ModuleNotFoundError"):
        return "import"
    return "runtime"


_DOMAIN_PATTERNS: list[tuple[str, str]] = [
    ("pandas",     r"\bpandas\b|DataFrame|Series|\.iloc|\.loc\b|pd\."),
    ("numpy",      r"\bnumpy\b|ndarray|np\."),
    ("matplotlib", r"\bmatplotlib\b|plt\.|pyplot"),
    ("filesystem", r"FileNotFoundError|PermissionError|No such file"),
]


def _infer_domain(error_class: str, message: str, traceback: str) -> str:
    context = traceback + " " + message
    for domain, pattern in _DOMAIN_PATTERNS:
        if re.search(pattern, context):
            return domain
    if error_class in ("SyntaxError", "NameError", "TypeError", "AttributeError",
                        "ValueError", "IndexError"):
        return "python"
    return "general"


_ROOT_CAUSE_MAP: dict[str, str] = {
    "ImportError":          "missing_import",
    "ModuleNotFoundError":  "missing_import",
    "KeyError":             "missing_key",
    "FileNotFoundError":    "missing_file",
    "NameError":            "undefined_name",
    "AttributeError":       "attribute_error",
    "TypeError":            "type_error",
    "ValueError":           "value_error",
    "ZeroDivisionError":    "division_by_zero",
    "SyntaxError":          "syntax_error",
    "IndexError":           "index_out_of_range",
}


def _root_cause_from_class(error_class: str) -> str:
    return _ROOT_CAUSE_MAP.get(error_class, "unknown")


def _from_error_type_string(error_type: str) -> FailureFingerprint:
    """Minimal fingerprint from error_type string only (no traceback)."""
    if not error_type:
        return FailureFingerprint()
    error_class = error_type.split(":")[0].strip()
    return FailureFingerprint(
        error_class=error_class,
        execution_phase=_infer_phase(error_class, ""),
        domain=_infer_domain(error_class, error_type, ""),
    )
