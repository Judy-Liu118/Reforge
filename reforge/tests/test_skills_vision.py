"""Tests for VisionDescribeSkill.

Strategy: cover behaviour with a hand-written fake OpenAI-compatible client —
never hit the real GLM-4.6V API in CI. The fake records the messages it
receives so we can assert what shape ends up on the wire.
"""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import patch

import pytest

from reforge.runtime.skills import Skill, SkillContext, SkillResult
from reforge.runtime.skills.builtin import default_skill_registry
from reforge.runtime.skills.builtin.vision import (
    VisionDescribeSkill,
    VisionInputError,
    _extract_text,
    _resolve_image_url,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(tmp_path: Path) -> SkillContext:
    return SkillContext(session_id="vision-test", workspace=tmp_path, timeout_s=10)


class _FakeCompletions:
    def __init__(self, fake_client: "_FakeClient") -> None:
        self._client = fake_client

    def create(self, **kwargs):
        self._client.calls.append(kwargs)
        if self._client.raise_exc is not None:
            raise self._client.raise_exc
        return {
            "choices": [
                {"message": {"content": self._client.reply_text}}
            ]
        }


class _FakeClient:
    """Minimal OpenAI-shaped client for tests."""

    def __init__(self, reply_text: str = "a picture of a cat", raise_exc: Exception | None = None) -> None:
        self.reply_text = reply_text
        self.raise_exc = raise_exc
        self.calls: list[dict] = []
        self.chat = _FakeChat(self)


class _FakeChat:
    def __init__(self, client: _FakeClient) -> None:
        self.completions = _FakeCompletions(client)


def _png_bytes() -> bytes:
    # 1x1 transparent PNG, smallest valid file.
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xfa"
        b"\xcf\x00\x00\x00\x02\x00\x01\xe2!\xbc\x33\x00\x00\x00\x00IEND\xaeB`\x82"
    )


# ---------------------------------------------------------------------------
# Protocol + schema
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_satisfies_skill_protocol(self) -> None:
        assert isinstance(VisionDescribeSkill(client=_FakeClient()), Skill)

    def test_schema_requires_image_path(self) -> None:
        schema = VisionDescribeSkill.input_schema
        assert "image_path" in schema["required"]
        assert "image_path" in schema["properties"]
        assert "question" in schema["properties"]

    def test_name_and_description(self) -> None:
        assert VisionDescribeSkill.name == "vision_describe"
        assert VisionDescribeSkill.description


# ---------------------------------------------------------------------------
# Image URL resolution
# ---------------------------------------------------------------------------


class TestImageUrlResolution:
    def test_http_url_passes_through(self) -> None:
        assert _resolve_image_url("https://example.com/x.png") == "https://example.com/x.png"
        assert _resolve_image_url("http://example.com/x.png") == "http://example.com/x.png"

    def test_data_url_passes_through(self) -> None:
        url = "data:image/png;base64,AAAA"
        assert _resolve_image_url(url) == url

    def test_local_file_inlined_as_b64(self, tmp_path: Path) -> None:
        img = tmp_path / "shot.png"
        img.write_bytes(_png_bytes())
        url = _resolve_image_url(str(img))
        assert url.startswith("data:image/png;base64,")
        # round-trip — the base64 portion decodes back to original bytes
        b64 = url.split(",", 1)[1]
        assert base64.b64decode(b64) == _png_bytes()

    def test_missing_local_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(VisionInputError):
            _resolve_image_url(str(tmp_path / "missing.png"))

    def test_unknown_extension_defaults_to_png(self, tmp_path: Path) -> None:
        img = tmp_path / "noext"
        img.write_bytes(_png_bytes())
        url = _resolve_image_url(str(img))
        # Either png (default) or some image/* — both acceptable.
        assert url.startswith("data:image/")


# ---------------------------------------------------------------------------
# Invoke behaviour
# ---------------------------------------------------------------------------


class TestInvoke:
    def test_url_passes_through_to_client(self, tmp_path: Path) -> None:
        client = _FakeClient(reply_text="a cat")
        skill = VisionDescribeSkill(client=client)
        result = skill.invoke(
            {"image_path": "https://example.com/cat.png", "question": "what animal"},
            _ctx(tmp_path),
        )
        assert result.success
        assert "cat" in result.output

        # exactly one call, with the URL in the image_url part
        assert len(client.calls) == 1
        msg = client.calls[0]["messages"][0]
        assert msg["role"] == "user"
        parts = msg["content"]
        assert parts[0] == {"type": "text", "text": "what animal"}
        assert parts[1]["image_url"]["url"] == "https://example.com/cat.png"

    def test_local_path_is_b64_inlined(self, tmp_path: Path) -> None:
        img = tmp_path / "shot.png"
        img.write_bytes(_png_bytes())

        client = _FakeClient(reply_text="a one-pixel image")
        skill = VisionDescribeSkill(client=client)
        result = skill.invoke({"image_path": str(img)}, _ctx(tmp_path))

        assert result.success
        sent_url = client.calls[0]["messages"][0]["content"][1]["image_url"]["url"]
        assert sent_url.startswith("data:image/png;base64,")
        # metadata records the source as local
        assert result.metadata["image_source"] == "local"

    def test_default_question_used_when_absent(self, tmp_path: Path) -> None:
        client = _FakeClient()
        skill = VisionDescribeSkill(client=client)
        result = skill.invoke({"image_path": "https://x.com/y.png"}, _ctx(tmp_path))
        assert result.success
        text_part = client.calls[0]["messages"][0]["content"][0]["text"]
        assert "describe" in text_part.lower()
        assert result.metadata["question_was_default"] is True

    def test_missing_image_path_returns_error(self, tmp_path: Path) -> None:
        skill = VisionDescribeSkill(client=_FakeClient())
        result = skill.invoke({}, _ctx(tmp_path))
        assert result.success is False
        assert "image_path" in result.error

    def test_empty_image_path_returns_error(self, tmp_path: Path) -> None:
        skill = VisionDescribeSkill(client=_FakeClient())
        result = skill.invoke({"image_path": "   "}, _ctx(tmp_path))
        assert result.success is False

    def test_missing_local_file_returns_error(self, tmp_path: Path) -> None:
        skill = VisionDescribeSkill(client=_FakeClient())
        result = skill.invoke(
            {"image_path": str(tmp_path / "does_not_exist.png")},
            _ctx(tmp_path),
        )
        assert result.success is False
        assert "not found" in result.error

    def test_client_exception_becomes_skill_error(self, tmp_path: Path) -> None:
        client = _FakeClient(raise_exc=RuntimeError("503 Service Unavailable"))
        skill = VisionDescribeSkill(client=client)
        result = skill.invoke({"image_path": "https://x.com/y.png"}, _ctx(tmp_path))
        assert result.success is False
        assert "RuntimeError" in result.error
        assert "503" in result.error
        # duration is still measured
        assert result.duration_ms >= 0

    def test_long_output_truncated(self, tmp_path: Path) -> None:
        long_text = "x" * 10_000
        client = _FakeClient(reply_text=long_text)
        skill = VisionDescribeSkill(client=client)
        result = skill.invoke({"image_path": "https://x.com/y.png"}, _ctx(tmp_path))
        assert result.success
        assert len(result.output) <= 4100  # 4000 + truncate marker
        assert "truncated" in result.output


# ---------------------------------------------------------------------------
# Config gate — no client available
# ---------------------------------------------------------------------------


class TestConfigGate:
    def test_missing_config_returns_graceful_error(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Without VISION_LLM_API_KEY set, invoke should return success=False
        with a clear message — not crash."""
        from reforge.config import config as cfg

        monkeypatch.setattr(cfg, "vision_api_key", "")
        skill = VisionDescribeSkill()  # no injected client
        result = skill.invoke({"image_path": "https://x.com/y.png"}, _ctx(tmp_path))
        assert result.success is False
        assert "vision client" in result.error.lower()


# ---------------------------------------------------------------------------
# Auto-registration
# ---------------------------------------------------------------------------


class TestStepTimingPrint:
    def test_describe_image_helper_prints_step_timing(self, tmp_path: Path, capsys) -> None:
        from reforge.runtime.skills.builtin.vision import describe_image

        img = tmp_path / "shot.png"
        img.write_bytes(_png_bytes())
        describe_image(str(img), workspace=tmp_path, client=_FakeClient())
        captured = capsys.readouterr()
        assert "[reforge.step] describe_image" in captured.out
        assert "ok" in captured.out

    def test_describe_image_prints_start_before_call(self, tmp_path: Path, capsys) -> None:
        """A START line must appear BEFORE the vision API call begins.

        Critical when subprocess is killed mid-call: without the START print,
        the user can't tell which step was active when the budget ran out.
        """
        from reforge.runtime.skills.builtin.vision import describe_image

        client = _FakeClient()
        original_create = client.chat.completions.create
        snapshot: dict = {}

        def sniffing_create(**kwargs):
            # Snapshot stdout right as the API would be called — START line
            # must already be present at this point.
            snapshot["out"] = capsys.readouterr().out
            return original_create(**kwargs)

        client.chat.completions.create = sniffing_create  # type: ignore[method-assign]

        img = tmp_path / "shot.png"
        img.write_bytes(_png_bytes())
        describe_image(str(img), workspace=tmp_path, client=client)
        assert "[reforge.step] describe_image: start" in snapshot["out"]


class TestAutoRegistration:
    def test_registered_when_api_key_present(self, monkeypatch) -> None:
        monkeypatch.setenv("VISION_LLM_API_KEY", "sk-fake")
        reg = default_skill_registry(include_web_search=False)
        assert reg.get("vision_describe") is not None

    def test_not_registered_when_api_key_absent(self, monkeypatch) -> None:
        monkeypatch.delenv("VISION_LLM_API_KEY", raising=False)
        reg = default_skill_registry(include_web_search=False)
        assert reg.get("vision_describe") is None

    def test_explicit_include_overrides_env(self, monkeypatch) -> None:
        monkeypatch.delenv("VISION_LLM_API_KEY", raising=False)
        reg = default_skill_registry(include_web_search=False, include_vision=True)
        assert reg.get("vision_describe") is not None


# ---------------------------------------------------------------------------
# _extract_text — robustness across response shapes
# ---------------------------------------------------------------------------


class TestExtractText:
    def test_dict_response(self) -> None:
        resp = {"choices": [{"message": {"content": "hello"}}]}
        assert _extract_text(resp) == "hello"

    def test_object_response(self) -> None:
        class Msg:
            content = "hello"

        class Choice:
            message = Msg()

        class Resp:
            choices = [Choice()]

        assert _extract_text(Resp()) == "hello"

    def test_list_content(self) -> None:
        resp = {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "text", "text": "part one"},
                            {"type": "text", "text": "part two"},
                        ]
                    }
                }
            ]
        }
        assert "part one" in _extract_text(resp)
        assert "part two" in _extract_text(resp)

    def test_malformed_returns_empty_string(self) -> None:
        assert _extract_text({}) == ""
        assert _extract_text(None) == ""
        assert _extract_text({"choices": []}) == ""
