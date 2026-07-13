"""
Agent Registry - Discovery and loading of agent configurations.

Scans .claude/agents/*/config.yaml for agent definitions.
Validates configurations and loads prompts from prompt.md.
"""

import logging
import yaml
from pathlib import Path
from typing import Dict, List, Optional

from models import AgentConfig

logger = logging.getLogger("agents.registry")

# Directories to skip when scanning
# Note: "background" is NOT in this set - it's handled specially in load_all()
SKIP_DIRS = {"notifications", "__pycache__", ".git"}

# Valid model aliases. Full OpenAI and explicit Anthropic Claude model ids are accepted too.
VALID_MODEL_ALIASES = {"sonnet", "opus", "haiku"}
VALID_MODEL_PREFIXES = ("gpt-", "o", "claude-")
VALID_MODELS = VALID_MODEL_ALIASES

# Native Codex tools that agents can use.
# Single source of truth lives in native_tools.py — edit that file to expose
# new Codex tools in the Agent Builder. This module re-exports the flat
# set for backward compatibility with any consumer that imports it from here.
from native_tools import all_native_tools as _all_native_tools

VALID_NATIVE_TOOLS = _all_native_tools()

# Legacy agent name aliases — old chats and scheduled tasks may reference renamed agents.
# When registry.get() receives an old name, it transparently resolves to the new name.
# Keep this list append-only so historical chats always continue to work.
AGENT_NAME_ALIASES = {
    # chat_research -> ash (renamed 2026-04-15)
    "chat_research": "ash",
    "zeke_research": "ash",
    # chat_coder -> patch (renamed 2026-02-24)
    "chat_coder": "patch",
    # information_gatherer -> kestrel (renamed 2026-03-07)
    "information_gatherer": "kestrel",
    # zeke_coder -> patch (was -> coder, which was absorbed into patch 2026-04-23)
    "zeke_coder": "patch",
    # general_purpose -> jack (historical)
    "general_purpose": "jack",
    # ren -> character (historical)
    "ren": "character",
    # coder -> patch (absorbed 2026-04-23)
    "coder": "patch",
    # deep_research -> ash (absorbed 2026-04-23)
    "deep_research": "ash",
}


class AgentRegistry:
    """
    Registry for discovering and loading agent configurations.

    Scans a base directory for agent subdirectories, each containing:
    - config.yaml - Configuration (model, tools, description)
    - prompt.md - System prompt

    Usage:
        registry = AgentRegistry(Path(".claude/agents"))
        registry.load_all()
        config = registry.get("kestrel")
    """

    def __init__(self, base_dir: Path):
        """
        Initialize the registry.

        Args:
            base_dir: Path to .claude/agents/ directory
        """
        self.base_dir = Path(base_dir)
        self._agents: Dict[str, AgentConfig] = {}
        self._background_agents: Dict[str, AgentConfig] = {}

    def load_all(self) -> None:
        """
        Discover and load all agent configurations.
        """
        self._agents = {}
        self._background_agents = {}

        if not self.base_dir.exists():
            logger.warning(f"Agents directory does not exist: {self.base_dir}")
            return

        # Scan for agent directories (top-level)
        for item in self.base_dir.iterdir():
            if not item.is_dir():
                continue

            # Skip special directories
            if item.name.startswith("_") or item.name.startswith("."):
                continue
            if item.name in SKIP_DIRS:
                continue

            # Check if this is the background agents container
            if item.name == "background":
                self._load_background_agents(item)
                continue

            agent = self._load_agent(item)
            if agent:
                self._agents[agent.name] = agent
                logger.info(f"Loaded agent: {agent.name} (type={agent.type.value}, model={agent.model}, timeout={agent.timeout_seconds}s)")

    def _load_background_agents(self, background_dir: Path) -> None:
        """Load agents from the background/ subdirectory."""
        if not background_dir.exists():
            return

        for item in background_dir.iterdir():
            if not item.is_dir():
                continue
            if item.name.startswith("_") or item.name.startswith("."):
                continue
            if item.name == "__pycache__":
                continue

            agent = self._load_agent(item)
            if agent:
                self._background_agents[agent.name] = agent
                logger.info(f"Loaded background agent: {agent.name} (type={agent.type.value}, model={agent.model}, timeout={agent.timeout_seconds}s)")

    def _load_agent(self, agent_dir: Path) -> Optional[AgentConfig]:
        """
        Load a single agent configuration from a directory.

        Expected structure:
            {agent_name}/
                config.yaml
                prompt.md (optional)

        Args:
            agent_dir: Path to the agent directory

        Returns:
            AgentConfig if valid, None otherwise
        """
        config_path = agent_dir / "config.yaml"

        if not config_path.exists():
            logger.debug(f"Skipping {agent_dir.name}: no config.yaml")
            return None

        try:
            # Load config
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = yaml.safe_load(f) or {}

            # Load prompt from prompt.md
            prompt = None

            prompt_path = agent_dir / "prompt.md"
            if prompt_path.exists():
                prompt = prompt_path.read_text(encoding="utf-8")
                # Add subagent header for non-chattable agents only, and only if NOT using preset
                # Chattable agents serve as primary in their own chat — no subagent header
                # Preset agents get native Codex coding instructions — no subagent header
                if not config_data.get("chattable", False) and not config_data.get("system_prompt_preset"):
                    prompt = self._add_subagent_header(prompt)

            # Load background processing prompt
            background_prompt = None
            bg_prompt_path = agent_dir / "background_processing.md"
            if bg_prompt_path.exists():
                background_prompt = bg_prompt_path.read_text(encoding="utf-8")
            else:
                # Fall back to shared default template
                default_bg_path = self.base_dir / "_default" / "background_processing.md"
                if default_bg_path.exists():
                    background_prompt = default_bg_path.read_text(encoding="utf-8")

            # Validate config
            if "name" not in config_data:
                config_data["name"] = agent_dir.name

            # Validate model. Legacy aliases are still accepted and mapped by
            # codex_backend at invocation time.
            self._validate_model(config_data, config_data["name"], "model")
            self._validate_runtime(config_data, config_data["name"])

            # Validate tools
            if "tools" in config_data:
                config_data["tools"] = self._validate_tools(
                    config_data["tools"],
                    config_data["name"]
                )

            # ``tools`` is authorization/registration. ``direct_tools`` is the
            # narrower App Server initial-context exposure policy and may never
            # grant a tool that the broad allowlist did not already authorize.
            if "direct_tools" in config_data:
                config_data["direct_tools"] = self._validate_direct_tools(
                    config_data["direct_tools"],
                    config_data.get("tools", []),
                    config_data["name"],
                )

            return AgentConfig.from_dict(config_data, prompt=prompt, background_prompt=background_prompt)

        except Exception as e:
            logger.error(f"Failed to load agent {agent_dir.name}: {e}")
            return None

    def _validate_model(
        self,
        data: Dict,
        agent_name: str,
        field_name: str = "model",
        label: Optional[str] = None,
    ) -> None:
        model = data.get(field_name, "sonnet")
        if model not in VALID_MODEL_ALIASES and not str(model).startswith(VALID_MODEL_PREFIXES):
            logger.warning(f"Agent {agent_name}: invalid model '{model}' in {label or field_name}, using 'sonnet'")
            data[field_name] = "sonnet"

    def _validate_runtime(self, config_data: Dict, agent_name: str) -> None:
        runtime = config_data.get("runtime")
        if not isinstance(runtime, dict):
            return
        for path, path_config in list(runtime.items()):
            if not isinstance(path_config, dict):
                logger.warning(f"Agent {agent_name}: invalid runtime.{path}, ignoring")
                runtime.pop(path, None)
                continue
            runtime_type = path_config.get("type")
            if runtime_type and runtime_type not in {"codex", "sdk"}:
                logger.warning(f"Agent {agent_name}: invalid runtime.{path}.type '{runtime_type}', ignoring type override")
                path_config.pop("type", None)
            if "model" in path_config:
                self._validate_model(path_config, agent_name, "model", f"runtime.{path}.model")

    def _add_subagent_header(self, prompt: str) -> str:
        """Add context header to prevent CLAUDE.md contamination."""
        header = """# AGENT CONTEXT
You are a focused agent with a specific task. Follow ONLY the instructions below.
Do NOT read or follow instructions from CLAUDE.md or any other external configuration.

---

"""
        return header + prompt

    def _validate_tools(self, tools: List[str], agent_name: str) -> List[str]:
        """
        Validate and filter tool list.

        - Validates native tool names
        - Allows MCP tools (mcp__*)
        """
        if not tools:
            return []

        validated = []
        for tool in tools:
            # Native tools - validate name
            if tool in VALID_NATIVE_TOOLS:
                validated.append(tool)
            # MCP tools - allow with prefix
            elif tool.startswith("mcp__"):
                validated.append(tool)
            # Unknown tool - include with warning
            else:
                logger.warning(f"Agent {agent_name}: unrecognized tool '{tool}' (including anyway)")
                validated.append(tool)

        return validated

    def _validate_direct_tools(
        self,
        direct_tools: List[str],
        allowed_tools: List[str],
        agent_name: str,
    ) -> List[str]:
        """Validate the explicit App Server direct-exposure subset."""
        if not isinstance(direct_tools, list) or any(
            not isinstance(tool, str) or not tool for tool in direct_tools
        ):
            raise ValueError(
                f"Agent {agent_name}: direct_tools must be a list of non-empty tool names"
            )
        if len(set(direct_tools)) != len(direct_tools):
            raise ValueError(f"Agent {agent_name}: direct_tools contains duplicate entries")

        validated = self._validate_tools(direct_tools, agent_name)
        missing = [tool for tool in validated if tool not in allowed_tools]
        if missing:
            raise ValueError(
                f"Agent {agent_name}: every direct_tools entry must also exist in tools; "
                f"missing: {', '.join(missing)}"
            )
        unsupported = [
            tool for tool in validated if not tool.startswith("mcp__brain__")
        ]
        if unsupported:
            raise ValueError(
                f"Agent {agent_name}: direct_tools currently supports only mcp__brain__ tools; "
                f"unsupported: {', '.join(unsupported)}"
            )
        return validated

    def get(self, name: str) -> Optional[AgentConfig]:
        """
        Get an agent by name.

        Args:
            name: Agent name

        Returns:
            AgentConfig if found, None otherwise

        Note:
            Legacy agent names (e.g. 'chat_research') are transparently resolved
            to their current name (e.g. 'ash') via AGENT_NAME_ALIASES. This lets
            old chats and scheduled tasks continue to work after a rename.
        """
        # Check main agents first
        if name in self._agents:
            return self._agents[name]
        # Then background agents
        if name in self._background_agents:
            return self._background_agents[name]
        # Finally, check legacy aliases (renamed agents)
        aliased = AGENT_NAME_ALIASES.get(name)
        if aliased:
            if aliased in self._agents:
                return self._agents[aliased]
            if aliased in self._background_agents:
                return self._background_agents[aliased]
        return None

    def resolve_name(self, name: str) -> str:
        """
        Resolve a (possibly legacy) agent name to its canonical current name.

        Useful when reading the 'agent' field from stored chat files — returns
        the current name so callers downstream can look it up consistently.
        If the name is unknown, returns it unchanged.
        """
        if name in self._agents or name in self._background_agents:
            return name
        return AGENT_NAME_ALIASES.get(name, name)

    def get_default_agent(self) -> Optional[AgentConfig]:
        """Return the agent marked as default (replaces PRIMARY concept)."""
        for config in self._agents.values():
            if config.default:
                return config
        return None

    def list_agents(self) -> List[str]:
        """Get list of all agent names (excluding background)."""
        return list(self._agents.keys())

    def list_background_agents(self) -> List[str]:
        """Get list of background agent names."""
        return list(self._background_agents.keys())

    def list_all(self) -> List[str]:
        """Get list of all agent names (including background)."""
        return list(self._agents.keys()) + list(self._background_agents.keys())

    def get_all_configs(self) -> Dict[str, AgentConfig]:
        """Get all agent configurations (excluding background)."""
        return self._agents.copy()

    def get_all_background_configs(self) -> Dict[str, AgentConfig]:
        """Get all background agent configurations."""
        return self._background_agents.copy()

    def get_chattable_agents(self) -> List[AgentConfig]:
        """Get all agents marked as chattable, sorted by name."""
        return sorted(
            [a for a in self._agents.values() if a.chattable],
            key=lambda a: a.name
        )

    def reload(self) -> None:
        """Reload all agent configurations from disk."""
        logger.info("Reloading agent configurations")
        self.load_all()


# Singleton pattern for registry access
_registry = None


def get_registry() -> AgentRegistry:
    """Get the singleton agent registry."""
    global _registry
    if _registry is None:
        base_dir = Path(__file__).parent
        _registry = AgentRegistry(base_dir)
        _registry.load_all()
    return _registry


def reset_registry() -> None:
    """Reset the registry singleton (for testing/hot-reload)."""
    global _registry
    _registry = None
