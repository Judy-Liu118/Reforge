"""Built-in skills shipped with Reforge."""

import importlib.util
import os

from reforge.runtime.skills.builtin.edit import EditSkill
from reforge.runtime.skills.builtin.glob_skill import GlobSkill
from reforge.runtime.skills.builtin.grep import GrepSkill
from reforge.runtime.skills.builtin.image_compare import CompareImagesSkill
from reforge.runtime.skills.builtin.python_sandbox import PythonSandboxSkill
from reforge.runtime.skills.builtin.read import ReadSkill
from reforge.runtime.skills.builtin.vision import VisionDescribeSkill
from reforge.runtime.skills.builtin.web_screenshot import WebScreenshotSkill
from reforge.runtime.skills.builtin.web_search import (
    SearchProvider,
    SearchResult,
    TavilyProvider,
    WebSearchSkill,
)

__all__ = [
    "CompareImagesSkill",
    "EditSkill",
    "GlobSkill",
    "GrepSkill",
    "PythonSandboxSkill",
    "ReadSkill",
    "SearchProvider",
    "SearchResult",
    "TavilyProvider",
    "VisionDescribeSkill",
    "WebScreenshotSkill",
    "WebSearchSkill",
]


def _playwright_available() -> bool:
    return importlib.util.find_spec("playwright") is not None


def default_skill_registry(
    *,
    include_web_search: bool | None = None,
    include_vision: bool | None = None,
    include_web_screenshot: bool | None = None,
    include_image_compare: bool | None = None,
):
    """Build a SkillRegistry pre-populated with all built-in skills.

    Always registers: python_sandbox + read + grep + glob + edit.
    Optional skills are auto-detected from environment / installed packages
    when their include flag is left at None:
      * web_search       — TAVILY_API_KEY
      * vision_describe  — VISION_LLM_API_KEY
      * web_screenshot   — playwright importable
      * compare_images   — VISION_LLM_API_KEY (shares the vision endpoint)
    """
    from reforge.runtime.skills.registry import SkillRegistry

    reg = SkillRegistry()
    reg.register(PythonSandboxSkill())
    reg.register(ReadSkill())
    reg.register(GrepSkill())
    reg.register(GlobSkill())
    reg.register(EditSkill())

    if include_web_search is None:
        include_web_search = bool(os.environ.get("TAVILY_API_KEY"))
    if include_web_search:
        reg.register(WebSearchSkill())

    if include_vision is None:
        include_vision = bool(os.environ.get("VISION_LLM_API_KEY"))
    if include_vision:
        reg.register(VisionDescribeSkill())

    if include_web_screenshot is None:
        include_web_screenshot = _playwright_available()
    if include_web_screenshot:
        reg.register(WebScreenshotSkill())

    if include_image_compare is None:
        include_image_compare = bool(os.environ.get("VISION_LLM_API_KEY"))
    if include_image_compare:
        reg.register(CompareImagesSkill())

    return reg
