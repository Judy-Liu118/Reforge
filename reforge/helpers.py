"""Top-level convenience module for sandbox-side Python.

Re-exports the helper functions that visual / vision tasks routinely need
so generated code can use a short, memorable import path:

    from reforge.helpers import describe_image, screenshot, compare_images

Each helper raises on failure so the runtime's reflection node sees the
exception and the self-heal loop kicks in.
"""

from reforge.runtime.skills.builtin.image_compare import compare_images
from reforge.runtime.skills.builtin.vision import describe_image
from reforge.runtime.skills.builtin.web_screenshot import screenshot

__all__ = ["compare_images", "describe_image", "screenshot"]
