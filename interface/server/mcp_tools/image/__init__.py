"""
Image generation and editing tools package.

Tools:
  - fal.ai tools: fal_text_to_image, fal_image_to_image, fal_multi_ref_image, fal_list_models
  - Legacy Gemini tools: generate_image, edit_image (archived, not registered)
"""

from .fal_generation import (
    fal_text_to_image,
    fal_image_to_image,
    fal_multi_ref_image,
    fal_list_models,
)

# Legacy Gemini tools — kept for reference but no longer registered as MCP tools.
# To re-enable, uncomment the import and re-add @register_tool decorators in generation.py.
# from .generation import generate_image, edit_image

__all__ = [
    "fal_text_to_image",
    "fal_image_to_image",
    "fal_multi_ref_image",
    "fal_list_models",
]
