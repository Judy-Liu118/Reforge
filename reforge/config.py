from __future__ import annotations

import os

from dotenv import load_dotenv

from reforge.paths import resolve_env_file

_env_path = resolve_env_file()
if _env_path is not None:
    load_dotenv(_env_path)


class Config:
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
    max_retry: int = int(os.getenv("MAX_RETRY", "3"))
    execution_timeout: int = int(os.getenv("EXECUTION_TIMEOUT", "30"))

    # Vision (image understanding) — separate model so the primary text LLM
    # stays unchanged. Defaults target Zhipu GLM-4.6V; any OpenAI-compatible
    # vision endpoint works (set base_url + api_key + model).
    vision_base_url: str = os.getenv(
        "VISION_LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"
    )
    vision_api_key: str = os.getenv("VISION_LLM_API_KEY", "")
    vision_model: str = os.getenv("VISION_LLM_MODEL", "glm-4v-plus")

    # Vision judge (compare_images role) — distinct conceptual role from
    # describe_image. Each judge_* var falls through to its vision_* sibling
    # when empty, so swapping just the model (same provider) is one env var
    # and swapping the whole provider is three. Letting users put a stricter
    # / thinking-capable model behind compare_images while leaving cheap OCR
    # on describe_image.
    vision_judge_base_url: str = (
        os.getenv("VISION_JUDGE_BASE_URL")
        or os.getenv("VISION_LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    )
    vision_judge_api_key: str = (
        os.getenv("VISION_JUDGE_API_KEY") or os.getenv("VISION_LLM_API_KEY", "")
    )
    vision_judge_model: str = (
        os.getenv("VISION_JUDGE_MODEL") or os.getenv("VISION_LLM_MODEL", "glm-4v-plus")
    )

    # Codegen (vision role) — used when the user supplies a target image
    # (target.png in the workspace) and the task is a visual reproduction.
    # The text-only primary LLM can only consume `describe_image` transcripts
    # which discard ~90% of the visual signal (spatial layout, exact colors,
    # font weight). A vision-capable codegen model sees the image directly
    # and can transcribe what it observes into HTML with proper layout.
    # Each codegen_vision_* var falls through to its llm_* sibling when
    # empty, so a single endpoint can serve both text and vision codegen
    # provided the model is multimodal.
    codegen_vision_base_url: str = (
        os.getenv("CODEGEN_VISION_BASE_URL")
        or os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    )
    codegen_vision_api_key: str = (
        os.getenv("CODEGEN_VISION_API_KEY") or os.getenv("LLM_API_KEY", "")
    )
    codegen_vision_model: str = (
        os.getenv("CODEGEN_VISION_MODEL") or os.getenv("LLM_MODEL", "gpt-4o-mini")
    )


config = Config()
