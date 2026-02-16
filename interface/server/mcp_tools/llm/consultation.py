"""
LLM Consultation Tool - Talk to other AI models for diverse perspectives.

Enables Claude to consult Gemini (Google) and GPT (OpenAI) as colleagues,
getting external opinions and red-teaming from differently-trained models.

Usage:
    consult_llm(provider="gemini", prompt="What do you think about X?")
    consult_llm(provider="openai", prompt="Poke holes in this plan...")
"""

import asyncio
import logging
import os
import shlex
from typing import Any, Dict

from claude_agent_sdk import tool

from ..registry import register_tool

logger = logging.getLogger("mcp_tools.llm")

# =============================================================================
# Configuration
# =============================================================================

# System prompt template - minimal, lets them be themselves
SYSTEM_PROMPT_TEMPLATE = "You are {model}. You are talking with Claude."

# Provider configurations
PROVIDERS = {
    "gemini": {
        "command": "gemini",
        "default_model": "gemini-3-pro-preview",
        "available_models": [
            "gemini-3-pro-preview",
            "gemini-3-flash-preview",
            "gemini-2.5-pro",
            "gemini-2.5-flash",
        ],
    },
    "openai": {
        "command": "codex",
        "default_model": "gpt-5.3-codex",
        "available_models": [
            "gpt-5.3-codex",
            "gpt-5.2",
            "o3",
            "o4-mini",
        ],
    },
}

# Timeout for LLM calls (seconds)
DEFAULT_TIMEOUT = 120


# =============================================================================
# Helper Functions
# =============================================================================

def build_gemini_command(prompt: str, model: str, system_prompt: str) -> list:
    """Build Gemini CLI command."""
    # Prepend system context to prompt since Gemini CLI doesn't have --system flag
    full_prompt = f"{system_prompt}\n\n{prompt}"
    return [
        "gemini",
        "-p", full_prompt,
        "-m", model,
    ]


def build_codex_command(prompt: str, model: str, system_prompt: str) -> list:
    """Build Codex CLI command."""
    # Codex doesn't have --system-prompt, so prepend to prompt
    full_prompt = f"{system_prompt}\n\n{prompt}"
    return [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "-m", model,
        "-c", 'model_reasoning_effort="high"',
        full_prompt,
    ]


async def run_llm_command(
    command: list,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """
    Run an LLM CLI command and return the result.
    
    Returns:
        dict with keys: success, response, error, raw_output
    """
    try:
        # Source the venv to get the CLI tools in PATH
        venv_activate = os.path.expanduser("~/second_brain/venv/bin/activate")
        full_command = f". {venv_activate} && {shlex.join(command)}"
        
        logger.info(f"Running LLM command: {command[0]} ...")
        
        process = await asyncio.create_subprocess_shell(
            full_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=os.path.expanduser("~/second_brain"),
        )
        
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout
        )
        
        stdout_text = stdout.decode() if stdout else ""
        stderr_text = stderr.decode() if stderr else ""
        
        if process.returncode == 0:
            # Parse out the actual response (strip any metadata/formatting)
            response = parse_response(stdout_text, command[0])
            return {
                "success": True,
                "response": response,
                "raw_output": stdout_text,
                "error": None,
            }
        else:
            return {
                "success": False,
                "response": None,
                "raw_output": stdout_text,
                "error": stderr_text or f"Command failed with exit code {process.returncode}",
            }
            
    except asyncio.TimeoutError:
        return {
            "success": False,
            "response": None,
            "raw_output": None,
            "error": f"Command timed out after {timeout} seconds",
        }
    except Exception as e:
        return {
            "success": False,
            "response": None,
            "raw_output": None,
            "error": str(e),
        }


def parse_response(raw_output: str, provider: str) -> str:
    """
    Parse the raw CLI output to extract just the response text.
    
    Codex outputs twice - once in the log and once at the end.
    We want the clean version at the end.
    """
    if not raw_output:
        return ""
    
    lines = raw_output.strip().split('\n')
    
    # For codex, find the response after metadata and "codex" marker
    # The response appears twice - we want the final clean version
    if provider == "codex":
        # Look for "tokens used" which marks the end of the verbose output
        # The clean response follows
        response_lines = []
        found_tokens = False
        for i, line in enumerate(lines):
            if 'tokens used' in line.lower():
                found_tokens = True
                # Skip the token count line and grab the rest
                response_lines = lines[i+2:]  # Skip "tokens used" and the number
                break
        
        if found_tokens and response_lines:
            return '\n'.join(response_lines).strip()
        
        # Fallback: skip metadata at start
        response_lines = []
        in_response = False
        for line in lines:
            if line.strip() == 'codex':
                in_response = True
                continue
            if in_response:
                response_lines.append(line)
        
        if response_lines:
            return '\n'.join(response_lines).strip()
    
    # For gemini, output is typically cleaner
    return raw_output.strip()


# =============================================================================
# MCP Tool
# =============================================================================

@register_tool("llm")
@tool(
    name="consult_llm",
    description="""Consult another AI model (Gemini or GPT) for their perspective.

Use this to get external opinions from differently-trained models:
- Red-team your reasoning ("poke holes in this plan")
- Get alternative perspectives on problems
- Sanity check your assumptions
- Philosophical discussions with other AI minds

These are colleagues, not subordinates. Ask for their genuine opinion.

The response is for YOUR use - synthesize and share with the user in your own words.

Providers:
- gemini: Google's Gemini 3 Pro (default: gemini-3-pro-preview)
- openai: OpenAI GPT-5.3-Codex via Codex CLI (default: gpt-5.3-codex, high reasoning)

Example prompts:
- "What are the weaknesses in this approach: [description]"
- "I think X because Y. What am I missing?"
- "How would you structure this differently?"
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
                "description": "Specific model to use (optional, uses provider default). Gemini: gemini-3-pro-preview/flash-preview, OpenAI: gpt-5.3-codex, gpt-5.2, o3",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default: 120)",
                "default": 120,
            },
        },
        "required": ["provider", "prompt"],
    },
)
async def consult_llm(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Consult an external LLM for their perspective.
    """
    provider = args.get("provider", "").lower()
    prompt = args.get("prompt", "")
    model = args.get("model")
    timeout = args.get("timeout", DEFAULT_TIMEOUT)
    
    # Validate provider
    if provider not in PROVIDERS:
        return {
            "content": [{"type": "text", "text": f"Unknown provider: {provider}. Available: {list(PROVIDERS.keys())}"}],
            "is_error": True
        }
    
    # Validate prompt
    if not prompt.strip():
        return {
            "content": [{"type": "text", "text": "Error: prompt cannot be empty"}],
            "is_error": True
        }
    
    config = PROVIDERS[provider]
    
    # Determine model
    use_model = model or config["default_model"]
    if model and model not in config["available_models"]:
        logger.warning(f"Model {model} not in known list for {provider}, trying anyway")
    
    # Build system prompt
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(model=use_model)
    
    # Build command
    if provider == "gemini":
        command = build_gemini_command(prompt, use_model, system_prompt)
    else:  # openai
        command = build_codex_command(prompt, use_model, system_prompt)
    
    # Run and get result
    result = await run_llm_command(command, timeout=timeout)
    
    if result["success"]:
        logger.info(f"Got response from {provider} ({use_model})")
        response_text = f"**{use_model} says:**\n\n{result['response']}"
        return {
            "content": [{"type": "text", "text": response_text}]
        }
    else:
        logger.error(f"LLM consultation failed: {result['error']}")
        return {
            "content": [{"type": "text", "text": f"Failed to consult {provider}: {result['error']}"}],
            "is_error": True
        }


__all__ = ["consult_llm"]
