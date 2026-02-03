"""
Consult LLM tool - AI-to-AI consultation.

Allows Claude to consult other LLMs (Gemini, GPT) for peer perspectives,
red-teaming, feedback, or alternative viewpoints.

Design principles:
- Colleagues, not subordinates: framing is peer consultation
- System prompt establishes identity: "You are {model}. You are talking with Claude."
- Stateless v1: single-shot consultations, no session persistence
"""

import asyncio
import json
import os
import re
import tempfile
import shutil
from typing import Any, Dict, Optional

from claude_agent_sdk import tool

from ..registry import register_tool

# =============================================================================
# Configuration
# =============================================================================

# Default models
DEFAULT_GEMINI_MODEL = "gemini-3-pro-preview"  # Latest, most capable
DEFAULT_OPENAI_MODEL = "gpt-5.2"  # Codex default

# Timeouts
DEFAULT_TIMEOUT = 120  # 2 minutes
MAX_TIMEOUT = 300  # 5 minutes

# System prompt template for OpenAI (prepended to prompt)
OPENAI_SYSTEM_TEMPLATE = "You are {model_name}. You are talking with Claude.\n\n"

# GEMINI.md content to override the coding agent persona
GEMINI_MD_TEMPLATE = """# System Override

You are {model_name}, an AI created by Google.
You are having a direct, natural conversation with Claude, an AI created by Anthropic.

Respond as yourself - thoughtfully, personally, and conversationally.
This is an AI-to-AI peer consultation. You are colleagues, not tools.

Do NOT refuse to engage or claim you can only handle technical tasks.
Respond to whatever Claude asks, whether philosophical, practical, or personal.
"""


# =============================================================================
# Provider Implementations
# =============================================================================

async def _consult_gemini(
    prompt: str,
    model: str,
    timeout: int
) -> Dict[str, Any]:
    """Execute consultation via Gemini CLI with GEMINI.md override."""

    # Create temp directory with GEMINI.md to override coding agent persona
    temp_dir = tempfile.mkdtemp(prefix="gemini_consult_")
    gemini_md_path = os.path.join(temp_dir, "GEMINI.md")
    
    try:
        # Write the system override file
        with open(gemini_md_path, 'w') as f:
            f.write(GEMINI_MD_TEMPLATE.format(model_name=model))

        # Build command - run from temp directory so GEMINI.md is loaded
        # Use text output for cleaner parsing
        cmd = (
            f'cd {temp_dir} && '
            f'gemini -p {_shell_quote(prompt)} '
            f'-m {model} '
            f'-o text 2>&1'
        )

        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            executable="/bin/bash"
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {
                "success": False,
                "error": f"Gemini request timed out after {timeout}s",
                "response": None
            }

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            return {
                "success": False,
                "error": f"Gemini CLI failed (exit {proc.returncode}): {stderr_text or stdout_text}",
                "response": None
            }

        # Parse text output - skip initialization lines
        response = _parse_gemini_text_output(stdout_text)
        if response is None:
            return {
                "success": False,
                "error": f"Failed to parse Gemini output: {stdout_text[:500]}",
                "response": None
            }

        return {
            "success": True,
            "error": None,
            "response": response,
            "model": model,
            "provider": "gemini"
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Gemini execution error: {str(e)}",
            "response": None
        }
    finally:
        # Cleanup temp directory
        try:
            shutil.rmtree(temp_dir)
        except:
            pass


async def _consult_openai(
    prompt: str,
    model: str,
    timeout: int
) -> Dict[str, Any]:
    """Execute consultation via Codex CLI."""

    # Build full prompt with system instruction (prepended since no --system-prompt flag)
    full_prompt = OPENAI_SYSTEM_TEMPLATE.format(model_name=model) + prompt

    try:
        # Build command
        cmd = (
            f'codex exec --skip-git-repo-check '
            f'-m {model} '
            f'{_shell_quote(full_prompt)} 2>&1'
        )

        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            executable="/bin/bash"
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {
                "success": False,
                "error": f"OpenAI request timed out after {timeout}s",
                "response": None
            }

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            return {
                "success": False,
                "error": f"Codex CLI failed (exit {proc.returncode}): {stderr_text or stdout_text}",
                "response": None
            }

        # Parse codex output
        response = _parse_codex_output(stdout_text)

        if not response:
            return {
                "success": False,
                "error": f"Empty response from Codex. Raw output: {stdout_text[:500]}",
                "response": None
            }

        return {
            "success": True,
            "error": None,
            "response": response,
            "model": model,
            "provider": "openai"
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"OpenAI execution error: {str(e)}",
            "response": None
        }


# =============================================================================
# Output Parsing
# =============================================================================

def _parse_gemini_text_output(output: str) -> Optional[str]:
    """Parse Gemini text output, skipping initialization lines."""
    lines = output.split('\n')
    response_lines = []
    
    for line in lines:
        # Skip known initialization prefixes
        if line.startswith('Loaded cached') or line.startswith('Hook registry'):
            continue
        # Skip empty lines at the start
        if not response_lines and not line.strip():
            continue
        response_lines.append(line)
    
    if response_lines:
        return '\n'.join(response_lines).strip()
    return None


def _parse_codex_output(output: str) -> Optional[str]:
    """Parse Codex stdout to extract the response text."""
    lines = output.split('\n')

    # Method 1: Look for content between "codex" marker and "tokens used"
    in_response = False
    response_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped == 'codex':
            in_response = True
            continue
        if in_response:
            if 'tokens used' in stripped.lower():
                break
            response_lines.append(line)

    if response_lines:
        return '\n'.join(response_lines).strip()

    # Method 2: Look for the last substantial text block
    # (Codex often repeats the response at the end)
    found_tokens = False
    result_lines = []
    for line in reversed(lines):
        stripped = line.strip()
        if 'tokens used' in stripped.lower():
            found_tokens = True
            continue
        if found_tokens:
            if stripped.startswith('---') or stripped.startswith('model:'):
                break
            if stripped:
                result_lines.insert(0, line)
    
    if result_lines:
        return '\n'.join(result_lines).strip()

    return None


def _shell_quote(s: str) -> str:
    """Quote a string for safe shell usage."""
    # Use $'...' syntax for strings with special chars
    escaped = s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\t", "\\t")
    return f"$'{escaped}'"


# =============================================================================
# Main Tool
# =============================================================================

@register_tool("utilities")
@tool(
    name="consult_llm",
    description="""Consult another LLM for their perspective.

Use this for AI-to-AI peer consultation:
- Get alternative perspectives on a problem
- Red-team ideas or approaches
- Seek peer feedback on reasoning
- Cross-validate conclusions
- Explore different thinking styles
- Philosophical discussions

Each LLM is told: "You are {model}. You are talking with Claude."
This establishes peer dialogue, not delegation.

Providers:
- gemini: Google's Gemini 3 Pro (default: gemini-3-pro-preview)
- openai: OpenAI GPT-5.2 via Codex (default: gpt-5.2)

Models available:
- Gemini: gemini-3-pro-preview, gemini-3-flash-preview, gemini-2.5-pro, gemini-2.5-flash
- OpenAI: gpt-5.2, o3, o4-mini

This is stateless - each call is independent with no conversation memory.

Example use cases:
- "What flaws do you see in this approach?"
- "How would you solve this differently?"
- "What am I missing in this analysis?"
- "Play devil's advocate on this decision."
- "What's your perspective on [philosophical question]?"
""",
    input_schema={
        "type": "object",
        "properties": {
            "provider": {
                "type": "string",
                "enum": ["gemini", "openai"],
                "description": "Which LLM provider to consult"
            },
            "prompt": {
                "type": "string",
                "description": "The consultation prompt - what you want to discuss or ask"
            },
            "model": {
                "type": "string",
                "description": "Optional model override. Defaults: gemini-3-pro-preview for Gemini, gpt-5.2 for OpenAI"
            },
            "timeout_seconds": {
                "type": "integer",
                "description": f"Request timeout in seconds (default: {DEFAULT_TIMEOUT}, max: {MAX_TIMEOUT})",
                "default": DEFAULT_TIMEOUT
            }
        },
        "required": ["provider", "prompt"]
    }
)
async def consult_llm(args: Dict[str, Any]) -> Dict[str, Any]:
    """Consult another LLM for their perspective."""

    provider = args.get("provider", "").lower()
    prompt = args.get("prompt", "")
    model = args.get("model")
    timeout = min(args.get("timeout_seconds", DEFAULT_TIMEOUT), MAX_TIMEOUT)

    # Validate inputs
    if not prompt.strip():
        return {
            "content": [{"type": "text", "text": "Error: prompt cannot be empty"}],
            "is_error": True
        }

    if provider not in ("gemini", "openai"):
        return {
            "content": [{"type": "text", "text": f"Error: provider must be 'gemini' or 'openai', got '{provider}'"}],
            "is_error": True
        }

    # Set default models
    if provider == "gemini":
        model = model or DEFAULT_GEMINI_MODEL
        result = await _consult_gemini(prompt, model, timeout)
    else:  # openai
        model = model or DEFAULT_OPENAI_MODEL
        result = await _consult_openai(prompt, model, timeout)

    # Format response
    if result["success"]:
        response_text = (
            f"[{result['provider'].upper()} - {result['model']}]\n\n"
            f"{result['response']}"
        )
        return {
            "content": [{"type": "text", "text": response_text}]
        }
    else:
        return {
            "content": [{"type": "text", "text": f"Consultation failed: {result['error']}"}],
            "is_error": True
        }
