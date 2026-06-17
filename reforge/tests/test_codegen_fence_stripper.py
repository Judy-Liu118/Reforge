"""Tests for the markdown fence stripper in codegen.

Regression: qwen3-coder (and occasionally other models) sometimes wraps its
answer in ```python ... ``` with Chinese / English explanation prose around
it. Without stripping, the sandbox sees prose-then-fence and crashes with
SyntaxError on the first non-ASCII character.
"""

from __future__ import annotations

from reforge.runtime.orchestration.graph.nodes.codegen import _strip_markdown


class TestRawCode:
    def test_plain_code_passes_through(self) -> None:
        raw = "import pandas as pd\nprint('hi')\n"
        assert _strip_markdown(raw).strip() == "import pandas as pd\nprint('hi')"

    def test_whitespace_trimmed(self) -> None:
        assert _strip_markdown("  \n\nprint('x')\n\n  ").strip() == "print('x')"


class TestFenceStripping:
    def test_single_python_fence(self) -> None:
        raw = "```python\nimport os\nprint(os.getcwd())\n```"
        assert _strip_markdown(raw) == "import os\nprint(os.getcwd())"

    def test_lowercase_py_fence(self) -> None:
        raw = "```py\nprint('hi')\n```"
        assert _strip_markdown(raw) == "print('hi')"

    def test_unlabeled_fence(self) -> None:
        raw = "```\nprint('hi')\n```"
        assert _strip_markdown(raw) == "print('hi')"

    def test_chinese_preamble_with_fence(self) -> None:
        """The exact failure pattern from the user's Trello run."""
        raw = (
            "我将复刻Trello的前端界面。首先，让我获取目标页面的截图并分析其内容。\n"
            "\n"
            "```python\n"
            "from reforge.helpers import screenshot\n"
            "screenshot('https://trello.com/', 'target.png')\n"
            "```"
        )
        out = _strip_markdown(raw)
        assert "我将复刻" not in out
        assert "screenshot" in out
        assert "from reforge.helpers" in out

    def test_english_preamble_and_postamble(self) -> None:
        raw = (
            "Here's the code:\n\n"
            "```python\nprint('hi')\n```\n\n"
            "Hope this helps!"
        )
        assert _strip_markdown(raw) == "print('hi')"
        assert "Hope" not in _strip_markdown(raw)

    def test_multiple_fenced_blocks_are_concatenated(self) -> None:
        """LLM sometimes splits the code across multiple fences with
        explanation between them. Concatenate in source order."""
        raw = (
            "First, the imports:\n"
            "```python\nimport os\n```\n"
            "Then the body:\n"
            "```python\nprint(os.getcwd())\n```"
        )
        out = _strip_markdown(raw)
        assert "import os" in out
        assert "print(os.getcwd())" in out
        # No prose
        assert "First" not in out
        assert "Then" not in out

    def test_fence_with_extra_blank_lines_inside(self) -> None:
        raw = "```python\n\nimport x\n\nprint('hi')\n\n```"
        out = _strip_markdown(raw)
        assert "import x" in out
        assert "print('hi')" in out

    def test_empty_fence_treated_as_no_fence(self) -> None:
        """An empty fenced block shouldn't return an empty string."""
        raw = "```python\n```\nprint('hi')"  # real code outside fence
        # Empty fence is filtered out; raw was returned as-is
        out = _strip_markdown(raw)
        # Either we return the original raw or skip the empty block — both fine
        # as long as the real print survives somehow.
        assert "print('hi')" in out

    def test_markdown_heading_and_fence(self) -> None:
        """Attempt #2 in the user's log had a `# 复刻 Trello 前端界面` heading."""
        raw = (
            "# 复刻 Trello 前端界面\n\n"
            "我将按照要求复刻 Trello 的前端界面。\n\n"
            "```python\n"
            "from reforge.helpers import screenshot\n"
            "screenshot('https://trello.com/', 'target.png')\n"
            "```"
        )
        out = _strip_markdown(raw)
        # The Chinese characters that broke Python (U+3002 etc.) are gone
        assert "。" not in out
        assert "from reforge.helpers" in out
