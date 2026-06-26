"""Loop-boundary invariant: image_inputs is task-level immutable.

The codegen node selects the vision route per attempt by
`bool(state.image_inputs)`. The disambiguation that prevents
"data task wrote chart.png to workspace → next attempt routes through
vision" is structural: only what the caller declared on the initial
state lands in image_inputs, and no graph node may mutate it.

These tests pin both directions:
  * Legal retry path keeps image_inputs identical across attempts
    (merged dict always carries the prior chunk's image_inputs forward,
    pydantic round-trip preserves it).
  * A node that intentionally returns image_inputs in its update dict
    raises RuntimeError from the chunk-loop invariant.

We test both because asserting only the failure path doesn't catch a
regression that silently empties image_inputs (which would also "not
mutate" by way of being empty all along).
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import Mock, patch

import pytest

from reforge.runtime.domain.state.models import RuntimeState
from reforge.runtime.orchestration.engine.runner import RuntimeRunner


_INTENT_MOCK = Mock()
_INTENT_MOCK.chat = Mock(return_value="NORMAL_EXECUTION")


def _patch_llm_nodes(factory):
    stack = ExitStack()
    for module in ("planner", "codegen", "reflection"):
        stack.enter_context(
            patch(f"reforge.runtime.orchestration.graph.nodes.{module}.LLMClient", factory)
        )
    return stack


def _multimodal_factory(text_responses: list[str], vision_responses: list[str]):
    """Factory producing FakeLLMs that share global cursors for text & vision.

    Vision path is reached via LLMClient.for_vision_codegen() (classmethod) and
    consumes chat_multimodal. Text path uses chat. Both share state across
    instances so the retry loop can mix them naturally.
    """
    text_cursor = [0]
    vision_cursor = [0]

    class _FakeVisionClient:
        def chat_multimodal(self, _system: str, _user: str, _images) -> str:
            idx = vision_cursor[0]
            vision_cursor[0] += 1
            return vision_responses[idx] if idx < len(vision_responses) else vision_responses[-1]

    class _FakeLLM:
        def chat(self, _system: str, _user: str) -> str:
            idx = text_cursor[0]
            text_cursor[0] += 1
            return text_responses[idx] if idx < len(text_responses) else text_responses[-1]

        @classmethod
        def for_vision_codegen(cls):
            return _FakeVisionClient()

    return _FakeLLM


class TestImageInputsImmutableAcrossRetries:
    def test_image_inputs_preserved_across_forced_retry(self):
        """Legal path: vision-route task that triggers ≥1 retry.

        Whatever the governor decides (RETRY some, then ACCEPT or STOP), the
        final image_inputs must be byte-identical to the caller's declaration.
        This pins the contract codegen relies on to re-route through vision
        on every attempt.

        We assert `retry_count >= 1` rather than a specific number so the test
        doesn't couple to the evaluator's pass/fail decision on synthetic
        output (which is governor-domain, not what this test is about).
        """
        text_responses = [
            "1. Render the page",  # planner uses text chat
            # Reflection between attempts (one per failure) — replayed if more.
            "ErrorType: RuntimeError\nSummary: forced fail\nFix: succeed next time",
        ]
        vision_responses = [
            "raise RuntimeError('first attempt')",  # codegen attempt 1 → exec fails
            "print('done')",  # codegen attempt 2 → exec succeeds
        ]
        factory = _multimodal_factory(text_responses, vision_responses)
        declared_inputs = ["/synthetic/target.png"]

        with (
            _patch_llm_nodes(factory),
            patch("reforge.runtime.policy.task_intent.LLMClient", return_value=_INTENT_MOCK),
        ):
            runner = RuntimeRunner()
            state = runner.run("复刻 target.png 前端", image_inputs=declared_inputs)

        assert state.control_state.retry_count >= 1, (
            f"expected ≥1 retry to exercise the loop, got {state.control_state.retry_count}"
        )
        assert state.image_inputs == declared_inputs, (
            "image_inputs must be byte-identical to caller's declaration after the loop"
        )
        assert state.image_inputs is not declared_inputs, (
            "Runner must defensively copy the caller's list (verified via list() in stream)"
        )

    def test_text_only_task_keeps_image_inputs_empty(self):
        """Auxiliary direction: no declaration → empty all the way through."""
        text_responses = [
            "1. Write a print",
            "print('hello')",
        ]
        factory = _multimodal_factory(text_responses, vision_responses=["unused"])

        with (
            _patch_llm_nodes(factory),
            patch("reforge.runtime.policy.task_intent.LLMClient", return_value=_INTENT_MOCK),
        ):
            runner = RuntimeRunner()
            state = runner.run("print hello")

        assert state.image_inputs == []
        assert state.control_state.retry_count == 0

    def test_pydantic_model_validate_preserves_image_inputs_on_partial_update(self):
        """node_update without image_inputs key must NOT reset the field to default.

        This is the pydantic round-trip behaviour the chunk-loop relies on:
        `merged = current.model_dump() | node_update; RuntimeState.model_validate(merged)`
        works because model_dump emits image_inputs from the current state, and
        a partial update dict that omits the key leaves the merged value alone.
        """
        s = RuntimeState(user_request="x", image_inputs=["/a.png"])
        dumped = s.model_dump()
        node_update = {"generated_code": "print(1)"}  # no image_inputs key
        merged = dumped | node_update
        rebuilt = RuntimeState.model_validate(merged)
        assert rebuilt.image_inputs == ["/a.png"]
        assert rebuilt.generated_code == "print(1)"


class TestInvariantTrapsMutation:
    def test_mid_loop_mutation_raises(self):
        """A node update dict that contains image_inputs must blow up.

        Inject the violation directly at the graph.stream boundary so the test
        is independent of which node "did" the mutation — LangGraph captures
        node function references at build time, making module-level monkeypatch
        of a node unreliable for this purpose. The invariant lives in the
        chunk loop of RuntimeRunner.stream, so that's where we test it.
        """
        runner = RuntimeRunner()
        poisoned_chunks = iter([
            {"code_generation": {
                "generated_code": "print(1)",
                "image_inputs": ["/a.png", "/poisoned/extra.png"],
            }},
        ])
        with patch.object(runner._graph, "stream", return_value=poisoned_chunks):
            with pytest.raises(RuntimeError, match="image_inputs mutated mid-loop"):
                runner.run("test", image_inputs=["/a.png"])
