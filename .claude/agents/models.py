"""
Core data models for Agents v2.

Defines:
- InvocationMode: How agents are executed
- AgentConfig: Configuration loaded from config.yaml
- AgentInvocation: Request to execute an agent
- AgentResult: Result from agent execution
- PendingNotification: For ping mode queue
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union


class InvocationMode(str, Enum):
    """How an agent is invoked."""
    FOREGROUND = "foreground"  # Blocking, wait for result
    PING = "ping"              # Async with notification when done
    TRUST = "trust"            # Fire and forget, logged
    SCHEDULED = "scheduled"    # Triggered by scheduler, like trust but semantic difference


class AgentType(str, Enum):
    """Type of agent implementation."""
    SDK = "sdk"        # Uses Claude Agent SDK (query())


@dataclass
class AgentConfig:
    """
    Configuration for an agent, loaded from config.yaml.

    Attributes:
        name: Unique identifier for the agent
        type: Implementation type (sdk)
        model: Model to use (sonnet, opus, haiku)
        description: Human-readable description for Claude <3
        tools: List of allowed tools
        timeout_seconds: Maximum execution time
        output_format: Structured output schema
        prompt: System prompt content (loaded from prompt.md)
    """
    name: str
    type: AgentType
    model: str
    description: str
    display_name: Optional[str] = None  # Human-friendly name (e.g., "Patch"). Falls back to title-cased `name`.
    tools: List[str] = field(default_factory=list)
    timeout_seconds: int = 300
    max_turns: int = 200
    output_format: Optional[Dict[str, Any]] = None
    prompt: Optional[str] = None
    system_prompt_preset: Optional[str] = None  # e.g., "claude_code" — uses SDK's SystemPromptPreset; prompt.md becomes append
    skills: Optional[List[str]] = None  # If set, only these skills can be injected. None = all skills available.
    chattable: bool = False
    hidden: bool = False
    color: Optional[str] = None
    icon: Optional[str] = None
    default: bool = False  # The default agent for chat (replaces PRIMARY concept)
    effort: Optional[str] = None  # Override thinking effort: 'low', 'medium', 'high'
    thinking_budget: Optional[int] = None  # Override: explicit budget_tokens for ThinkingConfigEnabled

    @classmethod
    def from_dict(cls, data: Dict[str, Any], prompt: Optional[str] = None) -> "AgentConfig":
        """Create AgentConfig from a dictionary (parsed YAML)."""
        return cls(
            name=data["name"],
            type=AgentType(data.get("type", "sdk")),
            model=data.get("model", "sonnet"),
            description=data.get("description", f"Agent: {data['name']}"),
            display_name=data.get("display_name"),
            tools=data.get("tools", []),
            timeout_seconds=data.get("timeout_seconds") or data.get("timeout") or 300,
            max_turns=data.get("max_turns", 200),
            output_format=data.get("output_format"),
            prompt=prompt,
            system_prompt_preset=data.get("system_prompt_preset"),
            skills=data.get("skills"),
            chattable=data.get("chattable", False),
            hidden=data.get("hidden", False),
            color=data.get("color"),
            icon=data.get("icon"),
            default=data.get("default", False),
            effort=data.get("effort"),
            thinking_budget=data.get("thinking_budget"),
        )


@dataclass
class AgentInvocation:
    """
    Request to invoke an agent.

    Attributes:
        agent: Name of the agent to invoke
        prompt: Task description for the agent
        mode: How to invoke (foreground, ping, trust, scheduled)
        source_chat_id: Chat ID for ping mode notifications
        model_override: Override the agent's default model
        invoked_at: When the invocation was requested
        project: Optional project tag for output routing
    """
    agent: str
    prompt: str
    mode: InvocationMode = InvocationMode.FOREGROUND
    source_chat_id: Optional[str] = None
    model_override: Optional[str] = None
    invoked_at: datetime = field(default_factory=datetime.utcnow)
    project: Optional[Union[str, List[str]]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        d = {
            "agent": self.agent,
            "prompt": self.prompt,
            "mode": self.mode.value,
            "source_chat_id": self.source_chat_id,
            "model_override": self.model_override,
            "invoked_at": self.invoked_at.isoformat(),
        }
        if self.project:
            d["project"] = self.project
        return d


@dataclass
class AgentResult:
    """
    Result from agent execution.

    Attributes:
        agent: Name of the agent that ran
        status: Execution status (success, error, timeout)
        response: Agent's final output
        started_at: When execution started
        completed_at: When execution finished
        error: Error message if status is error
    """
    agent: str
    status: Literal["success", "error", "timeout"]
    response: str
    started_at: datetime
    completed_at: datetime
    error: Optional[str] = None
    transcript: Optional[str] = None
    blocks: Optional[List[Dict[str, Any]]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        d = {
            "agent": self.agent,
            "status": self.status,
            "response": self.response,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "error": self.error,
        }
        if self.transcript:
            d["transcript"] = self.transcript
        # blocks are not serialized to execution log (too large)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentResult":
        """Create from dictionary."""
        return cls(
            agent=data["agent"],
            status=data["status"],
            response=data["response"],
            started_at=datetime.fromisoformat(data["started_at"]),
            completed_at=datetime.fromisoformat(data["completed_at"]),
            error=data.get("error"),
            transcript=data.get("transcript"),
        )


@dataclass
class PendingNotification:
    """
    A notification waiting to be injected (ping mode).

    Attributes:
        id: Unique identifier
        agent: Agent that completed
        invoked_at: When the agent was invoked
        completed_at: When the agent finished
        source_chat_id: Chat to inject notification into
        agent_response: The agent's final response
        status: pending, injected, or expired
    """
    id: str
    agent: str
    invoked_at: datetime
    completed_at: datetime
    source_chat_id: str
    agent_response: str
    status: Literal["pending", "injected", "expired"] = "pending"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "agent": self.agent,
            "invoked_at": self.invoked_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "source_chat_id": self.source_chat_id,
            "agent_response": self.agent_response,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PendingNotification":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            agent=data["agent"],
            invoked_at=datetime.fromisoformat(data["invoked_at"]),
            completed_at=datetime.fromisoformat(data["completed_at"]),
            source_chat_id=data["source_chat_id"],
            agent_response=data["agent_response"],
            status=data.get("status", "pending"),
        )

    def is_stale(self, threshold_minutes: int = 5, threshold_seconds: Optional[int] = None) -> bool:
        """Check if notification is stale (completed > threshold ago and still pending).

        Args:
            threshold_minutes: Minutes threshold (used if threshold_seconds is None)
            threshold_seconds: Seconds threshold (takes precedence over threshold_minutes)
        """
        if self.status != "pending":
            return False
        from datetime import timedelta
        age = datetime.utcnow() - self.completed_at
        if threshold_seconds is not None:
            return age > timedelta(seconds=threshold_seconds)
        return age > timedelta(minutes=threshold_minutes)
