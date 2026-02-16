"""
fal.ai Image Generation Tools

Unified API for 600+ models including Seedream v4, Grok Imagine, Flux, etc.
Uses raw HTTP via httpx — no Python SDK dependency needed.

Tools:
    fal_text_to_image   — Text prompt → image
    fal_image_to_image  — Source image + prompt → modified image
    fal_multi_ref_image — Multiple reference images + prompt → image (Riley workflow)
    fal_list_models     — Search available models
"""

import asyncio
import hashlib
import logging
import mimetypes
import os
import time
from typing import Any, Dict, List, Optional

import httpx

from claude_agent_sdk import tool

from ..registry import register_tool

logger = logging.getLogger("mcp_tools.image.fal")

# =============================================================================
# Configuration
# =============================================================================

OUTPUT_DIR = os.path.expanduser("~/second_brain/05_App_Data/generated_images")

# Docs: https://docs.fal.ai/model-apis/model-endpoints
# Sync: fal.run, Queue: queue.fal.run — "the fal API does not use api.fal.ai"
FAL_RUN_BASE = "https://fal.run"
FAL_QUEUE_BASE = "https://queue.fal.run"
# Platform API (model search only) uses api.fal.ai
FAL_API_BASE = "https://api.fal.ai"

# CDN upload: two-step flow via fal-cdn-v3
# Step 1: POST to get token + base_url
FAL_CDN_TOKEN_URL = "https://rest.alpha.fal.ai/storage/auth/token"
# Step 2: POST {base_url}/files/upload with Bearer {token}
# Fallback: direct upload to v3.fal.media
FAL_CDN_DIRECT_URL = "https://v3.fal.media/files/upload"

DEFAULT_MODEL = "fal-ai/bytedance/seedream/v4/text-to-image"
DEFAULT_MULTI_REF_MODEL = "fal-ai/bytedance/seedream/v4.5/edit"
DEFAULT_IMAGE_SIZE = "portrait_4_3"

# =============================================================================
# Model-specific parameter mapping
# =============================================================================
# Different models use different parameter names for reference images.
# This map tells us how to pass image URLs to each model.
#
# Format: model_prefix -> {
#   "param": parameter name for the image URL(s),
#   "mode": "list" (list of URLs) or "single" (one URL string),
#   "use_image_size": whether to pass image_size (some models use aspect_ratio instead),
# }
#
# Models are matched by prefix — the first match wins.

_MULTI_REF_MODEL_MAP = {
    # --- Seedream (ByteDance) edit endpoints — up to 10 reference images ---
    # Seedream v4.5 edit (DEFAULT for multi-ref) — best quality, up to 10 images
    "fal-ai/bytedance/seedream/v4.5/edit": {
        "param": "image_urls",
        "mode": "list",
        "use_image_size": True,
    },
    # Seedream v4 edit — up to 10 images
    "fal-ai/bytedance/seedream/v4/edit": {
        "param": "image_urls",
        "mode": "list",
        "use_image_size": True,
    },
    # --- Grok Imagine (xAI) edit endpoint — single image ---
    "xai/grok-imagine-image/edit": {
        "param": "image_url",
        "mode": "single",
        "use_image_size": False,  # uses output_format only, no size control
    },
    # --- FLUX Kontext max multi — multi-ref character consistency ---
    "fal-ai/flux-pro/kontext/max/multi": {
        "param": "image_urls",
        "mode": "list",
        "use_image_size": False,  # uses aspect_ratio
    },
    # FLUX Kontext (single ref) — pass first image only
    "fal-ai/flux-pro/kontext": {
        "param": "image_url",
        "mode": "single",
        "use_image_size": False,  # uses aspect_ratio
    },
    # --- FLUX 2 edit endpoints — multi-ref with @image syntax in prompt ---
    "fal-ai/flux-2-pro/edit": {
        "param": "image_urls",
        "mode": "list",
        "use_image_size": True,
    },
    "fal-ai/flux-2/edit": {
        "param": "image_urls",
        "mode": "list",
        "use_image_size": True,
    },
    "fal-ai/flux-2-flex/edit": {
        "param": "image_urls",
        "mode": "list",
        "use_image_size": True,
    },
    # --- FLUX Subject — single subject image ---
    "fal-ai/flux-subject": {
        "param": "image_url",
        "mode": "single",
        "use_image_size": True,
    },
    # --- Ideogram Character — uses reference_image_urls ---
    "fal-ai/ideogram/character": {
        "param": "reference_image_urls",
        "mode": "list",
        "use_image_size": True,
    },
}


def _get_multi_ref_config(model: str) -> dict:
    """Get the reference image parameter config for a model.

    Returns a dict with 'param', 'mode', and 'use_image_size' keys.
    Falls back to a sensible default (image_urls as list) for unknown models.
    """
    # Try exact match first, then prefix match (longest prefix wins)
    if model in _MULTI_REF_MODEL_MAP:
        return _MULTI_REF_MODEL_MAP[model]

    best_match = None
    best_len = 0
    for prefix, config in _MULTI_REF_MODEL_MAP.items():
        if model.startswith(prefix) and len(prefix) > best_len:
            best_match = config
            best_len = len(prefix)

    if best_match:
        return best_match

    # Default for unknown models: try image_urls as a list
    return {"param": "image_urls", "mode": "list", "use_image_size": True}

# Timeout: generation can take a while, especially multi-ref
HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)
# Queue polling config
QUEUE_POLL_INTERVAL = 1.0
QUEUE_MAX_WAIT = 300  # 5 minutes


def _get_fal_key() -> str:
    """Get FAL_KEY from environment."""
    key = os.environ.get("FAL_KEY")
    if not key:
        raise ValueError("FAL_KEY environment variable not set")
    return key


def _auth_headers() -> dict:
    """Standard auth headers for fal.run endpoints."""
    return {
        "Authorization": f"Key {_get_fal_key()}",
        "Content-Type": "application/json",
    }


def _ensure_output_dir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def _generate_filename(prefix: str = "fal") -> str:
    """Generate a unique filename: YYYYMMDD_HHMMSS_{hash}.png"""
    ts = time.strftime("%Y%m%d_%H%M%S")
    short_hash = hashlib.md5(f"{time.time()}".encode()).hexdigest()[:8]
    return f"{ts}_{short_hash}.png"


def _resolve_path(path: str) -> str:
    """Resolve a path relative to project root or absolute."""
    if os.path.isabs(path):
        return path
    return os.path.join(os.path.expanduser("~/second_brain"), path)


def _get_mime_type(path: str) -> str:
    """Determine MIME type from file extension."""
    mime, _ = mimetypes.guess_type(path)
    return mime or "application/octet-stream"


async def _download_image(url: str, output_path: str) -> str:
    """Download an image from a URL and save to disk."""
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(resp.content)
    return output_path


async def _upload_to_fal_cdn(file_path: str) -> str:
    """Upload a local file to fal's CDN (v3) and return the access URL.

    Flow (fal-cdn-v3):
    1. POST to rest.alpha.fal.ai/storage/auth/token to get {token, base_url}
    2. POST file bytes to {base_url}/files/upload with Bearer token
    Returns the CDN access_url (e.g. https://v3.fal.media/files/...)
    """
    resolved = _resolve_path(file_path)
    if not os.path.isfile(resolved):
        raise FileNotFoundError(f"File not found: {resolved}")

    fal_key = _get_fal_key()
    mime_type = _get_mime_type(resolved)
    filename = os.path.basename(resolved)

    with open(resolved, "rb") as f:
        file_bytes = f.read()

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        # Step 1: Get CDN auth token
        token_resp = await client.post(
            FAL_CDN_TOKEN_URL,
            headers={
                "Authorization": f"Key {fal_key}",
                "Content-Type": "application/json",
            },
            json={"storage_type": "fal-cdn-v3"},
        )

        if token_resp.status_code == 200:
            token_data = token_resp.json()
            cdn_token = token_data["token"]
            # Always use v3 CDN — the token response may return v2 which has issues
            upload_url = FAL_CDN_DIRECT_URL
            auth_header = f"Bearer {cdn_token}"
        else:
            # Fallback: upload directly with API key auth
            logger.warning(
                f"CDN token request failed ({token_resp.status_code}), "
                "falling back to direct upload"
            )
            upload_url = FAL_CDN_DIRECT_URL
            auth_header = f"Key {fal_key}"

        # Step 2: Upload the file
        upload_resp = await client.post(
            upload_url,
            headers={
                "Authorization": auth_header,
                "Content-Type": mime_type,
                "X-Fal-File-Name": filename,
            },
            content=file_bytes,
        )
        upload_resp.raise_for_status()
        upload_data = upload_resp.json()
        access_url = upload_data.get("access_url") or upload_data.get("url")
        if not access_url:
            raise RuntimeError(
                f"CDN upload response missing access_url. Keys: {list(upload_data.keys())}"
            )
        logger.info(f"Uploaded {filename} to CDN: {access_url}")
        return access_url


async def _fal_run(model: str, payload: dict, use_queue: bool = False) -> dict:
    """Call a fal model endpoint and return the JSON response.

    Args:
        model: The model endpoint ID (e.g. 'fal-ai/flux/dev')
        payload: Request body dict
        use_queue: If True, use queue.fal.run with polling instead of sync fal.run
    """
    if use_queue:
        return await _fal_queue_run(model, payload)

    url = f"{FAL_RUN_BASE}/{model}"
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.post(url, headers=_auth_headers(), json=payload)
        if resp.status_code == 200:
            return resp.json()

        error_detail = resp.text
        try:
            error_json = resp.json()
            error_detail = error_json.get("detail", error_json)
        except Exception:
            pass

        # If sync times out (504), automatically retry via queue
        if resp.status_code == 504:
            logger.warning(f"Sync request timed out for {model}, retrying via queue...")
            return await _fal_queue_run(model, payload)

        raise RuntimeError(
            f"fal.run returned {resp.status_code} for {model}: {error_detail}"
        )


async def _fal_queue_run(model: str, payload: dict) -> dict:
    """Submit a job to the queue and poll for results.

    Uses queue.fal.run which is more reliable for long-running models.
    Docs: https://docs.fal.ai/model-apis/model-endpoints/queue
    """
    submit_url = f"{FAL_QUEUE_BASE}/{model}"
    headers = _auth_headers()

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        # Submit job
        submit_resp = await client.post(submit_url, headers=headers, json=payload)
        if submit_resp.status_code not in (200, 201):
            error_detail = submit_resp.text
            try:
                error_detail = submit_resp.json().get("detail", error_detail)
            except Exception:
                pass
            raise RuntimeError(
                f"Queue submit failed ({submit_resp.status_code}) for {model}: {error_detail}"
            )

        submit_data = submit_resp.json()
        request_id = submit_data.get("request_id")
        if not request_id:
            raise RuntimeError(f"Queue submit response missing request_id: {submit_data}")

        # Poll for completion
        status_url = f"{FAL_QUEUE_BASE}/{model}/requests/{request_id}/status"
        result_url = f"{FAL_QUEUE_BASE}/{model}/requests/{request_id}"
        elapsed = 0.0

        while elapsed < QUEUE_MAX_WAIT:
            await asyncio.sleep(QUEUE_POLL_INTERVAL)
            elapsed += QUEUE_POLL_INTERVAL

            status_resp = await client.get(status_url, headers=headers)
            if status_resp.status_code != 200:
                continue  # Transient error, keep polling

            status_data = status_resp.json()
            status = status_data.get("status")

            if status == "COMPLETED":
                result_resp = await client.get(result_url, headers=headers)
                result_resp.raise_for_status()
                return result_resp.json()
            elif status in ("IN_QUEUE", "IN_PROGRESS"):
                pos = status_data.get("queue_position", "?")
                if elapsed % 10 < QUEUE_POLL_INTERVAL:  # Log every ~10s
                    logger.info(f"Queue status for {model}: {status} (position: {pos})")
            else:
                # Unknown status or error
                raise RuntimeError(f"Queue job failed for {model}: {status_data}")

        raise RuntimeError(
            f"Queue job timed out after {QUEUE_MAX_WAIT}s for {model}"
        )


def _save_result_images(
    result: dict,
    output_path: Optional[str] = None,
    prefix: str = "fal",
) -> List[str]:
    """Extract image URLs from a fal response and save them locally.

    Returns list of saved file paths. Runs synchronously (downloads inline).
    """
    images = result.get("images", [])
    if not images:
        # Some models return a single image differently
        image_url = result.get("image", {}).get("url") if isinstance(result.get("image"), dict) else None
        if image_url:
            images = [{"url": image_url}]
        else:
            raise ValueError(f"No images in response. Keys: {list(result.keys())}")

    saved = []
    _ensure_output_dir()

    for i, img in enumerate(images):
        url = img.get("url") if isinstance(img, dict) else img
        if not url:
            continue

        if output_path and len(images) == 1:
            resolved_out = _resolve_path(output_path)
            os.makedirs(os.path.dirname(resolved_out), exist_ok=True)
            dest = resolved_out
        else:
            filename = _generate_filename(prefix=f"{prefix}_{i+1}")
            dest = os.path.join(OUTPUT_DIR, filename)

        # Synchronous download (we're already in an async context,
        # but httpx sync is fine for small image files)
        with httpx.Client(timeout=HTTP_TIMEOUT) as dl_client:
            dl_resp = dl_client.get(url)
            dl_resp.raise_for_status()
            with open(dest, "wb") as f:
                f.write(dl_resp.content)

        saved.append(dest)
        logger.info(f"Saved fal image to {dest}")

    return saved


def _success(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _error(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


# =============================================================================
# MCP Tools
# =============================================================================

@register_tool("image")
@tool(
    name="fal_text_to_image",
    description="""Generate images from a text prompt using fal.ai's model library (600+ models).

Default model: Seedream v4 (fal-ai/bytedance/seedream/v4/text-to-image) — ByteDance's top image model.
Other popular models: fal-ai/flux/dev, fal-ai/flux-pro/v1.1, fal-ai/recraft-v3, etc.
Use fal_list_models to discover correct endpoint IDs.

Common params: prompt, image_size, num_images. Model-specific params go in extra_params.
If sync times out, the tool automatically retries via the queue API.

Images are saved to 05_App_Data/generated_images/ and file path(s) are returned.""",
    input_schema={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Text prompt describing the image to generate.",
            },
            "model": {
                "type": "string",
                "description": "fal.ai model endpoint ID (e.g. 'fal-ai/bytedance/seedream/v4/text-to-image', 'fal-ai/flux/dev'). Use fal_list_models to find correct IDs.",
                "default": DEFAULT_MODEL,
            },
            "image_size": {
                "type": ["string", "object"],
                "description": "Image size. Either a preset string ('square_hd', 'square', 'portrait_4_3', 'portrait_16_9', 'landscape_4_3', 'landscape_16_9') or an object like {\"width\": 1024, \"height\": 768}.",
                "default": DEFAULT_IMAGE_SIZE,
            },
            "num_images": {
                "type": "integer",
                "description": "Number of images to generate (1-4).",
                "default": 1,
                "minimum": 1,
                "maximum": 4,
            },
            "negative_prompt": {
                "type": "string",
                "description": "Things to avoid in the generated image.",
            },
            "seed": {
                "type": "integer",
                "description": "Seed for reproducibility.",
            },
            "output_path": {
                "type": "string",
                "description": "Custom output path (relative to project root or absolute). Only used when generating a single image.",
            },
            "use_queue": {
                "type": "boolean",
                "description": "Force queue-based execution (more reliable for slow models). Default: false (sync with auto-fallback to queue on timeout).",
                "default": False,
            },
            "extra_params": {
                "type": "object",
                "description": "Additional model-specific parameters merged directly into the request body. Use this for params not covered above (e.g. guidance_scale, num_inference_steps, aspect_ratio, etc.).",
            },
        },
        "required": ["prompt"],
    },
)
async def fal_text_to_image(args: Dict[str, Any]) -> Dict[str, Any]:
    """Generate image(s) from a text prompt via fal.ai."""
    prompt = args.get("prompt", "").strip()
    if not prompt:
        return _error("Error: prompt cannot be empty")

    model = args.get("model", DEFAULT_MODEL)
    image_size = args.get("image_size", DEFAULT_IMAGE_SIZE)
    num_images = min(max(args.get("num_images", 1), 1), 4)
    negative_prompt = args.get("negative_prompt")
    seed = args.get("seed")
    output_path = args.get("output_path")
    use_queue = args.get("use_queue", False)
    extra_params = args.get("extra_params", {}) or {}

    payload = {
        "prompt": prompt,
        "image_size": image_size,
        "num_images": num_images,
        "enable_safety_checker": False,
        **extra_params,
    }
    if negative_prompt:
        payload["negative_prompt"] = negative_prompt
    if seed is not None:
        payload["seed"] = seed

    try:
        logger.info(f"fal text-to-image: model={model}, prompt={prompt[:80]}...")
        result = await _fal_run(model, payload, use_queue=use_queue)

        saved = _save_result_images(result, output_path=output_path, prefix="t2i")

        seed_used = result.get("seed", "unknown")
        text = f"Generated {len(saved)} image(s) via {model} (seed: {seed_used}):\n"
        text += f"  Prompt: {prompt}\n"
        text += f"  Model: {model}\n"
        text += f"  Size: {image_size}\n"
        if negative_prompt:
            text += f"  Negative: {negative_prompt}\n"
        if extra_params:
            text += f"  Extra: {extra_params}\n"
        text += "  Files:\n"
        for p in saved:
            text += f"    - {p}\n"
        return _success(text)

    except Exception as e:
        logger.error(f"fal text-to-image failed: {e}")
        return _error(f"fal text-to-image failed: {e}")


@register_tool("image")
@tool(
    name="fal_image_to_image",
    description="""Edit or transform an image using a source image + text prompt via fal.ai.

Uploads the source image to fal's CDN, then passes the CDN URL to the model.
Most models use 'image_url' as the parameter name for the source image (this is the default).
Use image_param_name to override if a model uses a different name.

Popular i2i models: fal-ai/flux/dev/image-to-image, fal-ai/bytedance/seedream/v4.5/edit,
fal-ai/bytedance/seedream/v4/edit, xai/grok-imagine-image/edit.
Use fal_list_models with category='image-to-image' to discover more.

Edited image is saved to 05_App_Data/generated_images/ and the file path is returned.""",
    input_schema={
        "type": "object",
        "properties": {
            "image_path": {
                "type": "string",
                "description": "Path to source image (relative to project root or absolute). Can also be a URL starting with http.",
            },
            "prompt": {
                "type": "string",
                "description": "Text prompt describing the desired edit or transformation.",
            },
            "model": {
                "type": "string",
                "description": "fal.ai model endpoint ID. Use an image-to-image model (e.g. 'fal-ai/flux/dev/image-to-image').",
                "default": "fal-ai/flux/dev/image-to-image",
            },
            "strength": {
                "type": "number",
                "description": "How much to change the image (0.0 = preserve original, 1.0 = full transformation). Default varies by model (FLUX default: 0.95).",
                "minimum": 0.0,
                "maximum": 1.0,
            },
            "image_size": {
                "type": ["string", "object"],
                "description": "Output image size. Either a preset string or {\"width\": N, \"height\": N}.",
            },
            "output_path": {
                "type": "string",
                "description": "Custom output path.",
            },
            "image_param_name": {
                "type": "string",
                "description": "Override the parameter name for the source image URL. Default: 'image_url'. Some models use 'image', 'input_image', etc.",
            },
            "use_queue": {
                "type": "boolean",
                "description": "Force queue-based execution.",
                "default": False,
            },
            "extra_params": {
                "type": "object",
                "description": "Additional model-specific parameters (e.g. num_inference_steps, guidance_scale).",
            },
        },
        "required": ["image_path", "prompt"],
    },
)
async def fal_image_to_image(args: Dict[str, Any]) -> Dict[str, Any]:
    """Edit an image using a source image + prompt via fal.ai."""
    image_path = args.get("image_path", "").strip()
    prompt = args.get("prompt", "").strip()

    if not image_path:
        return _error("Error: image_path is required")
    if not prompt:
        return _error("Error: prompt is required")

    model = args.get("model", "fal-ai/flux/dev/image-to-image")
    strength = args.get("strength")
    image_size = args.get("image_size")
    output_path = args.get("output_path")
    image_param_name = args.get("image_param_name")
    use_queue = args.get("use_queue", False)
    extra_params = args.get("extra_params", {}) or {}

    try:
        # Upload source image to fal CDN (skip if already a URL)
        if image_path.startswith(("http://", "https://")):
            cdn_url = image_path
        else:
            logger.info(f"Uploading source image: {image_path}")
            cdn_url = await _upload_to_fal_cdn(image_path)

        # Build payload
        param_name = image_param_name or "image_url"
        payload = {
            "prompt": prompt,
            param_name: cdn_url,
            "enable_safety_checker": False,
            **extra_params,
        }
        if strength is not None:
            payload["strength"] = strength
        if image_size:
            payload["image_size"] = image_size

        logger.info(f"fal image-to-image: model={model}, prompt={prompt[:80]}...")
        result = await _fal_run(model, payload, use_queue=use_queue)

        saved = _save_result_images(result, output_path=output_path, prefix="i2i")

        text = f"Edited image via {model}:\n"
        text += f"  Prompt: {prompt}\n"
        text += f"  Source: {image_path}\n"
        text += f"  Model: {model}\n"
        if strength is not None:
            text += f"  Strength: {strength}\n"
        if image_size:
            text += f"  Size: {image_size}\n"
        if extra_params:
            text += f"  Extra: {extra_params}\n"
        text += "  Files:\n"
        for p in saved:
            text += f"    - {p}\n"
        return _success(text)

    except Exception as e:
        logger.error(f"fal image-to-image failed: {e}")
        return _error(f"fal image-to-image failed: {e}")


@register_tool("image")
@tool(
    name="fal_multi_ref_image",
    description="""Generate an image using reference images + a text prompt via fal.ai.

Key workflow for character consistency (e.g., Riley). Reference images are uploaded to
fal's CDN, then their URLs are passed to the model's edit/image-input endpoint.

Default model: Seedream v4.5 Edit — accepts up to 10 reference images, highest quality.

Recommended models for reference images (sorted by capability):
  - fal-ai/bytedance/seedream/v4.5/edit (DEFAULT, up to 10 refs, best quality, uses image_urls)
  - fal-ai/bytedance/seedream/v4/edit   (up to 10 refs, uses image_urls)
  - fal-ai/flux-pro/kontext/max/multi   (multi-ref character consistency, uses image_urls)
  - fal-ai/flux-pro/kontext             (single ref, uses image_url)
  - fal-ai/flux-2-pro/edit              (up to 4 refs, uses image_urls + @image1/@image2 in prompt)
  - fal-ai/flux-2/edit                  (up to 4 refs, uses image_urls + @image syntax)
  - xai/grok-imagine-image/edit         (single ref, uses image_url — Grok Imagine edit)
  - fal-ai/flux-subject                 (single subject ref, uses image_url)
  - fal-ai/ideogram/character           (character ref, uses reference_image_urls)

NOTE: Seedream and Grok text-to-image endpoints do NOT accept images.
Use the /edit endpoints listed above — those are different model endpoints.

The tool auto-detects the correct parameter name and mode (list vs single) for known models.
Override with image_param_name only if using an unlisted model.

Image is saved to 05_App_Data/generated_images/ and the file path is returned.""",
    input_schema={
        "type": "object",
        "properties": {
            "image_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of paths to reference images (local paths or URLs starting with http).",
            },
            "prompt": {
                "type": "string",
                "description": "Text prompt describing the desired image. For flux-2-pro/edit or flux-2/edit models, reference images in the prompt using @image1, @image2, etc.",
            },
            "model": {
                "type": "string",
                "description": "fal.ai model endpoint ID. Must be a model that supports reference images (use an /edit endpoint, not /text-to-image). Default: fal-ai/bytedance/seedream/v4.5/edit.",
                "default": DEFAULT_MULTI_REF_MODEL,
            },
            "image_size": {
                "type": ["string", "object"],
                "description": "Output image size. Either a preset string ('square_hd', 'square', 'portrait_4_3', 'portrait_16_9', 'landscape_4_3', 'landscape_16_9') or {\"width\": N, \"height\": N}. Not used by all models (some use aspect_ratio instead — handled automatically).",
                "default": DEFAULT_IMAGE_SIZE,
            },
            "output_path": {
                "type": "string",
                "description": "Custom output path.",
            },
            "image_param_name": {
                "type": "string",
                "description": "Override the auto-detected parameter name for reference image URLs. Only needed for models not in the built-in mapping.",
            },
            "use_queue": {
                "type": "boolean",
                "description": "Force queue-based execution.",
                "default": False,
            },
            "extra_params": {
                "type": "object",
                "description": "Additional model-specific parameters.",
            },
        },
        "required": ["image_paths", "prompt"],
    },
)
async def fal_multi_ref_image(args: Dict[str, Any]) -> Dict[str, Any]:
    """Generate an image from reference images + prompt via fal.ai."""
    image_paths = args.get("image_paths", [])
    prompt = args.get("prompt", "").strip()

    if not image_paths:
        return _error("Error: image_paths is required (list of file paths)")
    if not prompt:
        return _error("Error: prompt is required")

    model = args.get("model", DEFAULT_MULTI_REF_MODEL)
    image_size = args.get("image_size", DEFAULT_IMAGE_SIZE)
    output_path = args.get("output_path")
    image_param_name_override = args.get("image_param_name")
    use_queue = args.get("use_queue", False)
    extra_params = args.get("extra_params", {}) or {}

    # Get model-specific config for reference image parameters
    ref_config = _get_multi_ref_config(model)
    param_name = image_param_name_override or ref_config["param"]
    ref_mode = ref_config["mode"]
    use_image_size = ref_config["use_image_size"]

    try:
        # Upload all reference images to fal CDN (skip URLs)
        cdn_urls = []
        for i, path in enumerate(image_paths):
            path = path.strip()
            if path.startswith(("http://", "https://")):
                cdn_urls.append(path)
                continue
            resolved = _resolve_path(path)
            if not os.path.isfile(resolved):
                return _error(f"Reference image not found: {resolved}")
            logger.info(f"Uploading reference image {i+1}/{len(image_paths)}: {path}")
            url = await _upload_to_fal_cdn(path)
            cdn_urls.append(url)

        if not cdn_urls:
            return _error("Error: no valid reference images after upload")

        # Build payload with model-aware parameter handling
        payload = {
            "prompt": prompt,
            "enable_safety_checker": False,
            **extra_params,
        }

        # Set the reference image parameter using the correct name and mode
        if ref_mode == "single":
            # Model accepts a single image URL string
            payload[param_name] = cdn_urls[0]
            if len(cdn_urls) > 1:
                logger.warning(
                    f"Model {model} only accepts a single reference image. "
                    f"Using first of {len(cdn_urls)} provided."
                )
        else:
            # Model accepts a list of image URLs
            payload[param_name] = cdn_urls

        # Handle image_size vs aspect_ratio based on model
        if use_image_size:
            payload["image_size"] = image_size
        # Models that don't use image_size (like Kontext) use aspect_ratio instead.
        # If caller passed an aspect_ratio in extra_params, it's already merged.
        # Otherwise the model uses its own default.

        logger.info(
            f"fal multi-ref: model={model}, param={param_name}, "
            f"mode={ref_mode}, {len(cdn_urls)} refs, prompt={prompt[:80]}..."
        )
        result = await _fal_run(model, payload, use_queue=use_queue)

        saved = _save_result_images(result, output_path=output_path, prefix="mref")

        text = f"Generated image via {model} with {len(cdn_urls)} reference image(s):\n"
        text += f"  Prompt: {prompt}\n"
        text += f"  Model: {model}\n"
        text += f"  References: {image_paths}\n"
        text += f"  Param: {param_name}, Mode: {ref_mode}\n"
        if use_image_size:
            text += f"  Size: {image_size}\n"
        if extra_params:
            text += f"  Extra: {extra_params}\n"
        text += "  Files:\n"
        for p in saved:
            text += f"    - {p}\n"
        return _success(text)

    except Exception as e:
        logger.error(f"fal multi-ref failed: {e}")
        return _error(f"fal multi-ref failed: {e}")


@register_tool("image")
@tool(
    name="fal_list_models",
    description="""Search and list available models on fal.ai.

Can search by text query, filter by category/status, or look up specific endpoint IDs.
Returns model IDs, names, descriptions, and categories. Use this to discover correct
endpoint IDs for the other fal tools.

Common categories: text-to-image, image-to-image, image-to-video, text-to-video, training.
The endpoint_id returned here is exactly what you pass as 'model' to the other tools.""",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Free-text search query (e.g. 'seedream', 'flux', 'grok imagine').",
            },
            "endpoint_id": {
                "type": "string",
                "description": "Look up a specific model by endpoint ID (e.g. 'fal-ai/flux/dev'). Returns full details for that model.",
            },
            "category": {
                "type": "string",
                "description": "Filter by category: 'text-to-image', 'image-to-image', 'image-to-video', 'text-to-video', 'training', etc.",
            },
            "status": {
                "type": "string",
                "description": "Filter by status: 'active' or 'deprecated'. Default: all.",
                "enum": ["active", "deprecated"],
            },
            "limit": {
                "type": "integer",
                "description": "Max number of results to return.",
                "default": 20,
                "minimum": 1,
                "maximum": 100,
            },
        },
    },
)
async def fal_list_models(args: Dict[str, Any]) -> Dict[str, Any]:
    """Search available models on fal.ai."""
    query = args.get("query", "")
    endpoint_id = args.get("endpoint_id", "")
    category = args.get("category", "")
    status = args.get("status", "")
    limit = min(max(args.get("limit", 20), 1), 100)

    params: Dict[str, Any] = {"limit": limit}
    if query:
        params["q"] = query
    if endpoint_id:
        params["endpoint_id"] = endpoint_id
    if category:
        params["category"] = category
    if status:
        params["status"] = status

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(
                f"{FAL_API_BASE}/v1/models",
                headers={"Authorization": f"Key {_get_fal_key()}"},
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

        models = data.get("models", [])
        if not models:
            return _success("No models found matching your query.")

        lines = [f"Found {len(models)} model(s):\n"]
        for m in models:
            eid = m.get("endpoint_id", "unknown")
            meta = m.get("metadata", {})
            name = meta.get("display_name", eid)
            cat = meta.get("category", "")
            desc = meta.get("description", "")
            m_status = meta.get("status", "")
            tags = meta.get("tags", [])
            price_est = meta.get("duration_estimate")

            line = f"  [{eid}] {name}"
            if cat:
                line += f" ({cat})"
            if m_status and m_status != "active":
                line += f" [{m_status}]"
            if tags:
                line += f" {' '.join(f'#{t}' for t in tags[:3])}"
            lines.append(line)
            if desc:
                lines.append(f"    {desc[:150]}")
            if price_est:
                lines.append(f"    ~{price_est:.1f}s per run")

        has_more = data.get("has_more", False)
        if has_more:
            lines.append(f"\n  (more results available — increase limit or refine query)")

        return _success("\n".join(lines))

    except Exception as e:
        logger.error(f"fal list models failed: {e}")
        return _error(f"fal list models failed: {e}")


__all__ = [
    "fal_text_to_image",
    "fal_image_to_image",
    "fal_multi_ref_image",
    "fal_list_models",
]
