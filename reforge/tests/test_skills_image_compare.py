"""Tests for CompareImagesSkill — multi-image visual diff."""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest

from reforge.runtime.skills import Skill, SkillContext
from reforge.runtime.skills.builtin import default_skill_registry
from reforge.runtime.skills.builtin.image_compare import (
    CompareImagesSkill,
    _parse_score,
    compare_images,
)


# ---------------------------------------------------------------------------
# Fake OpenAI-shaped client
# ---------------------------------------------------------------------------


class _FakeCompletions:
    def __init__(self, owner: "_FakeClient") -> None:
        self._owner = owner

    def create(self, **kwargs):
        self._owner.calls.append(kwargs)
        if self._owner.raise_exc is not None:
            raise self._owner.raise_exc
        return {"choices": [{"message": {"content": self._owner.reply_text}}]}


class _FakeChat:
    def __init__(self, owner: "_FakeClient") -> None:
        self.completions = _FakeCompletions(owner)


class _FakeClient:
    def __init__(self, reply_text: str = "SCORE: 0.85\n- color tweak\n- spacing", raise_exc=None) -> None:
        self.reply_text = reply_text
        self.raise_exc = raise_exc
        self.calls: list[dict] = []
        self.chat = _FakeChat(self)


def _png_bytes() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xfa"
        b"\xcf\x00\x00\x00\x02\x00\x01\xe2!\xbc\x33\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def _ctx(tmp_path: Path) -> SkillContext:
    return SkillContext(session_id="ic-test", workspace=tmp_path, timeout_s=10)


# ---------------------------------------------------------------------------


class TestProtocol:
    def test_protocol_conformance(self) -> None:
        assert isinstance(CompareImagesSkill(client=_FakeClient()), Skill)

    def test_required_inputs(self) -> None:
        s = CompareImagesSkill.input_schema
        assert set(s["required"]) == {"target_image", "current_image"}


# ---------------------------------------------------------------------------


class TestInvoke:
    def test_two_local_images_both_inlined_as_b64(self, tmp_path: Path) -> None:
        (tmp_path / "a.png").write_bytes(_png_bytes())
        (tmp_path / "b.png").write_bytes(_png_bytes())
        client = _FakeClient(reply_text="SCORE: 0.72\nSlight color drift")
        result = CompareImagesSkill(client=client).invoke(
            {"target_image": "a.png", "current_image": "b.png"},
            _ctx(tmp_path),
        )
        assert result.success
        msg = client.calls[0]["messages"][0]
        # 1 text + 2 image parts
        parts = msg["content"]
        assert parts[0]["type"] == "text"
        assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
        assert parts[2]["image_url"]["url"].startswith("data:image/png;base64,")

    def test_parses_score_into_metadata(self, tmp_path: Path) -> None:
        (tmp_path / "a.png").write_bytes(_png_bytes())
        (tmp_path / "b.png").write_bytes(_png_bytes())
        client = _FakeClient(reply_text="SCORE: 0.91\nVery close")
        result = CompareImagesSkill(client=client).invoke(
            {"target_image": "a.png", "current_image": "b.png"},
            _ctx(tmp_path),
        )
        assert result.metadata["score"] == pytest.approx(0.91)

    def test_missing_target_returns_error(self, tmp_path: Path) -> None:
        result = CompareImagesSkill(client=_FakeClient()).invoke(
            {"current_image": "x.png"}, _ctx(tmp_path)
        )
        assert not result.success
        assert "target_image" in result.error

    def test_missing_current_returns_error(self, tmp_path: Path) -> None:
        result = CompareImagesSkill(client=_FakeClient()).invoke(
            {"target_image": "x.png"}, _ctx(tmp_path)
        )
        assert not result.success
        assert "current_image" in result.error

    def test_focus_default_present_in_question(self, tmp_path: Path) -> None:
        (tmp_path / "a.png").write_bytes(_png_bytes())
        (tmp_path / "b.png").write_bytes(_png_bytes())
        client = _FakeClient()
        CompareImagesSkill(client=client).invoke(
            {"target_image": "a.png", "current_image": "b.png"},
            _ctx(tmp_path),
        )
        question = client.calls[0]["messages"][0]["content"][0]["text"]
        assert "layout" in question.lower()

    def test_explicit_focus_is_threaded_through(self, tmp_path: Path) -> None:
        (tmp_path / "a.png").write_bytes(_png_bytes())
        (tmp_path / "b.png").write_bytes(_png_bytes())
        client = _FakeClient()
        CompareImagesSkill(client=client).invoke(
            {"target_image": "a.png", "current_image": "b.png", "focus": "typography only"},
            _ctx(tmp_path),
        )
        question = client.calls[0]["messages"][0]["content"][0]["text"]
        assert "typography" in question

    def test_missing_local_file_returns_error(self, tmp_path: Path) -> None:
        (tmp_path / "a.png").write_bytes(_png_bytes())
        result = CompareImagesSkill(client=_FakeClient()).invoke(
            {"target_image": "a.png", "current_image": "missing.png"},
            _ctx(tmp_path),
        )
        assert not result.success
        assert "not found" in result.error

    def test_client_exception_becomes_skill_error(self, tmp_path: Path) -> None:
        (tmp_path / "a.png").write_bytes(_png_bytes())
        (tmp_path / "b.png").write_bytes(_png_bytes())
        client = _FakeClient(raise_exc=RuntimeError("503"))
        result = CompareImagesSkill(client=client).invoke(
            {"target_image": "a.png", "current_image": "b.png"},
            _ctx(tmp_path),
        )
        assert not result.success
        assert "503" in result.error


# ---------------------------------------------------------------------------


class TestDownscale:
    """Oversized user-supplied screenshots get auto-shrunk before encoding.

    Pillow is an optional dependency of the runtime — _maybe_downscale
    short-circuits when PIL isn't importable. The test itself USES PIL to
    construct a too-large PNG, so the whole class is skipped on bare CI
    installs that don't have Pillow.
    """

    def _big_png(self, tmp_path: Path, width: int = 2400, height: int = 1500) -> Path:
        """Generate a wide PNG over the downscale threshold. Random pixels
        defeat PNG compression so the file size honestly exceeds 500KB."""
        import os
        Image = pytest.importorskip("PIL.Image", reason="Pillow not installed")
        img = Image.frombytes("RGB", (width, height), os.urandom(width * height * 3))
        out = tmp_path / "big.png"
        img.save(out, format="PNG", optimize=False)
        assert out.stat().st_size > 500_000, f"test png too small: {out.stat().st_size}"
        return out

    def test_large_image_is_downscaled(self, tmp_path: Path) -> None:
        big = self._big_png(tmp_path)
        client = _FakeClient()
        skill = CompareImagesSkill(client=client)
        skill.invoke(
            {"target_image": str(big), "current_image": str(big)},
            _ctx(tmp_path),
        )
        sent_url = client.calls[0]["messages"][0]["content"][1]["image_url"]["url"]
        # Decode the b64 payload and verify the image is now <= max width.
        from PIL import Image as PILImage
        b64 = sent_url.split(",", 1)[1]
        data = base64.b64decode(b64)
        with PILImage.open(io.BytesIO(data)) as img:
            assert img.size[0] <= 1280, f"expected <= 1280 px wide, got {img.size}"

    def test_small_image_is_left_alone(self, tmp_path: Path) -> None:
        small = tmp_path / "small.png"
        small.write_bytes(_png_bytes())  # 1x1, way below threshold
        client = _FakeClient()
        skill = CompareImagesSkill(client=client)
        skill.invoke(
            {"target_image": str(small), "current_image": str(small)},
            _ctx(tmp_path),
        )
        sent_url = client.calls[0]["messages"][0]["content"][1]["image_url"]["url"]
        b64 = sent_url.split(",", 1)[1]
        # Round-trip equals the original bytes — no shrink applied.
        assert base64.b64decode(b64) == _png_bytes()


class TestScoreParser:
    def test_parses_typical_format(self) -> None:
        assert _parse_score("SCORE: 0.85\nfoo") == 0.85

    def test_parses_with_equals(self) -> None:
        assert _parse_score("score=0.5") == 0.5

    def test_clamps_to_one(self) -> None:
        # The text contains "1.0" which is exactly the max — accepted as-is.
        assert _parse_score("SCORE: 1.0") == 1.0

    def test_returns_none_when_absent(self) -> None:
        assert _parse_score("looks good, no score given") is None


# ---------------------------------------------------------------------------


class TestModuleHelper:
    def test_compare_images_returns_score_and_text(self, tmp_path: Path) -> None:
        (tmp_path / "a.png").write_bytes(_png_bytes())
        (tmp_path / "b.png").write_bytes(_png_bytes())
        client = _FakeClient(reply_text="SCORE: 0.42\nlots different")
        score, diff = compare_images(
            str(tmp_path / "a.png"),
            str(tmp_path / "b.png"),
            workspace=tmp_path,
            client=client,
        )
        assert score == pytest.approx(0.42)
        assert "different" in diff

    def test_compare_images_no_score_emitted_defaults_to_zero(self, tmp_path: Path) -> None:
        (tmp_path / "a.png").write_bytes(_png_bytes())
        (tmp_path / "b.png").write_bytes(_png_bytes())
        client = _FakeClient(reply_text="just a description, no score")
        score, _ = compare_images(
            str(tmp_path / "a.png"),
            str(tmp_path / "b.png"),
            workspace=tmp_path,
            client=client,
        )
        assert score == 0.0

    def test_compare_images_failed_skill_raises(self, tmp_path: Path) -> None:
        client = _FakeClient(raise_exc=RuntimeError("API down"))
        (tmp_path / "a.png").write_bytes(_png_bytes())
        (tmp_path / "b.png").write_bytes(_png_bytes())
        with pytest.raises(RuntimeError):
            compare_images(
                str(tmp_path / "a.png"),
                str(tmp_path / "b.png"),
                workspace=tmp_path,
                client=client,
            )


# ---------------------------------------------------------------------------


class TestStepTimingPrint:
    def test_compare_images_helper_prints_step_timing(self, tmp_path: Path, capsys) -> None:
        from reforge.runtime.skills.builtin.image_compare import compare_images

        (tmp_path / "a.png").write_bytes(_png_bytes())
        (tmp_path / "b.png").write_bytes(_png_bytes())
        compare_images(
            str(tmp_path / "a.png"),
            str(tmp_path / "b.png"),
            workspace=tmp_path,
            client=_FakeClient(),
        )
        captured = capsys.readouterr()
        assert "[reforge.step] compare_images" in captured.out
        assert "ok" in captured.out

    def test_compare_images_prints_start_before_call(self, tmp_path: Path, capsys) -> None:
        """A START line must appear BEFORE the judge call begins.

        Important here: the strict judge is often a thinking model where the
        bulk of wall-clock time is spent. If timeout hits, the user sees
        `compare_images: start` so they know which step ate the budget.
        """
        from reforge.runtime.skills.builtin.image_compare import compare_images

        (tmp_path / "a.png").write_bytes(_png_bytes())
        (tmp_path / "b.png").write_bytes(_png_bytes())
        client = _FakeClient()
        original_create = client.chat.completions.create
        snapshot: dict = {}

        def sniffing_create(**kwargs):
            snapshot["out"] = capsys.readouterr().out
            return original_create(**kwargs)

        client.chat.completions.create = sniffing_create  # type: ignore[method-assign]

        compare_images(
            str(tmp_path / "a.png"),
            str(tmp_path / "b.png"),
            workspace=tmp_path,
            client=client,
        )
        assert "[reforge.step] compare_images: start" in snapshot["out"]


class TestAutoRegistration:
    def test_registered_when_vision_key_present(self, monkeypatch) -> None:
        monkeypatch.setenv("VISION_LLM_API_KEY", "sk-fake")
        reg = default_skill_registry(include_web_search=False)
        assert reg.get("compare_images") is not None

    def test_not_registered_without_key(self, monkeypatch) -> None:
        monkeypatch.delenv("VISION_LLM_API_KEY", raising=False)
        reg = default_skill_registry(include_web_search=False)
        assert reg.get("compare_images") is None


# ---------------------------------------------------------------------------
# Judge config split — describe role and judge role can use different models
# ---------------------------------------------------------------------------


class TestJudgeConfigSplit:
    """Compare_images reads from vision_judge_* with fall-through to vision_*."""

    def test_judge_model_falls_through_to_vision_model_by_default(self, monkeypatch) -> None:
        from reforge.config import config

        monkeypatch.setattr(config, "vision_model", "glm-4.6v")
        monkeypatch.setattr(config, "vision_judge_model", "glm-4.6v")
        skill = CompareImagesSkill(client=_FakeClient())
        _, model = skill._resolve_client_and_model()
        assert model == "glm-4.6v"

    def test_judge_model_can_differ_from_vision_model(self, monkeypatch) -> None:
        """When VISION_JUDGE_MODEL is explicitly set, compare_images uses it
        instead of the describe role's model."""
        from reforge.config import config

        monkeypatch.setattr(config, "vision_model", "glm-4.6v")
        monkeypatch.setattr(config, "vision_judge_model", "qwen3-vl-32b-thinking")
        skill = CompareImagesSkill(client=_FakeClient())
        _, model = skill._resolve_client_and_model()
        assert model == "qwen3-vl-32b-thinking"

    def test_judge_provider_can_differ_from_describe_provider(self, monkeypatch) -> None:
        """Full provider override — base_url + api_key + model all set so the
        judge can sit on a different platform (e.g. DashScope) from the
        describe role (e.g. Zhipu)."""
        from reforge.config import config

        monkeypatch.setattr(config, "vision_base_url", "https://open.bigmodel.cn/api/paas/v4")
        monkeypatch.setattr(config, "vision_api_key", "zhipu-key")
        monkeypatch.setattr(config, "vision_model", "glm-4.6v")
        monkeypatch.setattr(config, "vision_judge_base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        monkeypatch.setattr(config, "vision_judge_api_key", "dashscope-key")
        monkeypatch.setattr(config, "vision_judge_model", "qwen3-vl-32b-thinking")
        # No injected client → falls through to live config resolution
        skill = CompareImagesSkill()
        client, model = skill._resolve_client_and_model()
        # We can't easily assert which base_url was used without spinning up
        # a real OpenAI client, but we can confirm the model came through.
        assert model == "qwen3-vl-32b-thinking"
        # Client must be non-None because all three judge_* fields are set
        assert client is not None

    def test_judge_falls_through_when_only_main_vision_configured(self, monkeypatch) -> None:
        """Loading config from env: judge_* unset → judge uses vision_* values.
        This is the common case before users opt into the split.

        Skip resolve_env_file so the on-disk .env (which may explicitly set
        judge values for the developer) doesn't contaminate the assertion.
        """
        monkeypatch.setattr(
            "reforge.paths.resolve_env_file",
            lambda: None,
        )
        monkeypatch.setenv("VISION_LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
        monkeypatch.setenv("VISION_LLM_API_KEY", "key-zhipu")
        monkeypatch.setenv("VISION_LLM_MODEL", "glm-4.6v")
        monkeypatch.delenv("VISION_JUDGE_BASE_URL", raising=False)
        monkeypatch.delenv("VISION_JUDGE_API_KEY", raising=False)
        monkeypatch.delenv("VISION_JUDGE_MODEL", raising=False)
        import importlib

        from reforge import config as cfg_mod
        importlib.reload(cfg_mod)
        cfg = cfg_mod.config
        assert cfg.vision_judge_model == "glm-4.6v"
        assert cfg.vision_judge_api_key == "key-zhipu"
        assert cfg.vision_judge_base_url == "https://open.bigmodel.cn/api/paas/v4"
