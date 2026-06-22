"""
Image generation and editing tools package.

Tools:
  - fal.ai tools: fal_text_to_image, fal_image_to_image, fal_multi_ref_image, fal_reference_to_video, fal_list_models
  - Atlas Cloud tools: atlas_multi_ref_image, atlas_reference_to_video
  - Legacy Gemini tools: generate_image, edit_image (archived, not registered)
"""

from .fal_generation import (
    fal_text_to_image,
    fal_image_to_image,
    fal_multi_ref_image,
    fal_reference_to_video,
    fal_list_models,
)
from .atlas_generation import (
    atlas_multi_ref_image,
    atlas_reference_to_video,
)

# Legacy Gemini tools — kept for reference but no longer registered as MCP tools.
# To re-enable, uncomment the import and re-add @register_tool decorators in generation.py.
# from .generation import generate_image, edit_image

__all__ = [
    "fal_text_to_image",
    "fal_image_to_image",
    "fal_multi_ref_image",
    "fal_reference_to_video",
    "fal_list_models",
    "atlas_multi_ref_image",
    "atlas_reference_to_video",
]
