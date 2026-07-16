"""Bounded parallel orchestration for independent Atlas Cloud image jobs."""

import asyncio
import json
import math
import os
from typing import Any, Dict, List, Set

from claude_agent_sdk import tool

from ..registry import register_tool
from . import atlas_generation


MAX_BATCH_JOBS = 4
MAX_CONCURRENT_ATLAS_JOBS = 4
MAX_JOB_ID_LENGTH = 64

_ALLOWED_JOB_KEYS = {
    "id",
    "image_paths",
    "prompt",
    "model",
    "image_param_name",
    "size",
    "output_format",
    "output_path",
    "max_wait_seconds",
    "extra_params",
}
_REQUIRED_JOB_KEYS = {"image_paths", "prompt"}
_STRING_JOB_KEYS = {"model", "image_param_name", "size", "output_path"}
_OUTPUT_FORMATS = {"jpeg", "png", "webp"}


def _validation_error(message: str) -> ValueError:
    return ValueError(f"Atlas parallel image validation failed: {message}")


def _format_keys(keys: Set[Any]) -> str:
    return ", ".join(sorted(str(key) for key in keys))


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def _output_target_candidates(output_path: str) -> Set[str]:
    """Return every concrete image path an explicit Atlas target can become."""
    if os.path.splitext(output_path)[1]:
        return {atlas_generation._resolve_path(output_path)}
    return {
        atlas_generation._resolve_path(f"{output_path}{extension}")
        for extension in atlas_generation._IMAGE_EXTENSIONS
    }


def _preflight(args: Any) -> List[Dict[str, Any]]:
    if not isinstance(args, dict):
        raise _validation_error("arguments must be an object")

    unexpected_top_level = set(args) - {"jobs"}
    if unexpected_top_level:
        raise _validation_error(
            f"unexpected top-level key(s): {_format_keys(unexpected_top_level)}"
        )

    jobs = args.get("jobs")
    if not isinstance(jobs, list):
        raise _validation_error("jobs must be an array")
    if not 1 <= len(jobs) <= MAX_BATCH_JOBS:
        raise _validation_error(
            f"jobs must contain between 1 and {MAX_BATCH_JOBS} items"
        )

    validated: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()
    output_target_owners: Dict[str, int] = {}

    for index, raw_job in enumerate(jobs):
        if not isinstance(raw_job, dict):
            raise _validation_error(f"jobs[{index}] must be an object")

        unexpected_keys = set(raw_job) - _ALLOWED_JOB_KEYS
        if unexpected_keys:
            raise _validation_error(
                f"jobs[{index}] has unexpected key(s): {_format_keys(unexpected_keys)}"
            )
        missing_keys = _REQUIRED_JOB_KEYS - set(raw_job)
        if missing_keys:
            raise _validation_error(
                f"jobs[{index}] is missing required key(s): {_format_keys(missing_keys)}"
            )

        if "id" in raw_job:
            job_id = raw_job["id"]
            if not isinstance(job_id, str) or not job_id.strip():
                raise _validation_error(f"jobs[{index}].id must be a nonblank string")
            if len(job_id) > MAX_JOB_ID_LENGTH:
                raise _validation_error(
                    f"jobs[{index}].id must be at most {MAX_JOB_ID_LENGTH} characters"
                )
            if job_id in seen_ids:
                raise _validation_error(f"duplicate job id: {job_id}")
            seen_ids.add(job_id)

        image_paths = raw_job["image_paths"]
        if not isinstance(image_paths, list) or not image_paths:
            raise _validation_error(
                f"jobs[{index}].image_paths must be a nonempty array"
            )
        for path_index, image_path in enumerate(image_paths):
            if not isinstance(image_path, str) or not image_path.strip():
                raise _validation_error(
                    f"jobs[{index}].image_paths[{path_index}] must be a nonempty string"
                )

        prompt = raw_job["prompt"]
        if not isinstance(prompt, str) or not prompt.strip():
            raise _validation_error(f"jobs[{index}].prompt must be a nonblank string")

        for key in _STRING_JOB_KEYS:
            if key in raw_job and not isinstance(raw_job[key], str):
                raise _validation_error(f"jobs[{index}].{key} must be a string")

        if "output_format" in raw_job:
            output_format = raw_job["output_format"]
            if not isinstance(output_format, str) or output_format not in _OUTPUT_FORMATS:
                raise _validation_error(
                    f"jobs[{index}].output_format must be one of: jpeg, png, webp"
                )

        if "max_wait_seconds" in raw_job:
            max_wait_seconds = raw_job["max_wait_seconds"]
            if not _is_finite_number(max_wait_seconds) or max_wait_seconds < 1:
                raise _validation_error(
                    f"jobs[{index}].max_wait_seconds must be a finite number greater than or equal to 1"
                )

        if "extra_params" in raw_job and not isinstance(raw_job["extra_params"], dict):
            raise _validation_error(f"jobs[{index}].extra_params must be an object")

        output_path = raw_job.get("output_path")
        if output_path:
            try:
                candidates = _output_target_candidates(output_path)
            except Exception as exc:
                raise _validation_error(
                    f"jobs[{index}].output_path is invalid: {atlas_generation._fmt_exc(exc)}"
                ) from exc
            for candidate in candidates:
                previous_index = output_target_owners.get(candidate)
                if previous_index is not None:
                    raise _validation_error(
                        "duplicate explicit output target between "
                        f"jobs[{previous_index}] and jobs[{index}]"
                    )
            for candidate in candidates:
                output_target_owners[candidate] = index

        validated.append(dict(raw_job))

    return validated


def _content_text(result: Dict[str, Any]) -> str:
    content = result.get("content")
    if not isinstance(content, list):
        return ""
    texts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]
    return "\n".join(text for text in texts if text)


async def _run_job(
    index: int,
    job: Dict[str, Any],
    semaphore: asyncio.Semaphore,
) -> Dict[str, Any]:
    item: Dict[str, Any] = {"index": index}
    if "id" in job:
        item["id"] = job["id"]
    atlas_args = {key: value for key, value in job.items() if key != "id"}

    try:
        async with semaphore:
            result = await atlas_generation.atlas_multi_ref_image.handler(atlas_args)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        item.update(
            status="failed",
            error_text=f"Atlas image job raised {atlas_generation._fmt_exc(exc)}",
        )
        return item

    if not isinstance(result, dict):
        item.update(
            status="failed",
            error_text="Atlas image job returned an invalid result",
        )
        return item

    result_text = _content_text(result)
    if result.get("is_error"):
        item.update(
            status="failed",
            error_text=result_text or "Atlas image job returned an error without text",
        )
    elif not result_text:
        item.update(
            status="failed",
            error_text="Atlas image job returned no text receipt",
        )
    else:
        item.update(status="succeeded", output_text=result_text)
    return item


@register_tool("image")
@tool(
    name="atlas_generate_images_parallel",
    description="""Generate 1-4 independent Atlas Cloud image jobs concurrently.

Each job accepts the same image fields as atlas_multi_ref_image and runs through
that existing Atlas handler under a fixed four-slot async concurrency bound.
Results stay in input order, and one failed Atlas job does not stop its siblings.
The complete batch is validated before any job starts. Cancelling this call
cancels and awaits unfinished local tasks, but Atlas work already submitted to
the provider may continue because the current Atlas path has no remote-cancel
API.""",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "jobs": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_BATCH_JOBS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "id": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": MAX_JOB_ID_LENGTH,
                            "description": "Optional correlation ID, unique within this batch.",
                        },
                        "image_paths": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                            "minItems": 1,
                            "description": "Reference image paths or URLs for this Atlas job.",
                        },
                        "prompt": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Edit or generation prompt for this Atlas job.",
                        },
                        "model": {
                            "type": "string",
                            "default": atlas_generation.DEFAULT_IMAGE_EDIT_MODEL,
                            "description": "Atlas Cloud image model ID.",
                        },
                        "image_param_name": {
                            "type": "string",
                            "default": atlas_generation.DEFAULT_IMAGE_PARAM,
                            "description": "Reference image URL parameter name.",
                        },
                        "size": {
                            "type": "string",
                            "description": "Optional Atlas size string, for example 1024*1024.",
                        },
                        "output_format": {
                            "type": "string",
                            "enum": ["jpeg", "png", "webp"],
                            "description": "Optional Atlas output format.",
                        },
                        "output_path": {
                            "type": "string",
                            "description": "Optional custom output path inside the Second Brain tree.",
                        },
                        "max_wait_seconds": {
                            "type": "number",
                            "minimum": 1,
                            "default": atlas_generation.DEFAULT_IMAGE_MAX_WAIT,
                            "description": "Maximum polling wait for this Atlas job.",
                        },
                        "extra_params": {
                            "type": "object",
                            "description": "Additional model-specific Atlas request parameters.",
                        },
                    },
                    "required": ["image_paths", "prompt"],
                },
            }
        },
        "required": ["jobs"],
    },
)
async def atlas_generate_images_parallel(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        jobs = _preflight(args)
    except Exception as exc:
        return atlas_generation._error(atlas_generation._fmt_exc(exc))

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_ATLAS_JOBS)
    tasks: List[asyncio.Task] = []
    try:
        tasks = [
            asyncio.create_task(_run_job(index, job, semaphore))
            for index, job in enumerate(jobs)
        ]
        results = await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        raise
    except Exception as exc:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return atlas_generation._error(
            f"Atlas parallel image orchestration failed: {atlas_generation._fmt_exc(exc)}"
        )

    succeeded = sum(result["status"] == "succeeded" for result in results)
    envelope = {
        "status": "completed",
        "total": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "results": results,
    }
    text = json.dumps(
        envelope,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return atlas_generation._success(text)
