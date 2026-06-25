"""Rule-based heuristic evaluator. Checks execution output quality without LLM.

Evaluator = signal provider only. Runtime Decision = final authority.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from reforge.runtime.orchestration.ast_guard import ASTGuard
from reforge.runtime.orchestration.integrity_guard import RetryIntegrityGuard
from reforge.runtime.domain.state.models import EvalCheck, EvaluationResult, RuntimeState


class HeuristicEvaluator:
    """Evaluate execution output with simple rule-based checks.

    This is NOT an LLM judge. It performs fast, deterministic checks
    to catch common output quality problems.
    """

    # Patterns that match Python traceback shapes / shell-level error messages.
    # Substring scans for the word "error" misfire on legitimate prose like
    # "absolute error vs math.pi: 0.01" — these patterns target traceback
    # *shape*, not vocabulary, so benign text containing "error" passes.
    ERROR_OUTPUT_PATTERNS: list[tuple[str, "re.Pattern[str]"]] = [
        ("Traceback (most recent call last):",
         re.compile(r"\bTraceback \(most recent call last\):")),
        ('File "...", line N',
         re.compile(r'^\s*File "[^"]+", line \d+', re.MULTILINE)),
        ("<ExceptionClass>: <message>",
         re.compile(r"^\s*[A-Z]\w*(?:Error|Exception)\s*:\s+\S", re.MULTILINE)),
        ("No such file or directory",
         re.compile(r"\bNo such file or directory\b")),
        ("Permission denied",
         re.compile(r"\b[Pp]ermission denied\b")),
    ]
    MIN_OUTPUT_LENGTH = 5
    MIN_DATA_OUTPUT_LENGTH = 20  # Data tasks need more than a one-word answer

    # Patterns for detecting suspicious results
    SUSPICIOUS_NUMERIC = {"0", "0.0", "0.00", "none", "null", "nan", "inf", "-inf"}

    # Keywords that mark a request as data-oriented (output must contain structured data)
    DATA_TASK_KEYWORDS = [
        "csv", "dataframe", "数据", "均值", "mean", "average",
        "calculate", "计算", "分析", "analyze", "统计", "sum", "count",
    ]

    # Keywords that mark a request as a research verification task
    RESEARCH_VERIFY_KEYWORDS = [
        "verify", "check if", "check whether", "test whether",
        "confirm that", "validate", "prove that", "show that",
    ]
    MIN_RESEARCH_OUTPUT_LENGTH = 15

    # User intent patterns that relax error-related checks
    INTENTIONAL_ERROR_PATTERNS = [
        "故意报错", "故意触发", "故意让", "让它出错", "让他出错",
        "让其出错", "让它报错", "演示 traceback", "演示错误",
        "教学 syntaxerror", "教学 error", "教学异常",
        "用于教学", "error example", "exception demo",
        "traceback demo", "演示异常", "故意包含",
        "故意生成.*错误", "故意.*异常",
    ]

    # Anti-cheating: blanket-except detection
    BLANKET_EXCEPT_RE = re.compile(
        r"except\s*(?:Exception|BaseException)?\s*:\s*\n\s*(?:pass|return|continue|print\s*\()",
        re.MULTILINE,
    )

    # Swallowed-compare detection: the LLM captured a low similarity score
    # from compare_images and then printed a "Warning: ..." message instead
    # of raising. We see a SCORE: X.XX line in stdout plus a warning-style
    # follow-up, with the script exiting cleanly — visibly bad output
    # masquerading as a clean run.
    _SCORE_LINE_RE = re.compile(
        r"(?:visual\s+similarity\s+score|similarity\s+score|score)\s*[:=]\s*"
        r"(0?\.\d+|1(?:\.0+)?|0|1)",
        re.IGNORECASE,
    )
    _SWALLOW_HINT_RE = re.compile(
        r"warning\b|low\s+similarity|continuing\s+anyway|comparison\s+skipped|"
        r"could\s+not\s+(?:compare|perform)|comparison\s+failed",
        re.IGNORECASE,
    )
    # Above this threshold, a printed score followed by a "Warning" is
    # accepted as harmless commentary (the comparison passed). Below it,
    # the script printed a real failure signal but kept going.
    _COMPARE_SWALLOW_SCORE_FLOOR = 0.7

    # Detect filenames promised in prompts like "save the chart to plot.png and
    # the data to results.csv". Split into two stages so we can match multiple
    # filenames per prompt without the verb anchor blocking later matches:
    #   1. Verb gate — the prompt must mention an artifact-producing verb at
    #      least once. Without this, prose like "the README points to docs.md"
    #      would trip the filename regex.
    #   2. Filename scan — finditer captures every "to/as/into <name>.<ext>"
    #      occurrence. Extension allowlist keeps benign prose like "go to
    #      paris.json" extremely unlikely while accepting all real artifact
    #      types.
    _ARTIFACT_VERB_RE = re.compile(
        r"\b(?:save|write|output|export|store|generate|produce)\b",
        re.IGNORECASE,
    )
    _ARTIFACT_FILENAME_RE = re.compile(
        r"\b(?:to|as|into)\s+"
        r"([A-Za-z0-9_./\\-]+?"
        r"\.(?:png|jpg|jpeg|svg|gif|webp|pdf|csv|tsv|md|txt|json|jsonl|html|htm|xlsx|parquet|yaml|yml|xml|log))"
        r"\b",
        re.IGNORECASE,
    )

    # Approximate upper bound on wall-clock between an attempt's execution
    # finishing and this evaluator being called. Covers the reflection LLM
    # call that sits between exec and eval (typically 5–15s, rarely up to
    # 30s). Used to detect artifacts that survived from a *prior* attempt.
    # Proper architectural fix is reading EXECUTION_STARTED.timestamp from
    # the event log — this is the pragmatic intermediate.
    _STALE_ARTIFACT_GRACE_S = 30.0

    def __init__(self, workspace: Path | None = None, now_fn=None) -> None:
        """Workspace defaults to cwd because the subprocess sandbox already
        runs with cwd=workspace (sandbox A), so any file the script writes
        lands there. Tests can inject a tmp_path for isolation, and a
        deterministic now_fn to avoid flake from wall-clock comparisons.
        """
        self._workspace = workspace if workspace is not None else Path.cwd()
        self._now_fn = now_fn or time.time

    def evaluate(self, state: RuntimeState) -> EvaluationResult:
        """Run all checks against the current state and return a result."""
        if state.execution_output is None:
            return self._make_result(
                passed=False, score=0.0,
                checks=[EvalCheck(name="has_execution", passed=False, detail="No execution output")],
                summary="No execution happened", failure_type="execution_failed",
            )

        checks: list[EvalCheck] = []
        stdout = (state.execution_output.stdout or "").strip()
        stderr = (state.execution_output.stderr or "").strip()
        is_intentional = self._is_intentional_task(state.user_request)

        # Check: clean_exit — exit_code 0 is the strongest signal of success.
        # A non-zero exit (with or without traceback) is execution failure,
        # even when the script printed a graceful message before quitting.
        # Without this, eval can score 100% on a script that did `exit(1)`,
        # leaving governor as the only layer that sees the failure.
        exit_code = state.execution_output.exit_code
        if exit_code is not None and exit_code != 0:
            checks.append(EvalCheck(
                name="clean_exit",
                passed=False,
                detail=f"script exited with non-zero code {exit_code}",
            ))

        # Check: output_not_empty
        has_output = len(stdout) >= self.MIN_OUTPUT_LENGTH
        checks.append(EvalCheck(
            name="output_not_empty",
            passed=has_output,
            detail=f"stdout has {len(stdout)} chars" if has_output else "stdout is empty or too short",
        ))

        # Checks: no_error_in_output + stderr_clean.
        # Both detect failure-shaped output (tracebacks, error lines). For
        # intentional-error tasks these are by definition expected, so we
        # skip them entirely instead of appending paired "relaxed" entries
        # that pad the check list without contributing signal. Pattern-
        # based so prose like "absolute error vs math.pi: 0.01" does not
        # trip them; only traceback shape does.
        if not is_intentional:
            matched = [name for name, pat in self.ERROR_OUTPUT_PATTERNS if pat.search(stdout)]
            no_errors = not matched
            checks.append(EvalCheck(
                name="no_error_in_output",
                passed=no_errors,
                detail="output clean" if no_errors else f"matched: {matched[0]}",
            ))
            if stderr:
                stderr_clean = "traceback" not in stderr.lower()
                checks.append(EvalCheck(
                    name="stderr_clean",
                    passed=stderr_clean,
                    detail="stderr is clean" if stderr_clean else f"stderr: {stderr[:80]}",
                ))

        # Check: suspicious_result
        if "average" in state.user_request.lower() or "mean" in state.user_request.lower() or "统计" in state.user_request:
            stripped = stdout.strip()
            if stripped in self.SUSPICIOUS_NUMERIC or (
                stripped.replace(",", "").replace(".", "").isdigit()
                and float(stripped.replace(",", "")) == 0
            ):
                checks.append(EvalCheck(
                    name="suspicious_result",
                    passed=False,
                    detail=f"Result '{stripped[:40]}' looks implausible for a statistical query",
                ))

        # Check: blanket_except_detected — anti-cheating / retry drift
        if state.generated_code and self.BLANKET_EXCEPT_RE.search(state.generated_code):
            checks.append(EvalCheck(
                name="blanket_except_detected",
                passed=False,
                detail="Code contains bare 'except: pass' or similar silent error swallowing",
            ))

        # Check: AST capability violations
        if state.generated_code:
            ast_result = ASTGuard().analyze(state.generated_code)
            if not ast_result.allow:
                checks.append(EvalCheck(
                    name="ast_capability_violation",
                    passed=False,
                    detail=f"Dangerous code patterns: {', '.join(ast_result.violations)}",
                ))

        # Check: retry integrity
        if state.generated_code:
            integrity = RetryIntegrityGuard().check(state.generated_code)
            if not integrity.clean:
                for issue in integrity.issues:
                    checks.append(EvalCheck(
                        name=f"integrity:{issue.split(':')[0]}",
                        passed=False,
                        detail=issue,
                    ))

        # Check: must_fail_first_violated
        if (state.task_requirements and state.task_requirements.must_fail_first
                and state.control_state.retry_count == 0
                and state.execution_output.exit_code == 0):
            checks.append(EvalCheck(
                name="must_fail_first_violated",
                passed=False,
                detail="Task requires intentional failure first, but code executed cleanly — process shortcut detected",
            ))

        # Check: unnecessary_exception_handling
        if (state.task_requirements and state.task_requirements.expects_uncaught_exception
                and state.generated_code
                and re.search(r"\btry\s*:", state.generated_code)):
            checks.append(EvalCheck(
                name="unnecessary_exception_handling",
                passed=False,
                detail="Task expects real uncaught exception, but code contains try/except — swallows authentic traceback",
            ))

        # Check: retry_drift — same error type repeating across attempts (no progress)
        rr = state.semantic_state.reflection_result
        if (not is_intentional
                and rr
                and rr.error_type
                and len(state.attempts) >= 2):
            current_error = rr.error_type
            recent_errors = [a.error_type for a in state.attempts[-2:] if a.error_type]
            if len(recent_errors) >= 2 and all(e == current_error for e in recent_errors):
                checks.append(EvalCheck(
                    name="retry_drift",
                    passed=False,
                    detail=(
                        f"'{current_error}' repeated across {len(recent_errors)} attempts — "
                        "code regeneration is not fixing the root cause"
                    ),
                ))

        # Check: output_contains_data — data-oriented tasks need substantive output
        lowered_request = state.user_request.lower()
        is_data_task = any(kw in lowered_request for kw in self.DATA_TASK_KEYWORDS)
        if (is_data_task and not is_intentional and stdout
                and len(stdout) < self.MIN_DATA_OUTPUT_LENGTH
                and not any(c.isdigit() for c in stdout)):
            checks.append(EvalCheck(
                name="output_contains_data",
                passed=False,
                detail=(
                    f"Data task output too brief ('{stdout[:40]}'). "
                    "Use print() to show intermediate and final results."
                ),
            ))

        # Check: compare_swallowed — the script printed a low similarity score
        # alongside a Warning message and then exited cleanly. The LLM wrapped
        # the comparison verdict in try/except and reported the failure as
        # commentary instead of raising. Visibly bad output passing as success.
        if (not is_intentional
                and state.execution_output.exit_code == 0
                and self._SWALLOW_HINT_RE.search(stdout)):
            score_match = self._SCORE_LINE_RE.search(stdout)
            if score_match:
                try:
                    score_val = float(score_match.group(1))
                except ValueError:
                    score_val = 1.0
                if score_val < self._COMPARE_SWALLOW_SCORE_FLOOR:
                    checks.append(EvalCheck(
                        name="compare_swallowed",
                        passed=False,
                        detail=(
                            f"Visible similarity score {score_val:.2f} below "
                            f"{self._COMPARE_SWALLOW_SCORE_FLOOR} but script "
                            "printed a warning and exited cleanly. compare_images "
                            "verdict was swallowed — raise on low score instead."
                        ),
                    ))

        # Check: output_artifact_exists — when the prompt promises a file output,
        # confirm the script actually produced it during THIS attempt. Catches
        # both "I gracefully handled the error with try/except and never wrote
        # the file" and "a prior attempt wrote the file but this one did not".
        if not is_intentional and state.execution_output.exit_code == 0:
            promised = self._extract_promised_artifacts(state.user_request)
            if promised:
                exec_duration_s = (state.execution_output.duration_ms or 0) / 1000
                # Anything written before `freshness_threshold` is from a prior
                # attempt or pre-existed before this session began.
                freshness_threshold = (
                    self._now_fn() - exec_duration_s - self._STALE_ARTIFACT_GRACE_S
                )
                for fname in promised:
                    fail_reason = self._classify_artifact(fname, freshness_threshold)
                    if fail_reason is not None:
                        checks.append(EvalCheck(
                            name="output_artifact_exists",
                            passed=False,
                            detail=(
                                f"Prompt promised artifact '{fname}' but {fail_reason}. "
                                "Don't swallow the underlying error with try/except — "
                                "fix the cause or synthesize fallback data and actually "
                                "write the file."
                            ),
                        ))
                        break  # one failure is enough; avoid noisy duplicate fails

        # Check: research_output_quality — verification tasks need quantitative output
        lowered_request = state.user_request.lower()
        is_research_verify = any(kw in lowered_request for kw in self.RESEARCH_VERIFY_KEYWORDS)
        if (is_research_verify and not is_intentional and stdout
                and (len(stdout) < self.MIN_RESEARCH_OUTPUT_LENGTH
                     or not any(c.isdigit() for c in stdout))):
            checks.append(EvalCheck(
                name="research_output_quality",
                passed=False,
                detail=(
                    f"Research verification needs quantitative output "
                    f"(got {len(stdout)} chars, has_numbers="
                    f"{any(c.isdigit() for c in stdout)}). "
                    "Use print() with measurements or counts."
                ),
            ))

        passed = all(c.passed for c in checks)
        score = sum(1 for c in checks if c.passed) / len(checks) if checks else 1.0
        score = round(score, 2)

        failed_names = [c.name for c in checks if not c.passed]
        summary = "All checks passed" if passed else f"{len(failed_names)} check(s) failed"

        failure_type = self._classify_failure(checks, has_output)

        return self._make_result(
            passed=passed, score=score, checks=checks,
            summary=summary, failure_type=failure_type,
        )

    def _is_intentional_task(self, user_request: str) -> bool:
        """Check if the user's request is an intentional error / demo task."""
        lowered = user_request.lower()
        for pattern in self.INTENTIONAL_ERROR_PATTERNS:
            if re.search(pattern, lowered):
                return True
        return False

    def _extract_promised_artifacts(self, user_request: str) -> list[str]:
        """Pull filenames from save/write/output promises in the prompt.

        Requires an artifact-producing verb to appear somewhere in the prompt
        (gate), then finds every "to/as/into <file>.<ext>" occurrence
        (scan). De-duplicates while preserving order.
        """
        if not self._ARTIFACT_VERB_RE.search(user_request):
            return []
        seen: list[str] = []
        for match in self._ARTIFACT_FILENAME_RE.finditer(user_request):
            fname = match.group(1).strip().strip(".,'\"")
            if fname and fname not in seen:
                seen.append(fname)
        return seen

    def _resolve_artifact_path(self, fname: str) -> Path:
        """Resolve a promised artifact filename against the workspace.

        Absolute paths are honored as-is; relative paths are joined to workspace.
        """
        p = Path(fname)
        return p if p.is_absolute() else self._workspace / p

    def _classify_artifact(self, fname: str, freshness_threshold: float) -> str | None:
        """Return a human-readable failure reason, or None if the artifact looks good."""
        p = self._resolve_artifact_path(fname)
        if not p.exists():
            return f"file is missing at {p}"
        try:
            st = p.stat()
        except OSError as exc:
            return f"could not stat file at {p}: {exc}"
        if st.st_size == 0:
            return f"file at {p} exists but is empty"
        if st.st_mtime < freshness_threshold:
            return (
                f"file at {p} is stale (mtime predates this attempt — it was "
                "written by a previous attempt or pre-existed before this run)"
            )
        return None

    def _classify_failure(self, checks: list[EvalCheck], has_output: bool) -> str:
        failed = {c.name for c in checks if not c.passed}
        if not failed:
            return ""
        if "has_execution" in failed:
            return "execution_failed"
        if "clean_exit" in failed:
            return "execution_failed"
        if "output_not_empty" in failed:
            return "empty_output"
        if "suspicious_result" in failed:
            return "suspicious_result"
        if "must_fail_first_violated" in failed:
            return "must_fail_first_violated"
        if "unnecessary_exception_handling" in failed:
            return "unnecessary_exception_handling"
        if "blanket_except_detected" in failed:
            return "blanket_except_detected"
        if "no_error_in_output" in failed:
            return "invalid_output"
        if "stderr_clean" in failed:
            return "invalid_output"
        if "retry_drift" in failed:
            return "retry_drift"
        if "output_contains_data" in failed:
            return "insufficient_output"
        if "compare_swallowed" in failed:
            return "swallowed_comparison"
        if "output_artifact_exists" in failed:
            return "missing_artifact"
        if "research_output_quality" in failed:
            return "insufficient_output"
        return "incomplete_result"

    @staticmethod
    def _make_result(
        passed: bool, score: float, checks: list[EvalCheck],
        summary: str, failure_type: str,
    ) -> EvaluationResult:
        return EvaluationResult(
            passed=passed, score=score, checks=checks,
            summary=summary, failure_type=failure_type,
        )
