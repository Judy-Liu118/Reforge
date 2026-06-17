"""Tests for codegen's vision-routing decision logic.

When the user references a target image (target.png in workspace) and
the request matches visual reproduction intent, codegen must route to
the multimodal vision client instead of the text-only one. The text
path discards ~90% of UI signal (layout / colors / fonts) so vision
routing is the only realistic way to converge on complex SaaS mocks.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from reforge.runtime.orchestration.graph.nodes.codegen import code_generation_node
from reforge.runtime.orchestration.graph.vision_routing import (
    discover_target_images as _discover_target_images,
)
from reforge.runtime.domain.state.models import RuntimeState


@pytest.fixture
def cwd_in(tmp_path, monkeypatch):
    """Sandbox cwd → tmp_path so target image discovery is hermetic."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _state(user_request: str) -> RuntimeState:
    return RuntimeState(user_request=user_request)


# ---------------------------------------------------------------------------
# _discover_target_images — the gate that turns vision routing on or off
# ---------------------------------------------------------------------------


class TestTargetImageDiscovery:
    def test_no_image_no_routing(self, cwd_in):
        """No target.* file → no vision route, regardless of intent."""
        assert _discover_target_images("请复刻 target.png") == []

    def test_png_with_chinese_intent(self, cwd_in):
        (cwd_in / "target.png").write_bytes(b"\x89PNG\r\nfake")
        result = _discover_target_images("请复刻 target.png 前端页面")
        assert result == [cwd_in / "target.png"]

    def test_png_with_english_intent(self, cwd_in):
        (cwd_in / "target.png").write_bytes(b"\x89PNG\r\nfake")
        result = _discover_target_images(
            "Reproduce the frontend page from target.png"
        )
        assert result == [cwd_in / "target.png"]

    def test_jpg_also_detected(self, cwd_in):
        (cwd_in / "target.jpg").write_bytes(b"\xff\xd8\xff\xe0jpg")
        result = _discover_target_images("复刻 UI 界面")
        assert result == [cwd_in / "target.jpg"]

    def test_no_visual_intent_no_routing(self, cwd_in):
        """target.png exists but request is data analysis — text path is
        correct (sending the image would waste tokens and confuse the model)."""
        (cwd_in / "target.png").write_bytes(b"\x89PNG\r\nfake")
        result = _discover_target_images(
            "Read sales.csv and compute average revenue"
        )
        assert result == []

    def test_visual_intent_without_image_returns_empty(self, cwd_in):
        """User asks to reproduce a UI but supplied no target.png —
        runtime should NOT route to vision (model would have no image to
        look at); let the text path explain the gap or fail clearly."""
        result = _discover_target_images("复刻一个前端页面")
        assert result == []


# ---------------------------------------------------------------------------
# code_generation_node — full integration: route is taken when gate opens
# ---------------------------------------------------------------------------


class TestCodegenNodeRouting:
    def test_text_path_when_no_target_image(self, cwd_in):
        """No image → use plain LLMClient + .chat (text-only)."""
        s = _state("Add 1 + 1 and print the result")
        with patch(
            "reforge.runtime.orchestration.graph.nodes.codegen.LLMClient"
        ) as MockClient:
            instance = MagicMock()
            instance.chat.return_value = "print(1+1)"
            MockClient.return_value = instance
            result = code_generation_node(s)
        assert result["generated_code"] == "print(1+1)"
        instance.chat.assert_called_once()
        # Vision factory must NOT have been used.
        MockClient.for_vision_codegen.assert_not_called()
        instance.chat_multimodal.assert_not_called()

    def test_vision_path_when_target_png_and_intent(self, cwd_in):
        """target.png present + 复刻 keyword → vision route taken."""
        (cwd_in / "target.png").write_bytes(b"\x89PNG\r\nfake")
        s = _state("请复刻 target.png 前端页面")
        with patch(
            "reforge.runtime.orchestration.graph.nodes.codegen.LLMClient"
        ) as MockClient:
            vision_instance = MagicMock()
            vision_instance.chat_multimodal.return_value = "from reforge.helpers import screenshot"
            MockClient.for_vision_codegen.return_value = vision_instance

            result = code_generation_node(s)

        assert result["generated_code"] == "from reforge.helpers import screenshot"
        # Text-only path was NOT taken.
        MockClient.return_value.chat.assert_not_called()
        # Vision route called with the discovered image path.
        vision_instance.chat_multimodal.assert_called_once()
        _, _, image_paths = vision_instance.chat_multimodal.call_args.args
        assert list(image_paths) == [cwd_in / "target.png"]

    def test_vision_system_prompt_used_when_routing(self, cwd_in):
        """The vision route must use VISION_CODEGEN_SYSTEM (different
        guidance — model sees the image directly, no describe_image call)."""
        from reforge.models.prompts.templates import VISION_CODEGEN_SYSTEM

        (cwd_in / "target.png").write_bytes(b"\x89PNG\r\nfake")
        s = _state("根据 target.png 复刻前端页面")
        with patch(
            "reforge.runtime.orchestration.graph.nodes.codegen.LLMClient"
        ) as MockClient:
            vision_instance = MagicMock()
            vision_instance.chat_multimodal.return_value = "code"
            MockClient.for_vision_codegen.return_value = vision_instance

            code_generation_node(s)

        sys_prompt = vision_instance.chat_multimodal.call_args.args[0]
        assert sys_prompt == VISION_CODEGEN_SYSTEM

    def test_text_system_prompt_used_when_not_routing(self, cwd_in):
        """The non-vision route must keep the existing CODE_GENERATION_SYSTEM."""
        from reforge.models.prompts.templates import CODE_GENERATION_SYSTEM

        s = _state("Compute pi via Monte Carlo")
        with patch(
            "reforge.runtime.orchestration.graph.nodes.codegen.LLMClient"
        ) as MockClient:
            instance = MagicMock()
            instance.chat.return_value = "code"
            MockClient.return_value = instance
            code_generation_node(s)

        sys_prompt = instance.chat.call_args.args[0]
        assert sys_prompt == CODE_GENERATION_SYSTEM
