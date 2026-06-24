"""Evaluation feedback enrichment — maps failed checks to actionable fix instructions.

Converts terse check names into specific, code-level instructions that help
the LLM understand exactly what to change in the next attempt.
"""

from __future__ import annotations

from reforge.runtime.domain.state.models import EvaluationResult

# Maps each check name to a concrete, actionable instruction for the next code attempt
_CHECK_TO_INSTRUCTION: dict[str, str] = {
    "clean_exit": (
        "Your script exited with a non-zero status code. Do not call exit() / "
        "sys.exit(1) to gracefully bail when an input is missing — synthesize "
        "fallback data, retry with different inputs, or attempt the task with "
        "what you have. The runtime decides retry vs. accept, not your script."
    ),
    "output_not_empty": (
        "Add print() statements — your code produced no visible output. "
        "Every result must be printed explicitly."
    ),
    "no_error_in_output": (
        "stdout contains error keywords — output should show results, not error messages. "
        "Fix the underlying error instead of printing it."
    ),
    "stderr_clean": (
        "Fix the traceback shown in stderr — the code must run without exceptions."
    ),
    "suspicious_result": (
        "Your numeric result was 0, None, or NaN — this is likely a logic error. "
        "Verify column names, data types, and calculation logic."
    ),
    "blanket_except_detected": (
        "Remove 'except: pass' or 'except Exception: pass' — "
        "silent error suppression hides the real problem. "
        "Handle errors explicitly or let them propagate."
    ),
    "must_fail_first_violated": (
        "This task REQUIRES deliberate failure on the first attempt. "
        "Do NOT produce clean code yet — introduce a syntax or logic error first."
    ),
    "unnecessary_exception_handling": (
        "Remove all try/except blocks — "
        "this task requires the exception to propagate naturally to stderr."
    ),
    "retry_drift": (
        "The same error type has appeared in multiple consecutive attempts. "
        "Your current approach is not working — try a fundamentally different strategy. "
        "Consider: different library, different algorithm, or different data access method."
    ),
    "output_contains_data": (
        "Output is too brief for a data task. "
        "Use print() to display intermediate results, computed values, "
        "and final answers. The output must contain actual numbers or structured data."
    ),
    "output_artifact_exists": (
        "The prompt promised a file output (e.g. 'save chart to plot.png') "
        "but the file is missing or empty. Don't swallow the underlying error "
        "with try/except to make the script exit cleanly — either fix the root "
        "cause (e.g. read a different file, generate synthetic data when the "
        "input is unavailable) or actually write the promised file."
    ),
    "compare_swallowed": (
        "Your script captured a low similarity score from compare_images and "
        "then printed a 'Warning' message instead of raising. The runtime needs "
        "a real failure signal (a raised exception) to drive self-heal — "
        "burying it in a print is dishonest reporting. Use bare comparison + "
        "threshold check + raise: `if score < N: raise RuntimeError(...)`. "
        "No try/except around compare_images. No 'continuing anyway' messages."
    ),
    "ast_capability_violation": (
        "Your code contains dangerous operations (os.system, subprocess, eval, etc.) "
        "that are not permitted. Use only standard data analysis libraries."
    ),
    "research_output_quality": (
        "Research verification requires quantitative output — "
        "add print() with actual measurements, counts, or computed values. "
        "A bare True/False or one-word answer is not sufficient evidence."
    ),
}


def format_eval_feedback(er: EvaluationResult) -> str:
    """Convert failed evaluation checks into specific, actionable instructions.

    Returns empty string if evaluation passed.
    Falls back to check detail text for unknown check names.
    """
    if er.passed:
        return ""

    failed_checks = [c for c in er.checks if not c.passed]
    if not failed_checks:
        return f"Evaluation failed: {er.summary}"

    lines = ["Evaluation failures — fix each issue in the next attempt:"]
    for c in failed_checks:
        instruction = _CHECK_TO_INSTRUCTION.get(c.name, c.detail)
        lines.append(f"  [{c.name}] {instruction}")

    return "\n".join(lines)
