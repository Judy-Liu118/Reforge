"""Pure formatting functions for runtime execution traces.

No dependency on graph internals — only consumes (node_name, RuntimeState) pairs.
"""

from __future__ import annotations

from reforge.runtime.domain.state.models import RuntimeState


SEP = "-" * 60


def format_header(user_request: str) -> str:
    bar = "=" * 60
    return f"{bar}\n  Request: {user_request}\n{bar}"


def format_node(node_name: str, state: RuntimeState) -> str | None:
    """Return a formatted line for a node execution, or None if nothing to show."""

    if node_name == "capability_check":
        cap = state.capability_decision
        if cap:
            return f"  [Capability] DENY  risk={cap.get('risk_level', 'unknown')}  reason={cap.get('deny_category', 'policy')}"
        return "  [Capability] ALLOW"

    if node_name == "planner":
        # Show the first ~6 lines so multi-line plans render in full.
        # Single-line plans are unaffected.
        lines = state.generated_code.strip().splitlines()
        preview = "\n         ".join(lines[:6])
        if len(lines) > 6:
            preview += f"\n         ... ({len(lines) - 6} more lines)"
        return f"  [Plan] {preview}"

    if node_name == "code_generation":
        n = state.control_state.retry_count + 1
        rr = state.semantic_state.reflection_result
        if rr and rr.error_summary:
            return f"  [CodeGen #{n}] Regenerating... ({rr.error_summary})"
        return f"  [CodeGen #{n}] Generating initial code..."

    if node_name == "execution":
        n = state.control_state.retry_count + 1
        es = state.exec_state
        exit_code = es.exit_code if es.exit_code is not None else -1
        dur = es.duration_ms or 0.0
        if exit_code == 0 or exit_code is None:
            return f"  [Exec #{n}] OK  ({dur:.0f}ms)"
        return f"  [Exec #{n}] FAIL  exit_code={exit_code}  ({dur:.0f}ms)"

    if node_name == "reflection":
        rr = state.semantic_state.reflection_result
        if rr and rr.error_summary and rr.error_summary != "Execution succeeded":
            return f"  [Reflect] {rr.error_type}: {rr.error_summary}"
        return None

    if node_name == "evaluation":
        er = state.semantic_state.evaluation_result
        if er is None:
            return None
        if er.passed:
            return f"  [Eval]  score={er.score:.0%}  {er.summary}"
        failed = [c.name for c in er.checks if not c.passed]
        return f"  [Eval]  score={er.score:.0%}  FAIL  {er.summary}  ({', '.join(failed)})"

    if node_name == "retry_decision":
        cs = state.control_state
        action = cs.retry_decision_action or ""
        reason = cs.policy_reason or ""
        if not action:
            return None
        if action == "RETRY":
            policy_line = f"  [Policy] RETRY (attempt {state.control_state.retry_count + 1})"
        elif action == "STOP":
            policy_line = f"  [Policy] STOP"
        else:
            policy_line = f"  [Policy] {action}"
        return f"{policy_line}\n  [Reason] {reason}"

    if node_name == "final_response":
        return None

    return None


def format_traceback(state: RuntimeState) -> str | None:
    """Format the traceback if execution failed."""
    if not state.traceback:
        return None
    tb = state.traceback.strip()
    lines = [l for l in tb.split("\n") if l.strip()]
    if not lines:
        return None
    error_line = lines[-1]
    return f"  [Error] {error_line}"


def format_stdout_tail(state: RuntimeState, *, max_lines: int = 20) -> str | None:
    """Surface the last `max_lines` of stdout when an attempt failed.

    Without this, `[reforge.step] <helper>: N.Ns` timing prints from the
    visual helpers are swallowed — the CLI's per-attempt summary only
    shows the [Error] (stderr last line). For diagnostic runs we want to
    see how the wall-clock budget was spent.
    """
    if not state.execution_output or not state.execution_output.stdout:
        return None
    exit_code = state.execution_output.exit_code
    # Only show on failure — successful runs already printed everything live.
    if exit_code is not None and exit_code == 0:
        return None
    raw = state.execution_output.stdout.strip()
    lines = [l for l in raw.split("\n") if l.strip()]
    if not lines:
        return None
    tail = lines[-max_lines:]
    body = "\n    ".join(tail)
    header = (
        "  [stdout tail]"
        + (f" (last {len(tail)} of {len(lines)} lines)" if len(lines) > max_lines else "")
    )
    return f"{header}\n    {body}"


def format_code(state: RuntimeState) -> str | None:
    """Format the generated Python code for display."""
    code = state.generated_code.strip()
    if not code:
        return None
    lines = code.split("\n")
    if len(lines) <= 12:
        return f"  [Code]\n{SEP}\n{code}\n{SEP}"
    # Truncate long code
    head = "\n".join(lines[:8])
    tail = "\n".join(lines[-4:])
    return f"  [Code] ({len(lines)} lines)\n{SEP}\n{head}\n  ...\n{tail}\n{SEP}"


def format_summary(state: RuntimeState) -> str:
    """Format an execution summary table showing all attempts."""
    if not state.attempts:
        return ""

    lines = [SEP, "  Execution Summary", f"  {'#':<4} {'Status':<10} {'Duration':<12} {'Error'}"]
    lines.append(f"  {'-' * 50}")

    for a in state.attempts:
        status = "OK" if a.exit_code == 0 else "FAIL"
        dur = f"{a.duration_ms:.0f}ms"
        err = a.error_type if a.error_type else "-"
        lines.append(f"  {a.attempt:<4} {status:<10} {dur:<12} {err}")

    lines.append(SEP)
    return "\n".join(lines)


def format_multistep_header(user_request: str, n_subtasks: int, has_parallel: bool = False) -> str:
    bar = "=" * 60
    parallel_note = "  (parallel levels detected)" if has_parallel else ""
    return f"{bar}\n  Request: {user_request}\n  Multi-step: {n_subtasks} subtasks{parallel_note}\n{bar}"


def format_subtask_header(index: int, total: int, description: str, request: str) -> str:
    bar = "-" * 60
    label = description or request[:60]
    return f"\n{bar}\n  Step {index + 1}/{total}: {label}\n{bar}"


def format_multistep_summary(
    overall_outcome: str,
    subtask_outcomes: list[str],
    total_ms: float,
) -> str:
    bar = "=" * 60
    lines = [f"\n{bar}", "  Multi-Step Summary"]
    for i, outcome in enumerate(subtask_outcomes, 1):
        mark = "OK" if outcome in ("SUCCESS", "RECOVERED", "EXPECTED_FAILURE") else "FAIL"
        lines.append(f"    Step {i}: {mark} ({outcome})")
    lines.append(f"  Overall: {overall_outcome}  |  Total: {total_ms:.0f}ms")
    lines.append(bar)
    return "\n".join(lines)


def format_research_header(question: str, max_rounds: int) -> str:
    bar = "=" * 60
    return f"{bar}\n  Research: {question}\n  Max rounds: {max_rounds}\n{bar}"


def format_research_hypothesis(
    round_num: int, hypothesis: str, status: str, evidence: str = ""
) -> str:
    icon = {"confirmed": "✓", "rejected": "✗", "inconclusive": "?", "pending": "·"}.get(
        status, "·"
    )
    line = f"  [{icon}] Round {round_num}: {hypothesis[:80]}"
    if evidence:
        line += f"\n      {evidence[:100]}"
    return line


def format_research_summary(result) -> str:
    bar = "=" * 60
    confirmed = [h for h in result.final_hypotheses if h.status == "confirmed"]
    rejected = [h for h in result.final_hypotheses if h.status == "rejected"]
    inconclusive = [h for h in result.final_hypotheses if h.status == "inconclusive"]
    lines = [f"\n{bar}", f"  Research complete  |  {result.total_rounds} round(s)"]
    if confirmed:
        lines.append(f"  Confirmed ({len(confirmed)}): " + "; ".join(
            h.hypothesis[:50] for h in confirmed
        ))
    if rejected:
        lines.append(f"  Rejected  ({len(rejected)}): " + "; ".join(
            h.hypothesis[:50] for h in rejected
        ))
    if inconclusive:
        lines.append(f"  Inconclusive ({len(inconclusive)}): " + "; ".join(
            h.hypothesis[:50] for h in inconclusive
        ))
    if result.contradictions_detected:
        lines.append(f"  Contradictions detected: {len(result.contradictions_detected)}")
    lines.append(bar)
    return "\n".join(lines)


def format_research_history(results: list) -> str:
    if not results:
        return "No research history yet."
    header = f"  {'ID':<10} {'Question':<42} {'Rounds':<7} {'OK':<5} {'Contradictions'}"
    lines = [header, "  " + "-" * 76]
    for r in results:
        confirmed = sum(1 for h in r.final_hypotheses if h.status == "confirmed")
        q = r.question[:40].replace("\n", " ")
        rid = r.research_id[:8] if r.research_id else "-"
        lines.append(
            f"  {rid:<10} {q:<42} {r.total_rounds:<7} {confirmed:<5} {len(r.contradictions_detected)}"
        )
    return "\n".join(lines)


def format_result(state: RuntimeState) -> str:
    """Format the final result — separates execution status from task outcome."""
    bar = "=" * 60
    cap = state.capability_decision
    if cap:
        return (
            f"\n{bar}\n"
            f"  [Capability]        DENY\n"
            f"  [Task Outcome]      DENIED\n"
            f"  [Reason]             {cap.get('deny_category', 'policy')}\n"
            f"{bar}\n"
            f"Request blocked by capability policy.\n"
            f"{bar}"
        )

    output = (state.outcome_state.final_answer or "").strip()
    exec_status = "OK" if state.exec_state.exit_code == 0 else "FAIL"
    os_ = state.outcome_state
    outcome = os_.task_outcome or "FAILED"
    reason = os_.outcome_reason or ""

    status_lines = [
        f"  [Execution Status]  {exec_status}",
        f"  [Task Outcome]      {outcome}",
    ]
    if reason:
        status_lines.append(f"  [Reason]             {reason}")

    return f"\n{bar}\n" + "\n".join(status_lines) + f"\n{bar}\n{output}\n{bar}"
