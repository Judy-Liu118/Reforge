"""Tests for vision-routing decision logic + downstream codegen consumption.

When the user references a target image (target.png in the workspace) and
the request matches visual reproduction intent, the runtime must route to
the multimodal vision client instead of the text-only one. The text path
discards ~90% of UI signal (layout / colors / fonts) so vision routing is
the only realistic way to converge on complex SaaS mocks.

Decision is computed once by `vision_routing_node` (writes
state.vision_routing) and *read* by `code_generation_node` (no FS IO).
This split is what the tests below exercise — discovery as a pure
function of (request, workspace), and codegen as a pure function of
the cached decision.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from reforge.runtime.orchestration.graph.nodes.codegen import code_generation_node
from reforge.runtime.orchestration.graph.nodes.vision_routing import vision_routing_node
from reforge.runtime.orchestration.graph.vision_routing import (
    discover_target_images as _discover_target_images,
    resolve_vision_routing,
)
from reforge.runtime.domain.state.models import RuntimeState, VisionRouting


def _state(user_request: str, routing: VisionRouting | None = None) -> RuntimeState:
    return RuntimeState(user_request=user_request, vision_routing=routing)


# ---------------------------------------------------------------------------
# discover_target_images — pure function of (request, workspace)
# ---------------------------------------------------------------------------


class TestTargetImageDiscovery:
    def test_no_image_no_routing(self, tmp_path: Path):
        """No target.* file → no vision route, regardless of intent."""
        assert _discover_target_images("请复刻 target.png", workspace=tmp_path) == []

    def test_png_with_chinese_intent(self, tmp_path: Path):
        (tmp_path / "target.png").write_bytes(b"\x89PNG\r\nfake")
        result = _discover_target_images("请复刻 target.png 前端页面", workspace=tmp_path)
        assert result == [tmp_path / "target.png"]

    def test_png_with_english_intent(self, tmp_path: Path):
        (tmp_path / "target.png").write_bytes(b"\x89PNG\r\nfake")
        result = _discover_target_images(
            "Reproduce the frontend page from target.png", workspace=tmp_path
        )
        assert result == [tmp_path / "target.png"]

    def test_jpg_also_detected(self, tmp_path: Path):
        (tmp_path / "target.jpg").write_bytes(b"\xff\xd8\xff\xe0jpg")
        result = _discover_target_images("复刻 UI 界面", workspace=tmp_path)
        assert result == [tmp_path / "target.jpg"]

    def test_no_visual_intent_no_routing(self, tmp_path: Path):
        """target.png exists but request is data analysis — text path is
        correct (sending the image would waste tokens and confuse the model)."""
        (tmp_path / "target.png").write_bytes(b"\x89PNG\r\nfake")
        result = _discover_target_images(
            "Read sales.csv and compute average revenue", workspace=tmp_path
        )
        assert result == []

    def test_visual_intent_without_image_returns_empty(self, tmp_path: Path):
        """User asks to reproduce a UI but supplied no target.png — runtime
        should NOT route to vision (model would have no image to look at);
        let the text path explain the gap or fail clearly."""
        result = _discover_target_images("复刻一个前端页面", workspace=tmp_path)
        assert result == []

    @pytest.mark.parametrize(
        "request_text",
        [
            "build a circuit board simulation",
            "write a guide for migrating data",
            "create a suite of unit tests",
            "fix a build error",
        ],
    )
    def test_bare_UI_substring_does_not_false_positive(
        self, tmp_path: Path, request_text: str
    ):
        """Regression: bare /UI/ inside `re.IGNORECASE` was matching substrings
        of build/guide/suite/circuit and routing pure-text tasks to vision.
        \\bUI\\b anchors fix it."""
        (tmp_path / "target.png").write_bytes(b"\x89PNG\r\nfake")
        result = _discover_target_images(request_text, workspace=tmp_path)
        assert result == [], f"{request_text!r} should not trigger vision routing"


# ---------------------------------------------------------------------------
# vision_routing_node — wraps discover_target_images and writes state
# ---------------------------------------------------------------------------


class TestVisionRoutingNode:
    def test_writes_use_vision_true_when_image_and_intent(self, tmp_path: Path):
        (tmp_path / "target.png").write_bytes(b"\x89PNG\r\nfake")
        s = _state("复刻 target.png 页面")
        result = vision_routing_node(s, workspace=tmp_path)
        assert isinstance(result["vision_routing"], VisionRouting)
        assert result["vision_routing"].use_vision is True
        assert result["vision_routing"].target_images == [str(tmp_path / "target.png")]

    def test_writes_use_vision_false_when_intent_only(self, tmp_path: Path):
        s = _state("复刻一个前端页面")
        result = vision_routing_node(s, workspace=tmp_path)
        assert result["vision_routing"].use_vision is False
        assert result["vision_routing"].target_images == []

    def test_writes_use_vision_false_when_image_only(self, tmp_path: Path):
        (tmp_path / "target.png").write_bytes(b"\x89PNG\r\nfake")
        s = _state("compute average revenue from sales.csv")
        result = vision_routing_node(s, workspace=tmp_path)
        assert result["vision_routing"].use_vision is False

    def test_resolve_returns_paths_as_strings(self, tmp_path: Path):
        (tmp_path / "target.png").write_bytes(b"\x89PNG\r\nfake")
        decision = resolve_vision_routing("复刻 target.png 前端", workspace=tmp_path)
        assert decision.target_images == [str(tmp_path / "target.png")]


# ---------------------------------------------------------------------------
# code_generation_node — reads state.vision_routing (does no FS IO itself)
# ---------------------------------------------------------------------------


def _routing_off() -> VisionRouting:
    return VisionRouting(use_vision=False, target_images=[])


def _routing_on(image_path: Path) -> VisionRouting:
    return VisionRouting(use_vision=True, target_images=[str(image_path)])


class TestCodegenNodeRouting:
    def test_text_path_when_routing_off(self):
        """vision_routing.use_vision=False → plain LLMClient.chat path."""
        s = _state("Add 1 + 1 and print the result", routing=_routing_off())
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

    def test_text_path_when_routing_missing(self):
        """vision_routing field absent (e.g. legacy state) → text path."""
        s = _state("Compute pi via Monte Carlo")
        assert s.vision_routing is None
        with patch(
            "reforge.runtime.orchestration.graph.nodes.codegen.LLMClient"
        ) as MockClient:
            instance = MagicMock()
            instance.chat.return_value = "code"
            MockClient.return_value = instance
            code_generation_node(s)
        instance.chat.assert_called_once()
        MockClient.for_vision_codegen.assert_not_called()

    def test_vision_path_when_routing_on(self, tmp_path: Path):
        """vision_routing.use_vision=True → multimodal client + image paths."""
        image_path = tmp_path / "target.png"
        s = _state("请复刻 target.png 前端页面", routing=_routing_on(image_path))
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

    def test_vision_system_prompt_used_when_routing_on(self, tmp_path: Path):
        from reforge.models.prompts.templates import VISION_CODEGEN_SYSTEM

        s = _state("根据 target.png 复刻前端", routing=_routing_on(tmp_path / "target.png"))
        with patch(
            "reforge.runtime.orchestration.graph.nodes.codegen.LLMClient"
        ) as MockClient:
            vision_instance = MagicMock()
            vision_instance.chat_multimodal.return_value = "code"
            MockClient.for_vision_codegen.return_value = vision_instance
            code_generation_node(s)
        sys_prompt = vision_instance.chat_multimodal.call_args.args[0]
        assert sys_prompt == VISION_CODEGEN_SYSTEM

    def test_text_system_prompt_used_when_routing_off(self):
        from reforge.models.prompts.templates import CODE_GENERATION_SYSTEM

        s = _state("Compute pi via Monte Carlo", routing=_routing_off())
        with patch(
            "reforge.runtime.orchestration.graph.nodes.codegen.LLMClient"
        ) as MockClient:
            instance = MagicMock()
            instance.chat.return_value = "code"
            MockClient.return_value = instance
            code_generation_node(s)
        sys_prompt = instance.chat.call_args.args[0]
        assert sys_prompt == CODE_GENERATION_SYSTEM
