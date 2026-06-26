"""RuntimeRunner.run/stream(image_inputs=...) populates the initial state.

The Runner is the only legitimate writer of image_inputs. This pins the
plumbing: kwarg → initial RuntimeState.image_inputs, defensive copy so
the caller's mutation doesn't bleed into the loop.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import Mock, patch

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


def _factory(text_responses: list[str], vision_responses: list[str]):
    text_cursor = [0]
    vision_cursor = [0]

    class _FakeVisionClient:
        def chat_multimodal(self, _system, _user, _images) -> str:
            idx = vision_cursor[0]
            vision_cursor[0] += 1
            return vision_responses[idx] if idx < len(vision_responses) else vision_responses[-1]

    class _FakeLLM:
        def chat(self, _system, _user) -> str:
            idx = text_cursor[0]
            text_cursor[0] += 1
            return text_responses[idx] if idx < len(text_responses) else text_responses[-1]

        @classmethod
        def for_vision_codegen(cls):
            return _FakeVisionClient()

    return _FakeLLM


class TestRunnerImageInputsPlumbing:
    def test_run_default_is_empty_list(self):
        factory = _factory(["1. Plan", "print('hi')"], vision_responses=["unused"])
        with (
            _patch_llm_nodes(factory),
            patch("reforge.runtime.policy.task_intent.LLMClient", return_value=_INTENT_MOCK),
        ):
            state = RuntimeRunner().run("print hi")
        assert state.image_inputs == []

    def test_run_image_inputs_kwarg_lands_on_state(self):
        factory = _factory(["1. Plan"], vision_responses=["print('seen')"])
        with (
            _patch_llm_nodes(factory),
            patch("reforge.runtime.policy.task_intent.LLMClient", return_value=_INTENT_MOCK),
        ):
            state = RuntimeRunner().run(
                "复刻 target.png 前端",
                image_inputs=["/abs/path/a.png", "/abs/path/b.png"],
            )
        assert state.image_inputs == ["/abs/path/a.png", "/abs/path/b.png"]

    def test_runner_defensively_copies_caller_list(self):
        """Caller mutates their list after run() — state still has the original."""
        factory = _factory(["1. Plan"], vision_responses=["print('seen')"])
        caller_list = ["/a.png"]
        with (
            _patch_llm_nodes(factory),
            patch("reforge.runtime.policy.task_intent.LLMClient", return_value=_INTENT_MOCK),
        ):
            runner = RuntimeRunner()
            captured: list[RuntimeState] = []
            for _name, s in runner.stream(
                "复刻 target.png 前端",
                image_inputs=caller_list,
            ):
                captured.append(s)
            caller_list.append("/b.png")  # post-hoc caller mutation
        final = captured[-1]
        assert final.image_inputs == ["/a.png"], (
            "post-run mutation of caller's list must not affect the final state"
        )

    def test_stream_default_is_empty_list(self):
        factory = _factory(["1. Plan", "print('hi')"], vision_responses=["unused"])
        with (
            _patch_llm_nodes(factory),
            patch("reforge.runtime.policy.task_intent.LLMClient", return_value=_INTENT_MOCK),
        ):
            runner = RuntimeRunner()
            seen_states = [s for _n, s in runner.stream("print hi")]
        assert all(s.image_inputs == [] for s in seen_states)
