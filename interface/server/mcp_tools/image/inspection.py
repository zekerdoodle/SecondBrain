"""Bounded local-image previews for model inspection.

The original file is never modified. This tool decodes it in memory and emits
only re-encoded previews that satisfy hard per-item, aggregate, count, and
dimension limits before base64 wrapping.
"""

from __future__ import annotations

import base64
import io
import json
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

from claude_agent_sdk import tool
from PIL import Image, ImageOps, UnidentifiedImageError

from tool_output_artifacts import REPO_ROOT

from ..registry import register_tool

MAX_IMAGE_ITEMS = 2
MAX_PREVIEW_ENCODED_BYTES = 1 * 1024 * 1024
MAX_PREVIEW_AGGREGATE_BYTES = 2 * 1024 * 1024
MAX_PREVIEW_LONG_EDGE = 1600
MIN_PREVIEW_LONG_EDGE = 64
MAX_LOCAL_PATH_CHARS = 4096
PREVIEW_MIME_TYPE = "image/webp"
PREVIEW_QUALITY_STEPS = (82, 74, 66, 58, 50, 42, 34)


class ImageInspectionError(ValueError):
    """A bounded, caller-safe image inspection failure."""


@dataclass(frozen=True)
class Preview:
    data: bytes
    dimensions: Tuple[int, int]
    quality: int
    resized: bool


def _bounded_error_text(path: Any, exc: BaseException) -> str:
    display_path = str(path).replace("\n", "\\n").replace("\r", "\\r")[:1024]
    detail = str(exc).replace("\n", " ").replace("\r", " ")[:512]
    if not detail:
        detail = type(exc).__name__
    return f"Image inspection failed for {display_path}: {detail}"


def _resolve_local_path(value: Any) -> Path:
    raw = str(value or "")
    if not raw.strip():
        raise ImageInspectionError("path must not be empty")
    if len(raw) > MAX_LOCAL_PATH_CHARS:
        raise ImageInspectionError(f"path exceeds {MAX_LOCAL_PATH_CHARS} characters")
    if raw.startswith(("http://", "https://", "data:")):
        raise ImageInspectionError("only local file paths are supported")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ImageInspectionError("file does not exist or cannot be resolved") from exc
    if not resolved.is_file():
        raise ImageInspectionError("path is not a regular file")
    return resolved


def _loaded_oriented_image(path: Path) -> tuple[Image.Image, Tuple[int, int], str]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as source:
                original_dimensions = source.size
                original_format = str(source.format or "unknown").lower()
                source.seek(0)
                oriented = ImageOps.exif_transpose(source)
                oriented.load()
                loaded = oriented.copy()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageInspectionError("file is unreadable, corrupt, or unsafe to decode") from exc

    has_alpha = "A" in loaded.getbands() or (
        loaded.mode == "P" and "transparency" in loaded.info
    )
    return loaded.convert("RGBA" if has_alpha else "RGB"), original_dimensions, original_format


def _dimensions_for_long_edge(size: Tuple[int, int], long_edge: int) -> Tuple[int, int]:
    width, height = size
    longest = max(width, height)
    if longest <= long_edge:
        return width, height
    scale = long_edge / longest
    return max(1, round(width * scale)), max(1, round(height * scale))


def _encode_preview(
    image: Image.Image,
    *,
    max_bytes: int | None = None,
    max_long_edge: int | None = None,
) -> Preview:
    byte_limit = MAX_PREVIEW_ENCODED_BYTES if max_bytes is None else max_bytes
    long_edge_limit = MAX_PREVIEW_LONG_EDGE if max_long_edge is None else max_long_edge
    if byte_limit <= 0 or long_edge_limit <= 0:
        raise ImageInspectionError("preview limits are invalid")

    source_dimensions = image.size
    candidate_long_edge = min(max(source_dimensions), long_edge_limit)
    while True:
        dimensions = _dimensions_for_long_edge(source_dimensions, candidate_long_edge)
        if dimensions == source_dimensions:
            candidate = image
        else:
            candidate = image.resize(dimensions, Image.Resampling.LANCZOS)

        for quality in PREVIEW_QUALITY_STEPS:
            buffer = io.BytesIO()
            try:
                candidate.save(buffer, format="WEBP", quality=quality, method=4)
            except (OSError, ValueError) as exc:
                raise ImageInspectionError("preview encoder failed") from exc
            data = buffer.getvalue()
            if len(data) <= byte_limit:
                return Preview(
                    data=data,
                    dimensions=dimensions,
                    quality=quality,
                    resized=dimensions != source_dimensions,
                )

        if candidate_long_edge <= MIN_PREVIEW_LONG_EDGE:
            break
        next_long_edge = max(MIN_PREVIEW_LONG_EDGE, int(candidate_long_edge * 0.8))
        if next_long_edge == candidate_long_edge:
            break
        candidate_long_edge = next_long_edge

    raise ImageInspectionError(
        f"image could not fit within the {byte_limit}-byte preview limit"
    )


def _build_preview(path_value: Any) -> tuple[Dict[str, Any], Dict[str, Any], int]:
    path = _resolve_local_path(path_value)
    original_bytes = path.stat().st_size
    image, original_dimensions, original_format = _loaded_oriented_image(path)
    visual_dimensions = image.size
    preview = _encode_preview(image)
    preview_bytes = len(preview.data)
    reduced = preview.resized or preview_bytes < original_bytes
    metadata = {
        "original_path": str(path),
        "original_dimensions": {
            "width": original_dimensions[0],
            "height": original_dimensions[1],
        },
        "original_bytes": original_bytes,
        "original_format": original_format,
        "preview_dimensions": {
            "width": preview.dimensions[0],
            "height": preview.dimensions[1],
        },
        "preview_bytes": preview_bytes,
        "preview_mime_type": PREVIEW_MIME_TYPE,
        "preview_quality": preview.quality,
        "reduction_state": "reduced" if reduced else "re-encoded",
        "orientation_changed_dimensions": original_dimensions != visual_dimensions,
        "original_untouched": True,
    }
    image_item = {
        "type": "image",
        "mimeType": PREVIEW_MIME_TYPE,
        "data": base64.b64encode(preview.data).decode("ascii"),
    }
    return metadata, image_item, preview_bytes


@register_tool("image")
@tool(
    name="inspect_images",
    description="""Inspect one or two local image files through bounded visual previews.

Use this instead of requesting original-detail local images. The originals are
read-only and remain untouched. Each returned preview is at most 1 MiB before
base64, the aggregate is at most 2 MiB, and the long edge is at most 1,600 px.
Never pass data URLs; provide local filesystem paths only.""",
    input_schema={
        "type": "object",
        "properties": {
            "paths": {
                "type": "array",
                "description": "One or two local image paths to inspect.",
                "items": {
                    "type": "string",
                    "maxLength": MAX_LOCAL_PATH_CHARS,
                },
                "minItems": 1,
                "maxItems": MAX_IMAGE_ITEMS,
            },
        },
        "required": ["paths"],
        "additionalProperties": False,
    },
)
async def inspect_images(args: Dict[str, Any]) -> Dict[str, Any]:
    paths = args.get("paths")
    if not isinstance(paths, list) or not paths:
        return {
            "content": [{"type": "text", "text": "Image inspection failed: paths must contain one or two local files."}],
            "is_error": True,
        }
    if len(paths) > MAX_IMAGE_ITEMS:
        return {
            "content": [{"type": "text", "text": f"Image inspection failed: at most {MAX_IMAGE_ITEMS} images are allowed per call."}],
            "is_error": True,
        }

    previews: List[tuple[Dict[str, Any], Dict[str, Any], int]] = []
    try:
        for path in paths:
            previews.append(_build_preview(path))
    except (ImageInspectionError, OSError, ValueError) as exc:
        return {
            "content": [{"type": "text", "text": _bounded_error_text(path, exc)}],
            "is_error": True,
        }

    aggregate_bytes = sum(item[2] for item in previews)
    if aggregate_bytes > MAX_PREVIEW_AGGREGATE_BYTES:
        return {
            "content": [{
                "type": "text",
                "text": (
                    "Image inspection failed: previews exceeded the "
                    f"{MAX_PREVIEW_AGGREGATE_BYTES}-byte aggregate limit."
                ),
            }],
            "is_error": True,
        }

    content: List[Dict[str, Any]] = []
    for index, (metadata, image_item, _preview_bytes) in enumerate(previews, start=1):
        content.append({
            "type": "text",
            "text": f"Image {index} metadata: {json.dumps(metadata, ensure_ascii=False, sort_keys=True)}",
        })
        content.append(image_item)
    return {"content": content, "is_error": False}


__all__ = [
    "MAX_IMAGE_ITEMS",
    "MAX_PREVIEW_AGGREGATE_BYTES",
    "MAX_PREVIEW_ENCODED_BYTES",
    "MAX_PREVIEW_LONG_EDGE",
    "inspect_images",
]
