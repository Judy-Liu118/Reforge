"""Tests for codegen's vision-vs-text route selection.

Routing is decided per attempt by `bool(state.image_inputs)`:
  * non-empty image_inputs → multimodal client + VISION_CODEGEN_SYSTEM
  * empty / missing → text client + CODE_GENERATION_SYSTEM

`image_inputs` is populated once by RuntimeRunner from the caller's
declaration and is task-level immutable across the loop (see
test_state_image_inputs_immutable_through_loop.py for the invariant).
There is no more filesystem scan, no more visual-intent regex — the
disambiguation between "user-declared input image" and "data-task
produced PNG that happens to live in the workspace" is structural:
only what the caller declared lands in image_inputs.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

from reforge.runtime.orchestration.graph.nodes.codegen import code_generation_node
from reforge.runtime.domain.state.models import RuntimeState


def _state(user_request: str, image_inputs: list[str] | None = None) -> RuntimeState:
    return RuntimeState(
        user_request=user_request,
        image_inputs=image_inputs if image_inputs is not None else [],
    )


class TestCodegenNodeRouting:
    def test_text_path_when_image_inputs_empty(self):
        """image_inputs=[] → plain LLMClient.chat path."""
        s = _state("Add 1 + 1 and print the result", image_inputs=[])
        with patch(
            "reforge.runtime.orchestration.graph.nodes.codegen.LLMClient"
        ) as MockClient:
            instance = MagicMock()
            instance.chat.return_value = "print(1+1)"
            MockClient.return_value = instance
            result = code_generation_node(s)
        assert result["generated_code"] == "print(1+1)"
        instance.chat.assert_called_once()
        MockClient.for_vision_codegen.assert_not_called()
        instance.chat_multimodal.assert_not_called()

    def test_text_path_when_image_inputs_default(self):
        """image_inputs default (empty list) → text path."""
        s = _state("Compute pi via Monte Carlo")
        assert s.image_inputs == []
        with patch(
            "reforge.runtime.orchestration.graph.nodes.codegen.LLMClient"
        ) as MockClient:
            instance = MagicMock()
            instance.chat.return_value = "code"
            MockClient.return_value = instance
            code_generation_node(s)
        instance.chat.assert_called_once()
        MockClient.for_vision_codegen.assert_not_called()

    def test_vision_path_when_image_inputs_set(self, tmp_path: Path):
        """image_inputs=[path] → multimodal client + image Paths."""
        image_path = tmp_path / "target.png"
        s = _state("请复刻 target.png 前端页面", image_inputs=[str(image_path)])
        with patch(
            "reforge.runtime.orchestration.graph.nodes.codegen.LLMClient"
        ) as MockClient:
            vision_instance = MagicMock()
            vision_instance.chat_multimodal.return_value = "from helpers import shot"
            MockClient.for_vision_codegen.return_value = vision_instance
            result = code_generation_node(s)
        assert result["generated_code"] == "from helpers import shot"
        MockClient.return_value.chat.assert_not_called()
        vision_instance.chat_multimodal.assert_called_once()
        _, _, image_paths = vision_instance.chat_multimodal.call_args.args
        assert list(image_paths) == [image_path]

    def test_vision_system_prompt_used_when_image_inputs_set(self, tmp_path: Path):
        from reforge.models.prompts.templates import VISION_CODEGEN_SYSTEM

        s = _state(
            "根据 target.png 复刻前端",
            image_inputs=[str(tmp_path / "target.png")],
        )
        with patch(
            "reforge.runtime.orchestration.graph.nodes.codegen.LLMClient"
        ) as MockClient:
            vision_instance = MagicMock()
            vision_instance.chat_multimodal.return_value = "code"
            MockClient.for_vision_codegen.return_value = vision_instance
            code_generation_node(s)
        sys_prompt = vision_instance.chat_multimodal.call_args.args[0]
        assert sys_prompt == VISION_CODEGEN_SYSTEM

    def test_text_system_prompt_used_when_image_inputs_empty(self):
        from reforge.models.prompts.templates import CODE_GENERATION_SYSTEM

        s = _state("Compute pi via Monte Carlo", image_inputs=[])
        with patch(
            "reforge.runtime.orchestration.graph.nodes.codegen.LLMClient"
        ) as MockClient:
            instance = MagicMock()
            instance.chat.return_value = "code"
            MockClient.return_value = instance
            code_generation_node(s)
        sys_prompt = instance.chat.call_args.args[0]
        assert sys_prompt == CODE_GENERATION_SYSTEM
