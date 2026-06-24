"""Visual reproduction intent detection for the codegen node.

When the user attaches a target image AND asks to reproduce a visual UI,
codegen must route through a multimodal LLM that can see the pixels —
text-only intermediaries (`describe_image` transcription) drop ~90% of
the relevant signal (spatial layout, exact colors, font weight).

Kept out of `nodes/codegen.py` so that file stays under its size budget
and the routing rule is testable in isolation. The graph wires a
dedicated vision_routing_node ahead of code_generation; this module
owns the regex + filesystem scan and exposes them as pure functions
parameterised on workspace path (no Path.cwd() inside).
"""

from __future__ import annotations

import re
from pathlib import Path

from reforge.runtime.domain.state.models import VisionRouting

# Filenames the codegen scans for when deciding whether to route to the
# vision codegen path. The convention `target.<ext>` is documented in the
# visual self-heal system prompt; any of these extensions counts.
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
#
# Note on \bUI\b: the bare token "UI" is a 2-char substring that would
# false-positive on common English words (build/guide/suit/circuit/...)
# under IGNORECASE. The word boundary anchors keep it from matching
# inside larger words.
_VISUAL_INTENT_RE = re.compile(
    r"(复刻|复现|重现|仿做|reproduce|recreate|replicate|clone|render|"
    r"build.*(from|based on).*(image|screenshot|mockup|design)|"
    r"front.?end|web.?page|\bUI\b|界面|页面|前端)",
    re.IGNORECASE,
)


def discover_target_images(user_request: str, *, workspace: Path) -> list[Path]:
    """Return paths of target images present in *workspace* for vision codegen.

    A visual reproduction task is identified when BOTH:
      1. The user_request mentions visual / front-end intent (复刻 / 复现 /
         reproduce / 前端 / UI / …), AND
      2. At least one well-known target file (target.png/.jpg/.jpeg/.webp)
         exists in *workspace*.

    Returning [] disables the vision codegen route — the text-only path
    handles non-visual tasks unchanged. The double-gate avoids routing
    data-analysis tasks (that happen to also have a target.png alongside
    them) into a vision model that would pointlessly burn tokens.
    """
    if not _VISUAL_INTENT_RE.search(user_request or ""):
        return []
    found: list[Path] = []
    for name in _TARGET_IMAGE_NAMES:
        candidate = workspace / name
        if candidate.is_file():
            found.append(candidate)
    return found


def resolve_vision_routing(user_request: str, *, workspace: Path) -> VisionRouting:
    """Produce the typed routing decision consumed by code_generation_node.

    Pure: same (request, workspace) → same VisionRouting. Filesystem access
    is the only side effect, contained here so downstream nodes can stay
    pure on RuntimeState.
    """
    images = discover_target_images(user_request, workspace=workspace)
    return VisionRouting(
        use_vision=bool(images),
        target_images=[str(p) for p in images],
    )
