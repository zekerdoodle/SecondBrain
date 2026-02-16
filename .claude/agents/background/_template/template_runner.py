"""
{Agent Name} Background Agent Runner

CUSTOMIZE: Replace {Agent Name} with your agent's name.

This is the Python runner that:
1. Prepares input for the background agent
2. Invokes the agent via Claude Agent SDK
3. Processes the structured JSON output
4. Applies side effects (file writes, state updates, etc.)

The agent itself has NO tools — all side effects happen HERE.
"""

import json
import logging
import asyncio
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime

# CUSTOMIZE: Replace with your agent's name
AGENT_NAME = "{agent_name}"

logger = logging.getLogger(f"background.{AGENT_NAME}")

# Paths relative to this file
_PROMPT_PATH = Path(__file__).parent / "prompt.md"
_CONFIG_PATH = Path(__file__).parent / "config.yaml"


def _load_config() -> Dict[str, Any]:
    """Load agent config from sibling config.yaml."""
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}

_CONFIG = _load_config()


def _get_system_prompt() -> str:
    """Load the agent system prompt from file."""
    if _PROMPT_PATH.exists():
        return _PROMPT_PATH.read_text()
    else:
        raise FileNotFoundError(f"Agent prompt not found at {_PROMPT_PATH}")


# CUSTOMIZE: Define your output schema
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "result": {
            "type": "string",
            "description": "The agent's output"
        }
        # CUSTOMIZE: Add your schema properties here
    },
    "required": ["result"]
}


def _build_input(data: Any) -> str:
    """
    CUSTOMIZE: Build the input prompt for the agent.

    Transform whatever data you have into a string prompt
    that the agent will process and return structured output for.
    """
    # Example: format data as a structured prompt
    return f"""Process the following data:

{json.dumps(data, indent=2)}

Return your analysis as structured JSON."""


async def run(data: Any) -> Optional[Dict]:
    """
    Main entry point. Called by the MCP tool or scheduler.

    Args:
        data: Whatever input your agent needs to process

    Returns:
        Parsed JSON output from the agent, or None on failure
    """
    try:
        from claude_agent_sdk import query, ClaudeAgentOptions

        system_prompt = _get_system_prompt()
        user_prompt = _build_input(data)

        model = _CONFIG.get("model", "sonnet")
        max_turns = _CONFIG.get("max_turns", 200)

        options = ClaudeAgentOptions(
            model=model,
            system_prompt=system_prompt,
            max_turns=max_turns,
            output_format={
                "type": "json_schema",
                "json_schema": OUTPUT_SCHEMA
            }
        )

        logger.info(f"Invoking {AGENT_NAME} agent with {len(user_prompt)} char prompt")
        result = await query(prompt=user_prompt, options=options)

        # Parse the structured output
        if result and result.response:
            try:
                parsed = json.loads(result.response)
                logger.info(f"{AGENT_NAME} returned valid JSON output")
                return parsed
            except json.JSONDecodeError:
                logger.error(f"{AGENT_NAME} returned invalid JSON: {result.response[:200]}")
                return None
        else:
            logger.warning(f"{AGENT_NAME} returned empty response")
            return None

    except Exception as e:
        logger.error(f"{AGENT_NAME} failed: {e}")
        return None


def apply_side_effects(output: Dict) -> str:
    """
    CUSTOMIZE: Apply side effects from the agent's output.

    This is where you write files, update state, send notifications, etc.
    The agent itself has no tools — all side effects happen here.

    Args:
        output: Parsed JSON from the agent

    Returns:
        Human-readable summary of what was done
    """
    # Example implementation:
    summary_parts = []

    # CUSTOMIZE: Process the output and apply side effects
    # e.g., write files, update databases, trigger other agents

    result = output.get("result", "No result")
    summary_parts.append(f"Processed: {result}")

    return "\n".join(summary_parts) if summary_parts else "No actions taken"


async def run_full_cycle(data: Any) -> str:
    """
    Run the agent and apply side effects.

    This is the typical entry point for scheduler/MCP tool integration.

    Returns:
        Human-readable summary of what happened
    """
    output = await run(data)
    if output is None:
        return f"{AGENT_NAME}: Agent returned no output"

    summary = apply_side_effects(output)
    return f"{AGENT_NAME}: {summary}"
