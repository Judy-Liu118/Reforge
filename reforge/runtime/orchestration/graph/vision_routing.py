"""Visual reproduction intent detection for the codegen node.

When the user attaches a target image AND asks to reproduce a visual UI,
codegen must route through a multimodal LLM that can see the pixels —
text-only intermediaries (`describe_image` transcription) drop ~90% of
the relevant signal (spatial layout, exact colors, font weight).

Kept out of `nodes/codegen.py` so that file stays under its size budget
and the routing rule is testable in isolation.
"""

from __future__ import annotations

import re
from pathlib import Path

# Filenames the codegen scans for when deciding whether to route to the
# vision codegen path. The convention `target.<ext>` is documented in the
# visual self-heal system prompt; any of these extensions counts. The
# search is anchored to Path.cwd() (== sandbox workspace) so it matches
# what the generated script will see at runtime.
_TARGET_IMAGE_NAMES = (
    "target.png",
    "target.jpg",
    "target.jpeg",
    "target.webp",
)

# Verbs / phrases that, together with a target image, signal a visual
# reproduction intent. Err on the side of routing to vision when in
# doubt: a vision model can ignore an image and handle a text task, but
# the text model can never recover pixels it never saw.
_VISUAL_INTENT_RE = re.compile(
    r"(复刻|复现|重现|仿做|reproduce|recreate|replicate|clone|render|"
    r"build.*(from|based on).*(image|screenshot|mockup|design)|"
    r"front.?end|web.?page|UI|界面|页面|前端)",
    re.IGNORECASE,
)


def discover_target_images(user_request: str) -> list[Path]:
    """Return paths of target images present in the workspace for vision codegen.

    A visual reproduction task is identified when BOTH:
      1. The user_request mentions visual / front-end intent (复刻 / 复现 /
         reproduce / 前端 / UI / …), AND
      2. At least one well-known target file (target.png/.jpg/.jpeg/.webp)
         exists in the sandbox workspace (== Path.cwd()).

    Returning [] disables the vision codegen route — the text-only path
    handles non-visual tasks unchanged. The double-gate avoids routing
    data-analysis tasks (that happen to also have a target.png alongside
    them) into a vision model that would pointlessly burn tokens.
    """
    if not _VISUAL_INTENT_RE.search(user_request or ""):
        return []
    workspace = Path.cwd()
    found: list[Path] = []
    for name in _TARGET_IMAGE_NAMES:
        candidate = workspace / name
        if candidate.is_file():
            found.append(candidate)
    return found
