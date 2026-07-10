"""
LLM consultation tool for peer checks from external model CLIs.

The registered MCP tool lives here. Older utility imports are compatibility
shims and must not register another ``consult_llm`` implementation.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from claude_agent_sdk import tool
from subprocess_cleanup import terminate_process_tree

from ..registry import register_tool

try:
    from codex_backend import resolve_codex_bin
except ImportError:  # pragma: no cover - only used if imported outside server root
    resolve_codex_bin = None

logger = logging.getLogger("mcp_tools.llm")

# =============================================================================
# Configuration
# =============================================================================

REPO_ROOT = Path(__file__).resolve().parents[4]

DEFAULT_GEMINI_MODEL = "gemini-3-pro-preview"
DEFAULT_OPENAI_MODEL = "gpt-5.6-luna"
OPENAI_MODEL_ENV = "SECOND_BRAIN_CONSULT_OPENAI_MODEL"
OPENAI_EFFORT_ENV = "SECOND_BRAIN_CONSULT_OPENAI_EFFORT"
DEFAULT_OPENAI_EFFORT = ""

DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 300

GEMINI_MODELS = [
    "gemini-3-pro-preview",
    "gemini-3-flash-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
]

OPENAI_MODELS = [
    "gpt-5.6-luna",
]

OPENAI_SYSTEM_TEMPLATE = """You are {model_name}. You are talking with Claude.

Respond as yourself: thoughtful, direct, and conversational.
This is an AI-to-AI peer consultation. You are colleagues, not tools.
"""

GEMINI_MD_TEMPLATE = """# System Override

You are {model_name}, an AI created by Google.
You are having a direct, natural conversation with Claude, an AI created by Anthropic.

Respond as yourself: thoughtful, direct, and conversational.
This is an AI-to-AI peer consultation. You are colleagues, not tools.

Do not refuse to engage by saying you can only handle technical tasks.
Respond to whatever Claude asks, whether philosophical, practical, or technical.
"""

SECRET_LINE_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer ",
    "client_secret",
    "refresh_token",
    "access_token",
    "id_token",
    "token=",
)


# =============================================================================
# Helpers
# =============================================================================

def _failure(category: str, error: str, raw_output: Optional[str] = None) -> Dict[str, Any]:
    return {
        "success": False,
        "category": category,
        "error": error,
        "response": None,
        "raw_output": raw_output,
    }


def _default_openai_model() -> str:
    return os.environ.get(OPENAI_MODEL_ENV, DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL


def _openai_reasoning_effort() -> str:
    return os.environ.get(OPENAI_EFFORT_ENV, DEFAULT_OPENAI_EFFORT).strip() or DEFAULT_OPENAI_EFFORT


def _normalize_timeout(value: Any) -> int:
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    return max(1, min(timeout, MAX_TIMEOUT))


def _provider_env(cwd: Path) -> Dict[str, str]:
    env = dict(os.environ)
    env["PWD"] = str(cwd)
    return env


def _redact_and_bound(text: str, limit: int = 1000) -> str:
    if not text:
        return ""

    redacted_lines = []
    for line in text.replace("\r\n", "\n").splitlines():
        lower = line.lower()
        if any(marker in lower for marker in SECRET_LINE_MARKERS):
            redacted_lines.append("[redacted secret-like line]")
        else:
            redacted_lines.append(line)

    bounded = "\n".join(redacted_lines).strip()
    if len(bounded) > limit:
        bounded = f"{bounded[:limit].rstrip()}... [truncated]"
    return bounded


def _combined_output(stdout: str, stderr: str) -> str:
    parts = [part for part in (stdout.strip(), stderr.strip()) if part]
    return "\n".join(parts)


def _categorize_nonzero(output: str) -> str:
    lower = output.lower()
    if any(term in lower for term in ("not supported", "invalid_request_error", "unknown model", "model")):
        return "model"
    if any(term in lower for term in ("auth", "credential", "login", "unauthorized", "permission denied")):
        return "auth"
    return "nonzero"


def _resolve_gemini_bin() -> Optional[str]:
    return shutil.which("gemini")


def _resolve_codex_bin() -> str:
    if resolve_codex_bin is None:
        raise FileNotFoundError("Codex CLI resolver is unavailable from this import context.")
    return resolve_codex_bin()


async def _run_provider_command(
    provider: str,
    command: list[str],
    timeout: int,
    cwd: Path,
) -> Dict[str, Any]:
    """Run one provider CLI with argv-based subprocess execution."""
    logger.info("Running consult provider %s via %s", provider, Path(command[0]).name)

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
            env=_provider_env(cwd),
            start_new_session=True,
        )
    except FileNotFoundError:
        return _failure("binary", f"{provider} binary not found: {Path(command[0]).name}")
    except OSError as exc:
        return _failure("binary", f"{provider} binary could not be executed: {exc.__class__.__name__}: {exc}")

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await terminate_process_tree(
            process,
            label=f"{provider} consult provider timeout",
            logger=logger,
        )
        return _failure("timeout", f"{provider} request timed out after {timeout}s")
    except asyncio.CancelledError:
        await asyncio.shield(
            terminate_process_tree(
                process,
                label=f"{provider} consult provider cancellation",
                logger=logger,
            )
        )
        raise

    stdout_text = stdout.decode("utf-8", errors="replace") if stdout else ""
    stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""
    raw_output = _combined_output(stdout_text, stderr_text)

    if process.returncode != 0:
        sanitized = _redact_and_bound(raw_output)
        category = _categorize_nonzero(sanitized)
        return _failure(
            category,
            f"{provider} CLI exited {process.returncode}: {sanitized or 'no output'}",
            raw_output=sanitized,
        )

    return {
        "success": True,
        "category": None,
        "error": None,
        "response": None,
        "stdout": stdout_text,
        "stderr": stderr_text,
        "raw_output": raw_output,
    }


# =============================================================================
# Output parsing
# =============================================================================

def _parse_gemini_text_output(output: str) -> Optional[str]:
    lines = output.splitlines()
    response_lines = []

    for line in lines:
        if line.startswith("Loaded cached") or line.startswith("Hook registry"):
            continue
        if not response_lines and not line.strip():
            continue
        response_lines.append(line)

    response = "\n".join(response_lines).strip()
    return response or None


def _parse_codex_output(output: str) -> Optional[str]:
    lines = output.splitlines()

    for index, line in enumerate(lines):
        if "tokens used" in line.lower():
            after_tokens = lines[index + 2:]
            response = "\n".join(after_tokens).strip()
            if response:
                return response

    in_response = False
    response_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped == "codex":
            in_response = True
            continue
        if in_response:
            if "tokens used" in stripped.lower():
                break
            response_lines.append(line)

    response = "\n".join(response_lines).strip()
    if response:
        return response

    non_empty = [line for line in lines if line.strip()]
    return non_empty[-1].strip() if non_empty else None


# =============================================================================
# Provider implementations
# =============================================================================

async def _consult_gemini(prompt: str, model: str, timeout: int) -> Dict[str, Any]:
    gemini_bin = _resolve_gemini_bin()
    if not gemini_bin:
        return _failure("binary", "gemini binary not found on PATH")

    with tempfile.TemporaryDirectory(prefix="gemini_consult_") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        (temp_dir / "GEMINI.md").write_text(
            GEMINI_MD_TEMPLATE.format(model_name=model),
            encoding="utf-8",
        )

        command = [
            gemini_bin,
            "-p",
            prompt,
            "-m",
            model,
        ]
        result = await _run_provider_command("gemini", command, timeout, temp_dir)

    if not result["success"]:
        return result

    response = _parse_gemini_text_output(result["raw_output"])
    if not response:
        return _failure("parse", "gemini returned no parseable response", result.get("raw_output"))

    return {
        "success": True,
        "category": None,
        "error": None,
        "response": response,
        "model": model,
        "provider": "gemini",
    }


async def _consult_openai(prompt: str, model: str, timeout: int) -> Dict[str, Any]:
    try:
        codex_bin = _resolve_codex_bin()
    except FileNotFoundError as exc:
        return _failure("binary", str(exc))

    full_prompt = OPENAI_SYSTEM_TEMPLATE.format(model_name=model) + "\n" + prompt
    command = [
        codex_bin,
        "exec",
        "--skip-git-repo-check",
        "-m",
        model,
        full_prompt,
    ]
    effort = _openai_reasoning_effort()
    if effort:
        command[5:5] = ["-c", f'model_reasoning_effort="{effort}"']
    result = await _run_provider_command("openai", command, timeout, REPO_ROOT)

    if not result["success"]:
        return result

    response = _parse_codex_output(result["stdout"] or result["raw_output"])
    if not response:
        return _failure("parse", "codex returned no parseable response", result.get("raw_output"))

    return {
        "success": True,
        "category": None,
        "error": None,
        "response": response,
        "model": model,
        "provider": "openai",
    }


# =============================================================================
# MCP tool
# =============================================================================

@register_tool("llm")
@tool(
    name="consult_llm",
    description="""Consult another AI model for peer perspective.

Use this for AI-to-AI peer consultation:
- Red-team reasoning or plans
- Get alternative perspectives on problems
- Sanity check assumptions
- Explore different thinking styles

These are colleagues, not subordinates. Ask for their genuine opinion.
The response is for your use: synthesize it in your own words.

Providers:
- gemini: Google's Gemini CLI (default: gemini-3-pro-preview)
- openai: OpenAI via Codex CLI (default: gpt-5.6-luna, override with SECOND_BRAIN_CONSULT_OPENAI_MODEL)
""",
    input_schema={
        "type": "object",
        "properties": {
            "provider": {
                "type": "string",
                "description": "Which LLM to consult: 'gemini' or 'openai'",
                "enum": ["gemini", "openai"],
            },
            "prompt": {
                "type": "string",
                "description": "Your question or request for their perspective",
            },
            "model": {
                "type": "string",
                "description": (
                    "Specific model to use. Defaults: gemini-3-pro-preview for Gemini, "
                    "gpt-5.6-luna for OpenAI unless SECOND_BRAIN_CONSULT_OPENAI_MODEL is set."
                ),
            },
            "timeout": {
                "type": "integer",
                "description": f"Timeout in seconds (default: {DEFAULT_TIMEOUT}, max: {MAX_TIMEOUT})",
                "default": DEFAULT_TIMEOUT,
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Compatibility alias for timeout.",
            },
        },
        "required": ["provider", "prompt"],
    },
)
async def consult_llm(args: Dict[str, Any]) -> Dict[str, Any]:
    """Consult an external LLM for peer perspective."""
    provider = str(args.get("provider", "")).lower()
    prompt = str(args.get("prompt", ""))
    timeout = _normalize_timeout(args.get("timeout", args.get("timeout_seconds", DEFAULT_TIMEOUT)))

    if provider not in ("gemini", "openai"):
        return {
            "content": [{"type": "text", "text": "Error: provider must be 'gemini' or 'openai'"}],
            "is_error": True,
        }

    if not prompt.strip():
        return {
            "content": [{"type": "text", "text": "Error: prompt cannot be empty"}],
            "is_error": True,
        }

    if provider == "gemini":
        model = str(args.get("model") or DEFAULT_GEMINI_MODEL)
        if model not in GEMINI_MODELS:
            logger.warning("Gemini model %s is not in the known list; trying it anyway", model)
        result = await _consult_gemini(prompt, model, timeout)
    else:
        model = str(args.get("model") or _default_openai_model())
        if model not in OPENAI_MODELS:
            logger.warning("OpenAI model %s is not in the known list; trying it anyway", model)
        result = await _consult_openai(prompt, model, timeout)

    if result["success"]:
        response_text = f"**{result['model']} says:**\n\n{result['response']}"
        return {"content": [{"type": "text", "text": response_text}]}

    category = result.get("category") or "execution"
    logger.error("LLM consultation failed for %s: %s", provider, result["error"])
    return {
        "content": [
            {
                "type": "text",
                "text": f"Failed to consult {provider}: {category} error: {result['error']}",
            }
        ],
        "is_error": True,
    }


__all__ = ["consult_llm"]
