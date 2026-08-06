"""
Atlas Cloud image and video generation tools.

Provider-separate companion to the fal.ai tools. Uses Atlas Cloud's async
generation endpoints, polls prediction status, and downloads outputs locally.
"""

import asyncio
import json
import logging
import mimetypes
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from claude_agent_sdk import tool

from ..registry import register_tool

logger = logging.getLogger("mcp_tools.image.atlas")

# =============================================================================
# Configuration
# =============================================================================

OUTPUT_DIR = os.path.expanduser("~/second_brain/05_App_Data/generated_images")

ATLAS_BASE_URL = "https://api.atlascloud.ai/api/v1"
ATLAS_GENERATE_IMAGE_URL = f"{ATLAS_BASE_URL}/model/generateImage"
ATLAS_GENERATE_VIDEO_URL = f"{ATLAS_BASE_URL}/model/generateVideo"
ATLAS_UPLOAD_MEDIA_URL = f"{ATLAS_BASE_URL}/model/uploadMedia"
ATLAS_PREDICTION_URL = f"{ATLAS_BASE_URL}/model/prediction"

DEFAULT_IMAGE_EDIT_MODEL = "bytedance/seedream-v4.5/edit"
DEFAULT_VIDEO_MODEL = "vidu/q3/reference-to-video"
DEFAULT_IMAGE_PARAM = "images"
DEFAULT_VIDEO_REFERENCE_PARAM = "images"
DEFAULT_VIDEO_IMAGE_PARAM = "image"

HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=180.0, pool=10.0)
IMAGE_POLL_INTERVAL = 2.0
VIDEO_POLL_INTERVAL = 3.0
DEFAULT_IMAGE_MAX_WAIT = 180.0
DEFAULT_VIDEO_MAX_WAIT = 300.0

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".m4v"}
_COMPLETED_STATUSES = {"completed", "succeeded"}
_FAILED_STATUSES = {"failed", "error", "cancelled", "canceled"}
_ASYNC_OVERRIDE_KEYS = {"enable_sync_mode", "enable_base64_output"}


# =============================================================================
# Shared helpers
# =============================================================================


def _get_atlas_key() -> str:
    """Get ATLASCLOUD_API_KEY from the environment."""
    key = os.environ.get("ATLASCLOUD_API_KEY")
    if not key:
        raise ValueError("ATLASCLOUD_API_KEY environment variable not set")
    return key


def _auth_headers(content_type: Optional[str] = "application/json") -> Dict[str, str]:
    headers = {"Authorization": f"Bearer {_get_atlas_key()}"}
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _redact_text(value: Any) -> str:
    """Redact the current Atlas API key and common bearer-token shapes."""
    text = str(value)
    key = os.environ.get("ATLASCLOUD_API_KEY")
    if key:
        text = text.replace(key, "[REDACTED]")
    text = re.sub(
        r"Bearer\s+[A-Za-z0-9._~+/=-]+",
        "Bearer [REDACTED]",
        text,
        flags=re.IGNORECASE,
    )
    return text


def _safe_repr(value: Any) -> str:
    return _redact_text(repr(value))


def _looks_like_url(path: str) -> bool:
    return path.startswith(("http://", "https://"))


def _resolve_path(path: str) -> str:
    """Resolve a path relative to project root, with containment check."""
    project_root = os.path.realpath(os.path.expanduser("~/second_brain"))
    if os.path.isabs(path):
        resolved = os.path.realpath(path)
    else:
        resolved = os.path.realpath(os.path.join(project_root, path))
    if not resolved.startswith(project_root + os.sep) and resolved != project_root:
        raise ValueError(f"Path escapes project root: {path}")
    return resolved


def _get_mime_type(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    return mime or "application/octet-stream"


def _response_json(resp: Any) -> Any:
    try:
        return resp.json()
    except Exception:
        return None


def _response_excerpt(resp: Any, limit: int = 500) -> str:
    data = _response_json(resp)
    text = ""
    if isinstance(data, dict):
        detail = data.get("detail") or data.get("error") or data.get("message")
        if detail is None and isinstance(data.get("data"), dict):
            detail = data["data"].get("error") or data["data"].get("message")
        if detail is not None:
            text = json.dumps(detail, ensure_ascii=True) if isinstance(detail, (dict, list)) else str(detail)
        else:
            text = json.dumps(data, ensure_ascii=True)
    elif data not in (None, ""):
        text = json.dumps(data, ensure_ascii=True) if isinstance(data, (dict, list)) else str(data)

    if not text:
        text = getattr(resp, "text", "") or ""
    text = " ".join(_redact_text(text).split())
    if not text:
        return "<empty response body>"
    if len(text) > limit:
        return f"{text[:limit]}..."
    return text


def _raise_atlas_error(stage: str, resp: Any) -> None:
    status = getattr(resp, "status_code", "unknown")
    excerpt = _response_excerpt(resp)
    raise RuntimeError(f"Atlas Cloud {stage} failed ({status}): {excerpt}")


def _fmt_exc(exc: Exception) -> str:
    message = _redact_text(str(exc))
    if message:
        return f"{type(exc).__name__}: {message}"
    return type(exc).__name__


def _payload_data(response_json: Any) -> Dict[str, Any]:
    if isinstance(response_json, dict):
        data = response_json.get("data")
        if isinstance(data, dict):
            return data
        return response_json
    raise RuntimeError(f"Atlas response was not a JSON object: {response_json!r}")


def _prediction_id(data: Dict[str, Any]) -> Optional[str]:
    prediction_id = data.get("id") or data.get("prediction_id")
    return prediction_id if isinstance(prediction_id, str) and prediction_id else None


def _status(data: Dict[str, Any]) -> str:
    raw = data.get("status", "")
    return str(raw).strip().lower()


def _output_urls(data: Dict[str, Any]) -> List[str]:
    outputs = data.get("outputs")
    urls: List[str] = []
    if isinstance(outputs, list):
        for item in outputs:
            if isinstance(item, str) and item:
                urls.append(item)
            elif isinstance(item, dict):
                url = item.get("url") or item.get("download_url")
                if isinstance(url, str) and url:
                    urls.append(url)

    for key in ("output", "url", "download_url", "file_url"):
        value = data.get(key)
        if isinstance(value, str) and value:
            urls.append(value)

    image = data.get("image")
    if isinstance(image, dict):
        url = image.get("url") or image.get("download_url")
        if isinstance(url, str) and url:
            urls.append(url)
    elif isinstance(image, str) and image:
        urls.append(image)

    images = data.get("images")
    if isinstance(images, list):
        for item in images:
            if isinstance(item, str) and item:
                urls.append(item)
            elif isinstance(item, dict):
                url = item.get("url") or item.get("download_url")
                if isinstance(url, str) and url:
                    urls.append(url)

    videos = data.get("videos")
    if isinstance(videos, list):
        for item in videos:
            if isinstance(item, str) and item:
                urls.append(item)
            elif isinstance(item, dict):
                url = item.get("url") or item.get("download_url")
                if isinstance(url, str) and url:
                    urls.append(url)

    video = data.get("video")
    if isinstance(video, dict):
        url = video.get("url") or video.get("download_url")
        if isinstance(url, str) and url:
            urls.append(url)
    elif isinstance(video, str) and video:
        urls.append(video)

    deduped: List[str] = []
    for url in urls:
        if url not in deduped:
            deduped.append(url)
    return deduped


def _strip_async_overrides(extra_params: Dict[str, Any]) -> Dict[str, Any]:
    clean = dict(extra_params)
    ignored = [key for key in _ASYNC_OVERRIDE_KEYS if key in clean]
    for key in ignored:
        clean.pop(key, None)
    if ignored:
        logger.info(
            "Ignoring Atlas async override param(s): %s",
            ", ".join(sorted(ignored)),
        )
    return clean


def _extension_from_url(
    url: str,
    fallback: str,
    allowed_extensions: Optional[set[str]] = None,
) -> str:
    parsed = urlparse(url)
    _, ext = os.path.splitext(parsed.path)
    ext = ext.lower()
    if ext and (allowed_extensions is None or ext in allowed_extensions):
        return ext
    return fallback


def _generate_filename(prefix: str, extension: str) -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    random_token = uuid.uuid4().hex[:8]
    return f"{ts}_{random_token}_{prefix}{extension}"


def _resolve_output_path(
    output_path: Optional[str],
    output_url: str,
    prefix: str,
    fallback_ext: str,
    allowed_extensions: set[str],
) -> str:
    extension = _extension_from_url(output_url, fallback_ext, allowed_extensions)
    if output_path:
        path = output_path
        if not os.path.splitext(path)[1]:
            path = f"{path}{extension}"
        return _resolve_path(path)
    return os.path.join(OUTPUT_DIR, _generate_filename(prefix, extension))


def _file_size_or_error(path: str) -> int:
    if not os.path.isfile(path):
        raise RuntimeError(f"Download did not create output file: {path}")
    size = os.path.getsize(path)
    if size <= 0:
        raise RuntimeError(f"Download produced an empty file: {path}")
    return size


async def _download_file(url: str, output_path: str) -> str:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as output_file:
            output_file.write(resp.content)
    return output_path


async def _save_output_urls(
    urls: List[str],
    output_path: Optional[str],
    prefix: str,
    fallback_ext: str,
    allowed_extensions: set[str],
) -> List[Tuple[str, int]]:
    if not urls:
        raise ValueError("No output URLs in Atlas Cloud prediction result")
    if output_path and len(urls) > 1:
        logger.warning(
            "Atlas output_path ignored for %s outputs; generated filenames will be used",
            len(urls),
        )

    saved: List[Tuple[str, int]] = []
    for index, url in enumerate(urls):
        custom_path = output_path if output_path and len(urls) == 1 else None
        path_prefix = prefix if len(urls) == 1 else f"{prefix}_{index + 1}"
        dest = _resolve_output_path(
            custom_path,
            url,
            path_prefix,
            fallback_ext,
            allowed_extensions,
        )
        await _download_file(url, dest)
        saved.append((dest, _file_size_or_error(dest)))
        logger.info("Saved Atlas Cloud output to %s", dest)
    return saved


async def _upload_media(file_path: str) -> str:
    resolved = _resolve_path(file_path)
    if not os.path.isfile(resolved):
        raise FileNotFoundError(f"File not found: {resolved}")

    filename = os.path.basename(resolved)
    mime_type = _get_mime_type(resolved)
    with open(resolved, "rb") as input_file:
        file_bytes = input_file.read()

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.post(
            ATLAS_UPLOAD_MEDIA_URL,
            headers=_auth_headers(content_type=None),
            files={"file": (filename, file_bytes, mime_type)},
        )
        if not 200 <= resp.status_code < 300:
            _raise_atlas_error("media upload", resp)
        data = _payload_data(resp.json())

    for key in ("download_url", "url", "file_url"):
        value = data.get(key)
        if isinstance(value, str) and value:
            logger.info("Uploaded media to Atlas Cloud: %s", value)
            return value
    raise RuntimeError(f"Atlas upload response missing URL. Keys: {list(data.keys())}")


async def _prepare_media_urls(paths: List[str], media_label: str) -> Tuple[List[str], List[str]]:
    requested_paths = [path.strip() for path in paths if isinstance(path, str) and path.strip()]
    if not requested_paths:
        raise ValueError(f"No valid {media_label} paths provided")

    urls: List[str] = []
    for index, path in enumerate(requested_paths):
        if _looks_like_url(path):
            urls.append(path)
            continue
        resolved = _resolve_path(path)
        if not os.path.isfile(resolved):
            raise FileNotFoundError(f"{media_label.capitalize()} not found: {resolved}")
        logger.info(
            "Uploading Atlas %s %s/%s: %s",
            media_label,
            index + 1,
            len(requested_paths),
            path,
        )
        urls.append(await _upload_media(path))
    return urls, requested_paths


async def _submit_prediction(endpoint_url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.post(endpoint_url, headers=_auth_headers(), json=payload)
        if not 200 <= resp.status_code < 300:
            _raise_atlas_error("generation submit", resp)
        return _payload_data(resp.json())


async def _poll_prediction(
    prediction_id: str,
    max_wait_seconds: float,
    poll_interval: float,
) -> Dict[str, Any]:
    if max_wait_seconds <= 0:
        raise ValueError("max_wait_seconds must be greater than 0")

    elapsed = 0.0
    poll_url = f"{ATLAS_PREDICTION_URL}/{prediction_id}"
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        while elapsed <= max_wait_seconds:
            resp = await client.get(
                poll_url,
                headers=_auth_headers(content_type=None),
            )
            if not 200 <= resp.status_code < 300:
                _raise_atlas_error("prediction poll", resp)
            data = _payload_data(resp.json())
            status = _status(data)
            if status in _COMPLETED_STATUSES:
                return data
            if status in _FAILED_STATUSES:
                error = data.get("error") or data.get("message") or "Generation failed"
                raise RuntimeError(f"Atlas Cloud prediction {prediction_id} failed: {_redact_text(error)}")

            logger.info(
                "Atlas Cloud prediction %s status: %s",
                prediction_id,
                status or "unknown",
            )
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

    raise RuntimeError(
        f"Atlas Cloud prediction {prediction_id} timed out after {max_wait_seconds:g}s"
    )


async def _run_atlas_generation(
    endpoint_url: str,
    payload: Dict[str, Any],
    max_wait_seconds: float,
    poll_interval: float,
) -> Dict[str, Any]:
    submit_data = await _submit_prediction(endpoint_url, payload)
    prediction_id = _prediction_id(submit_data)
    if prediction_id:
        return await _poll_prediction(prediction_id, max_wait_seconds, poll_interval)

    # Some Atlas endpoints can return completed output directly. Keep this path
    # for compatibility, but normal tool calls use async submit plus poll.
    if _output_urls(submit_data):
        return submit_data
    raise RuntimeError(f"Atlas submit response missing prediction id. Keys: {list(submit_data.keys())}")


def _success(text: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": _redact_text(text)}]}


def _error(text: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": _redact_text(text)}], "is_error": True}


def _is_list_media_param(param_name: str) -> bool:
    normalized = param_name.strip().lower()
    return normalized in {
        "images",
        "reference_images",
        "image_urls",
        "videos",
        "video_urls",
    } or normalized.endswith("_urls")


def _video_media_config(model: str, image_param_name: Optional[str]) -> Dict[str, str]:
    if image_param_name:
        mode = "list" if _is_list_media_param(image_param_name) else "single"
        return {"param": image_param_name, "mode": mode}
    normalized = model.rstrip("/")
    if normalized == "bytedance/seedance-2.0/reference-to-video":
        return {"param": "reference_images", "mode": "list"}
    if normalized.endswith("/image-to-video") or "image-to-video" in normalized:
        return {"param": DEFAULT_VIDEO_IMAGE_PARAM, "mode": "single"}
    return {"param": DEFAULT_VIDEO_REFERENCE_PARAM, "mode": "list"}


# =============================================================================
# MCP Tools
# =============================================================================


@register_tool("image")
@tool(
    name="atlas_multi_ref_image",
    description="""Edit or generate an image from one or more reference images via Atlas Cloud.

This is the Atlas Cloud alternate provider path for Character/the user multi-reference
image work. Local references are uploaded to Atlas Cloud, URL references pass
through unchanged, and outputs are downloaded to 05_App_Data/generated_images/.

Default model: bytedance/seedream-v4.5/edit. The default reference parameter is
images, which supports multi-reference edit workflows. This tool uses Atlas
Cloud async submit and prediction polling. It requires ATLASCLOUD_API_KEY.""",
    input_schema={
        "type": "object",
        "properties": {
            "image_paths": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "description": "Reference image paths or URLs. Local paths are relative to the Second Brain root or absolute paths inside it.",
            },
            "prompt": {
                "type": "string",
                "description": "Edit or generation prompt.",
            },
            "model": {
                "type": "string",
                "description": "Atlas Cloud image model ID. Default: bytedance/seedream-v4.5/edit.",
                "default": DEFAULT_IMAGE_EDIT_MODEL,
            },
            "image_param_name": {
                "type": "string",
                "description": "Reference image URL parameter name. Default: images.",
                "default": DEFAULT_IMAGE_PARAM,
            },
            "size": {
                "type": "string",
                "description": "Optional Atlas size string, for example 1024*1024. Model support varies.",
            },
            "output_format": {
                "type": "string",
                "description": "Optional output format: jpeg, png, or webp when the model supports it.",
                "enum": ["jpeg", "png", "webp"],
            },
            "output_path": {
                "type": "string",
                "description": "Custom output path inside the Second Brain tree. Only used when one output is returned.",
            },
            "max_wait_seconds": {
                "type": "number",
                "description": "Maximum direct-call wait budget in seconds while the Atlas prediction is polled.",
                "default": DEFAULT_IMAGE_MAX_WAIT,
                "minimum": 1,
            },
            "extra_params": {
                "type": "object",
                "description": "Additional model-specific parameters merged into the Atlas request body. enable_sync_mode and enable_base64_output are ignored to preserve async URL output.",
            },
        },
        "required": ["image_paths", "prompt"],
    },
)
async def atlas_multi_ref_image(args: Dict[str, Any]) -> Dict[str, Any]:
    image_paths = args.get("image_paths", [])
    prompt = args.get("prompt", "").strip()
    if not image_paths:
        return _error("Error: image_paths is required (list of file paths or URLs)")
    if not prompt:
        return _error("Error: prompt is required")

    model = args.get("model", DEFAULT_IMAGE_EDIT_MODEL)
    image_param_name = args.get("image_param_name", DEFAULT_IMAGE_PARAM)
    size = args.get("size")
    output_format = args.get("output_format")
    output_path = args.get("output_path")
    max_wait_seconds = float(args.get("max_wait_seconds", DEFAULT_IMAGE_MAX_WAIT))
    extra_params = _strip_async_overrides(args.get("extra_params", {}) or {})

    try:
        media_urls, requested_paths = await _prepare_media_urls(image_paths, "reference image")

        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            image_param_name: media_urls,
        }
        if size:
            payload["size"] = size
        if output_format:
            payload["output_format"] = output_format
        payload.update(extra_params)

        logger.info(
            "Atlas multi-ref image: model=%s, param=%s, refs=%s, prompt=%s...",
            model,
            image_param_name,
            len(media_urls),
            prompt[:80],
        )
        result = await _run_atlas_generation(
            ATLAS_GENERATE_IMAGE_URL,
            payload,
            max_wait_seconds,
            IMAGE_POLL_INTERVAL,
        )
        output_urls = _output_urls(result)
        saved = await _save_output_urls(
            output_urls,
            output_path,
            "atlas_mref",
            ".png",
            _IMAGE_EXTENSIONS,
        )

        text = f"Generated image via Atlas Cloud {model} with {len(media_urls)} reference image(s):\n"
        text += f"  Prompt: {prompt}\n"
        text += f"  Model: {model}\n"
        text += f"  References: {_safe_repr(requested_paths)}\n"
        text += f"  Param: {image_param_name}\n"
        if size:
            text += f"  Size: {size}\n"
        if output_format:
            text += f"  Output format: {output_format}\n"
        if extra_params:
            text += f"  Extra: {_safe_repr(extra_params)}\n"
        text += "  Files:\n"
        for path, size_bytes in saved:
            text += f"    - {path} ({size_bytes} bytes)\n"
        return _success(text)

    except Exception as exc:
        msg = _fmt_exc(exc)
        logger.error("Atlas multi-ref image failed: %s", msg)
        return _error(f"Atlas multi-ref image failed: {msg}")


@register_tool("image")
@tool(
    name="atlas_reference_to_video",
    description="""Generate a video from reference/start images plus a prompt via Atlas Cloud.

Local references are uploaded to Atlas Cloud, URL references pass through
unchanged, and returned video outputs are downloaded to
05_App_Data/generated_images/. The default model is
vidu/q3/reference-to-video, which supports 1-4 reference images. Use model
overrides for image-to-video endpoints such as vidu/image-to-video-2.0 or
bytedance/seedance-2.0/image-to-video; those default to a single image parameter
named image. bytedance/seedance-2.0/reference-to-video uses the list parameter
reference_images; other reference-to-video models default to the list parameter
images. Requires ATLASCLOUD_API_KEY.""",
    input_schema={
        "type": "object",
        "properties": {
            "image_paths": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "description": "Reference/start image paths or URLs. Local paths are relative to the Second Brain root or absolute paths inside it.",
            },
            "prompt": {
                "type": "string",
                "description": "Video prompt.",
            },
            "model": {
                "type": "string",
                "description": "Atlas Cloud video model ID. Default: vidu/q3/reference-to-video.",
                "default": DEFAULT_VIDEO_MODEL,
            },
            "duration": {
                "type": ["string", "number"],
                "description": "Optional model-specific duration value.",
            },
            "resolution": {
                "type": "string",
                "description": "Optional model-specific resolution value.",
            },
            "aspect_ratio": {
                "type": "string",
                "description": "Optional model-specific aspect ratio value.",
            },
            "image_param_name": {
                "type": "string",
                "description": "Override the model-specific image URL parameter name. List parameters such as images/reference_images/image_urls receive all references; single parameters such as image receive only the first.",
            },
            "output_path": {
                "type": "string",
                "description": "Custom output path inside the Second Brain tree. If no extension is supplied, the tool uses the returned video extension or .mp4.",
            },
            "max_wait_seconds": {
                "type": "number",
                "description": "Maximum direct-call wait budget in seconds while the Atlas prediction is polled.",
                "default": DEFAULT_VIDEO_MAX_WAIT,
                "minimum": 1,
            },
            "extra_params": {
                "type": "object",
                "description": "Additional model-specific parameters merged into the Atlas request body. enable_sync_mode and enable_base64_output are ignored to preserve async URL output.",
            },
        },
        "required": ["image_paths", "prompt"],
    },
)
async def atlas_reference_to_video(args: Dict[str, Any]) -> Dict[str, Any]:
    image_paths = args.get("image_paths", [])
    prompt = args.get("prompt", "").strip()
    if not image_paths:
        return _error("Error: image_paths is required (list of file paths or URLs)")
    if not prompt:
        return _error("Error: prompt is required")

    model = args.get("model", DEFAULT_VIDEO_MODEL)
    duration = args.get("duration")
    resolution = args.get("resolution")
    aspect_ratio = args.get("aspect_ratio")
    image_param_name = args.get("image_param_name")
    output_path = args.get("output_path")
    max_wait_seconds = float(args.get("max_wait_seconds", DEFAULT_VIDEO_MAX_WAIT))
    extra_params = _strip_async_overrides(args.get("extra_params", {}) or {})
    media_config = _video_media_config(model, image_param_name)
    param_name = media_config["param"]
    mode = media_config["mode"]

    try:
        media_urls, requested_paths = await _prepare_media_urls(image_paths, "video reference image")
        ignored_reference_count = 0
        payload_urls: Any = media_urls
        source_paths = requested_paths
        if mode == "single":
            payload_urls = media_urls[0]
            if len(media_urls) > 1:
                ignored_reference_count = len(media_urls) - 1
                source_paths = requested_paths[:1]
                logger.warning(
                    "Atlas model %s uses a single image. Using first of %s provided.",
                    model,
                    len(media_urls),
                )

        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            param_name: payload_urls,
        }
        if duration is not None:
            payload["duration"] = duration
        if resolution:
            payload["resolution"] = resolution
        if aspect_ratio:
            payload["aspect_ratio"] = aspect_ratio
        payload.update(extra_params)

        logger.info(
            "Atlas reference-to-video: model=%s, param=%s, mode=%s, refs=%s, prompt=%s...",
            model,
            param_name,
            mode,
            len(media_urls),
            prompt[:80],
        )
        result = await _run_atlas_generation(
            ATLAS_GENERATE_VIDEO_URL,
            payload,
            max_wait_seconds,
            VIDEO_POLL_INTERVAL,
        )
        output_urls = _output_urls(result)
        saved = await _save_output_urls(
            output_urls,
            output_path,
            "atlas_video",
            ".mp4",
            _VIDEO_EXTENSIONS,
        )

        text = f"Generated video via Atlas Cloud {model} with {len(source_paths)} reference/start image(s):\n"
        text += f"  Prompt: {prompt}\n"
        text += f"  Model: {model}\n"
        if mode == "single":
            text += f"  Scene/start image: {source_paths[0]}\n"
        else:
            text += f"  References: {_safe_repr(source_paths)}\n"
        if ignored_reference_count:
            text += f"  Ignored references: {ignored_reference_count} extra image(s); model uses a single start image\n"
        text += f"  Param: {param_name}, Mode: {mode}\n"
        text += f"  Direct call: waits for completion (wait budget: {max_wait_seconds:g}s; Atlas prediction polling)\n"
        if duration is not None:
            text += f"  Requested duration: {duration}\n"
        if resolution:
            text += f"  Requested resolution: {resolution}\n"
        if aspect_ratio:
            text += f"  Requested aspect ratio: {aspect_ratio}\n"
        if extra_params:
            text += f"  Extra: {_safe_repr(extra_params)}\n"
        text += "  Files:\n"
        for path, size_bytes in saved:
            text += f"    - {path} ({size_bytes} bytes)\n"
        return _success(text)

    except Exception as exc:
        msg = _fmt_exc(exc)
        logger.error("Atlas reference-to-video failed: %s", msg)
        return _error(f"Atlas reference-to-video failed: {msg}")


__all__ = [
    "atlas_multi_ref_image",
    "atlas_reference_to_video",
]
