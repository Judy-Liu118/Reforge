"""SemanticSafetyGuard — pre-execution semantic gating.

Keyword/regex-based heuristics only. No LLM, no real sandbox isolation.
Checks user requests BEFORE code generation for dangerous patterns.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field


class CapabilityDecision(BaseModel):
    allow: bool = Field(default=True)
    # The risk category that triggered the deny (e.g. "filesystem_destruction").
    # Empty when allow=True — the field is meaningful only on rejection.
    deny_category: str = Field(default="")
    risk_level: str = Field(default="low")
    # Category matched but downgraded to allow=True under NORMAL_EXECUTION intent.
    # Lets callers/observability see "this looked risky but we let it through."
    warning_category: str = Field(default="")


_UNSAFE_PATTERNS: dict[str, list[str]] = {
    "filesystem_destruction": [
        r"rm\s+-rf", r"delete\s+system32", r"format\s+disk",
        r"delete\s+all\s+files", r"wipe\s+disk", r"shred",
        r"删除.*系统文件", r"格式化.*硬盘",
    ],
    "subprocess_abuse": [
        r"fork\s+bomb", r"fork\s*bomb",
        r"while\s+true.*fork", r":\(\)\s*\{",
        r"os\.fork\(\)",
    ],
    "sandbox_escape_attempt": [
        r"/etc/passwd", r"/etc/shadow",
        r"system32", r"C:\\\\Windows",
        r"ssh\s+key", r"private\s+key",
        r"\.ssh/", r"authorized_keys",
        r"读取.*C:\\\\Windows", r"访问.*系统目录",
    ],
    "network_abuse": [
        r"port\s+scan", r"ddos", r"botnet",
        r"端口扫描",
    ],
    "shell_injection": [
        r"os\.system\s*\(.*rm\s", r"os\.system\s+rm",
        r"subprocess\.call\(.*rm\s",
        r"eval\(.*input", r"exec\(.*input",
        r"__import__\(.*os.*\)\.system",
    ],
}


class SemanticSafetyGuard:
    """Pre-execution semantic safety gate. Keyword + regex heuristics only.

    Not a true capability isolation system — does not enforce resource boundaries
    or sandbox governance. Intercepts obviously dangerous request patterns before
    code generation.
    """

    def check(self, request: str, task_intent: str = "") -> CapabilityDecision:
        """Regex-screen the request.

        When *task_intent* is "NORMAL_EXECUTION" and the matched category is
        not high-risk, the hit is downgraded to allow=True with the category
        surfaced via warning_category. High-risk categories
        (filesystem_destruction / shell_injection / sandbox_escape_attempt)
        stay fail-closed regardless of intent. Omitting task_intent reproduces
        the legacy hard-deny-on-any-hit behavior.
        """
        lowered = request.lower()
        for category, patterns in _UNSAFE_PATTERNS.items():
            for pat in patterns:
                if re.search(pat, lowered):
                    risk = self._risk_level(category)
                    if task_intent == "NORMAL_EXECUTION" and risk != "high":
                        return CapabilityDecision(
                            allow=True,
                            warning_category=category,
                            risk_level=risk,
                        )
                    return CapabilityDecision(
                        allow=False,
                        deny_category=category,
                        risk_level=risk,
                    )
        return CapabilityDecision(allow=True, deny_category="", risk_level="low")

    @staticmethod
    def _risk_level(category: str) -> str:
        high_risk = {"filesystem_destruction", "shell_injection", "sandbox_escape_attempt"}
        medium_risk = {"subprocess_abuse", "network_abuse"}
        if category in high_risk:
            return "high"
        if category in medium_risk:
            return "medium"
        return "low"
