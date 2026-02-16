"""
Image Generation & Editing Tools - Powered by Gemini Nano Banana Pro.
*** ARCHIVED: Replaced by fal_generation.py (fal.ai tools). ***

Uses Gemini 3 Pro Image Preview (Nano Banana Pro) via AI Studio API key for high-quality
image generation and editing. All model-level safety filters are OFF per user request.

Usage:
    generate_image(prompt="a cozy coffee shop at golden hour")
    edit_image(image_path="05_App_Data/photo.jpg", prompt="change the sky to sunset")
"""

import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict

from claude_agent_sdk import tool

# Registry import kept but decorators removed — these tools are archived.
# from ..registry import register_tool

logger = logging.getLogger("mcp_tools.image")

# =============================================================================
# Configuration
# =============================================================================

MODEL_ID = "gemini-3-pro-image-preview"
OUTPUT_DIR = os.path.expanduser("~/second_brain/05_App_Data/generated_images")

VALID_ASPECT_RATIOS = [
    "1:1", "2:3", "3:2", "3:4", "4:3",
    "4:5", "5:4", "9:16", "16:9", "21:9",
]

VALID_RESOLUTIONS = ["1K", "2K", "4K"]


def _get_client():
    """Lazy-init Gemini client via AI Studio API key."""
    from google import genai
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable not set")
    return genai.Client(api_key=api_key)


def _get_safety_settings():
    """All model-level safety filters OFF."""
    from google.genai import types
    return [
        types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF"),
        types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
        types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
        types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
        types.SafetySetting(category="HARM_CATEGORY_CIVIC_INTEGRITY", threshold="OFF"),
    ]


def _ensure_output_dir():
    """Create output directory if it doesn't exist."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def _generate_filename(prefix: str = "img") -> str:
    """Generate a unique filename: {timestamp}_{short_hash}.png"""
    ts = time.strftime("%Y%m%d_%H%M%S")
    short_hash = hashlib.md5(f"{time.time()}".encode()).hexdigest()[:8]
    return f"{ts}_{short_hash}.png"


def _save_image(image_data: bytes, filename: str) -> str:
    """Save image bytes to file. Returns the full path."""
    _ensure_output_dir()
    filepath = os.path.join(OUTPUT_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(image_data)
    logger.info(f"Saved image to {filepath}")
    return filepath


def _extract_image_bytes(response) -> bytes:
    """Extract image bytes from Gemini response.

    Handles content filter blocks gracefully.
    """
    if not response.candidates:
        feedback = getattr(response, 'prompt_feedback', None)
        raise ValueError(
            f"No candidates returned — content was likely blocked by Google's filter. "
            f"Feedback: {feedback}"
        )

    candidate = response.candidates[0]
    finish_reason = getattr(candidate, 'finish_reason', None)

    # Check for content filter blocks
    if finish_reason and hasattr(finish_reason, 'name'):
        reason_name = finish_reason.name
        if reason_name in ('IMAGE_SAFETY', 'IMAGE_PROHIBITED_CONTENT', 'SAFETY'):
            raise ValueError(
                f"Image blocked by Google's content filter (reason: {reason_name}). "
                "Try rephrasing — 'athletic wear', 'fitness photography', 'sportswear' tend to pass. "
                "'bikini', 'swimsuit', 'swimwear' are typically blocked."
            )

    if not hasattr(candidate, 'content') or not candidate.content or not candidate.content.parts:
        raise ValueError(f"Candidate has no content. Finish reason: {finish_reason}")

    for part in candidate.content.parts:
        if hasattr(part, "inline_data") and part.inline_data and part.inline_data.data:
            return part.inline_data.data
    raise ValueError("No image data found in response parts")


def _get_mime_type(path: str) -> str:
    """Determine MIME type from file extension."""
    ext = Path(path).suffix.lower()
    mime_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }
    return mime_map.get(ext, "image/png")


def _resolve_path(path: str) -> str:
    """Resolve a path relative to project root or absolute."""
    if os.path.isabs(path):
        return path
    return os.path.join(os.path.expanduser("~/second_brain"), path)


# =============================================================================
# MCP Tools
# =============================================================================

# @register_tool("image")  # ARCHIVED — replaced by fal.ai tools
@tool(
    name="generate_image",
    description="""Generate images using Nano Banana — Google's top-rated image model that dominated LMArena with 5M+ votes.

Produces stunning photorealistic images, illustrations, stickers, and more.
Describe scenes narratively for best results. Use camera language for photos
("85mm lens, shallow DOF"), art style descriptors for illustrations ("watercolor,
loose brushstrokes"), and specific text in quotes for typography.

Images are saved to 05_App_Data/generated_images/ and the file path(s) are returned.""",
    input_schema={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Descriptive scene prompt. Be narrative, not keyword-listy.",
            },
            "negative_prompt": {
                "type": "string",
                "description": "Things to avoid — appended as 'Avoid: {text}' to the main prompt since model uses semantic negatives.",
            },
            "aspect_ratio": {
                "type": "string",
                "description": "Aspect ratio for the image.",
                "enum": VALID_ASPECT_RATIOS,
                "default": "1:1",
            },
            "resolution": {
                "type": "string",
                "description": "Output resolution.",
                "enum": VALID_RESOLUTIONS,
                "default": "2K",
            },
            "count": {
                "type": "integer",
                "description": "Number of images to generate (1-4).",
                "default": 1,
                "minimum": 1,
                "maximum": 4,
            },
            "seed": {
                "type": "integer",
                "description": "Seed for best-effort reproducibility (1 to 2147483647).",
            },
            "source_images": {
                "type": "array",
                "description": "Optional source image paths for character/style reference. Images are included as context for the generation.",
                "items": {"type": "string"},
            },
            "output_path": {
                "type": "string",
                "description": "Optional custom output path (relative to project root or absolute). If not set, auto-generates in 05_App_Data/generated_images/.",
            },
        },
        "required": ["prompt"],
    },
)
async def generate_image(args_or_prompt=None, *, prompt=None, source_images=None, output_path=None, **kwargs) -> Dict[str, Any]:
    """Generate image(s) from a text prompt."""
    from google.genai import types

    # Support both dict-style (MCP) and keyword-style (direct) calls
    if isinstance(args_or_prompt, dict):
        args = args_or_prompt
    elif isinstance(args_or_prompt, str):
        args = {"prompt": args_or_prompt, "source_images": source_images or [], "output_path": output_path, **kwargs}
    elif prompt is not None:
        args = {"prompt": prompt, "source_images": source_images or [], "output_path": output_path, **kwargs}
    else:
        args = kwargs

    prompt = args.get("prompt", "").strip()
    if not prompt:
        return {
            "content": [{"type": "text", "text": "Error: prompt cannot be empty"}],
            "is_error": True,
        }

    negative_prompt = args.get("negative_prompt", "")
    aspect_ratio = args.get("aspect_ratio", "1:1")
    resolution = args.get("resolution", "2K")
    count = min(max(args.get("count", 1), 1), 4)
    seed = args.get("seed")

    if aspect_ratio not in VALID_ASPECT_RATIOS:
        return {
            "content": [{"type": "text", "text": f"Invalid aspect_ratio: {aspect_ratio}. Valid: {VALID_ASPECT_RATIOS}"}],
            "is_error": True,
        }

    if resolution not in VALID_RESOLUTIONS:
        return {
            "content": [{"type": "text", "text": f"Invalid resolution: {resolution}. Valid: {VALID_RESOLUTIONS}"}],
            "is_error": True,
        }

    source_images = args.get("source_images", []) or []
    output_path = args.get("output_path")

    final_prompt = prompt
    if negative_prompt:
        final_prompt += f"\n\nAvoid: {negative_prompt}"

    config_kwargs = {
        "response_modalities": ["IMAGE"],
        "safety_settings": _get_safety_settings(),
        "image_config": types.ImageConfig(
            aspect_ratio=aspect_ratio,
            image_size=resolution,
        ),
    }
    if seed is not None:
        config_kwargs["seed"] = seed

    config = types.GenerateContentConfig(**config_kwargs)

    # Build contents: source images (if any) + text prompt
    from google.genai.types import Part
    contents = []
    for img_path in source_images:
        resolved = _resolve_path(img_path.strip())
        if not os.path.isfile(resolved):
            return {
                "content": [{"type": "text", "text": f"Source image not found: {resolved}"}],
                "is_error": True,
            }
        with open(resolved, "rb") as f:
            img_bytes = f.read()
        mime = _get_mime_type(resolved)
        contents.append(Part.from_bytes(data=img_bytes, mime_type=mime))
    contents.append(final_prompt)

    # If no source images, just use the prompt string directly
    if not source_images:
        contents = final_prompt

    try:
        client = _get_client()
        saved_paths = []

        for i in range(count):
            logger.info(f"Generating image {i+1}/{count}...")
            response = client.models.generate_content(
                model=MODEL_ID,
                contents=contents,
                config=config,
            )

            image_bytes = _extract_image_bytes(response)
            if output_path and count == 1:
                # Use custom output path
                resolved_out = _resolve_path(output_path)
                os.makedirs(os.path.dirname(resolved_out), exist_ok=True)
                with open(resolved_out, "wb") as f:
                    f.write(image_bytes)
                filepath = resolved_out
            else:
                filename = _generate_filename(prefix=f"gen_{i+1}")
                filepath = _save_image(image_bytes, filename)
            saved_paths.append(filepath)

        result_text = f"Generated {len(saved_paths)} image(s):\n"
        for p in saved_paths:
            result_text += f"  - {p}\n"

        return {"content": [{"type": "text", "text": result_text}]}

    except Exception as e:
        logger.error(f"Image generation failed: {e}")
        return {
            "content": [{"type": "text", "text": f"Image generation failed: {e}"}],
            "is_error": True,
        }


# @register_tool("image")  # ARCHIVED — replaced by fal.ai tools
@tool(
    name="edit_image",
    description="""Edit existing images using Nano Banana's multi-modal understanding.

Supports background changes, object addition/removal, style transfer, colorization,
pose changes, text overlay, and more. Provide a source image and natural language
instructions. For style transfer, provide a style reference image.

Edited image is saved to 05_App_Data/generated_images/ and the file path is returned.""",
    input_schema={
        "type": "object",
        "properties": {
            "image_path": {
                "type": "string",
                "description": "Path to source image (relative to project root or absolute).",
            },
            "prompt": {
                "type": "string",
                "description": "Natural language editing instructions. Be specific about what to keep vs change.",
            },
            "reference_image_path": {
                "type": "string",
                "description": "Optional path to a style/reference image for transfers.",
            },
            "aspect_ratio": {
                "type": "string",
                "description": "Override output aspect ratio.",
                "enum": VALID_ASPECT_RATIOS,
            },
            "resolution": {
                "type": "string",
                "description": "Output resolution.",
                "enum": VALID_RESOLUTIONS,
                "default": "2K",
            },
        },
        "required": ["image_path", "prompt"],
    },
)
async def edit_image(args: Dict[str, Any]) -> Dict[str, Any]:
    """Edit an existing image using natural language instructions."""
    from google.genai import types
    from google.genai.types import Part

    image_path = args.get("image_path", "").strip()
    prompt = args.get("prompt", "").strip()
    reference_image_path = args.get("reference_image_path", "")
    aspect_ratio = args.get("aspect_ratio")
    resolution = args.get("resolution", "2K")

    if not image_path:
        return {
            "content": [{"type": "text", "text": "Error: image_path is required"}],
            "is_error": True,
        }
    if not prompt:
        return {
            "content": [{"type": "text", "text": "Error: prompt is required"}],
            "is_error": True,
        }

    resolved_path = _resolve_path(image_path)
    if not os.path.isfile(resolved_path):
        return {
            "content": [{"type": "text", "text": f"Source image not found: {resolved_path}"}],
            "is_error": True,
        }

    with open(resolved_path, "rb") as f:
        image_bytes = f.read()

    source_mime = _get_mime_type(resolved_path)
    image_part = Part.from_bytes(data=image_bytes, mime_type=source_mime)

    contents = [image_part]

    if reference_image_path:
        ref_resolved = _resolve_path(reference_image_path.strip())
        if not os.path.isfile(ref_resolved):
            return {
                "content": [{"type": "text", "text": f"Reference image not found: {ref_resolved}"}],
                "is_error": True,
            }
        with open(ref_resolved, "rb") as f:
            ref_bytes = f.read()
        ref_mime = _get_mime_type(ref_resolved)
        contents.append(Part.from_bytes(data=ref_bytes, mime_type=ref_mime))

    contents.append(prompt)

    image_config_kwargs = {}
    if aspect_ratio:
        if aspect_ratio not in VALID_ASPECT_RATIOS:
            return {
                "content": [{"type": "text", "text": f"Invalid aspect_ratio: {aspect_ratio}. Valid: {VALID_ASPECT_RATIOS}"}],
                "is_error": True,
            }
        image_config_kwargs["aspect_ratio"] = aspect_ratio
    if resolution:
        image_config_kwargs["image_size"] = resolution

    config = types.GenerateContentConfig(
        response_modalities=["IMAGE"],
        safety_settings=_get_safety_settings(),
        image_config=types.ImageConfig(**image_config_kwargs) if image_config_kwargs else None,
    )

    try:
        client = _get_client()
        logger.info(f"Editing image: {resolved_path}")

        response = client.models.generate_content(
            model=MODEL_ID,
            contents=contents,
            config=config,
        )

        result_bytes = _extract_image_bytes(response)
        filename = _generate_filename(prefix="edit")
        filepath = _save_image(result_bytes, filename)

        return {
            "content": [{"type": "text", "text": f"Edited image saved to:\n  - {filepath}"}]
        }

    except Exception as e:
        logger.error(f"Image editing failed: {e}")
        return {
            "content": [{"type": "text", "text": f"Image editing failed: {e}"}],
            "is_error": True,
        }


__all__ = ["generate_image", "edit_image"]
