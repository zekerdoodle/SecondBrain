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
from typing import Any, Dict, List, Literal, Optional


class InvocationMode(str, Enum):
    """How an agent is invoked."""
    FOREGROUND = "foreground"  # Blocking, wait for result
    PING = "ping"              # Async with notification when done
    TRUST = "trust"            # Fire and forget, logged
    SCHEDULED = "scheduled"    # Triggered by scheduler, like trust but semantic difference


class AgentType(str, Enum):
    """Type of agent implementation."""
    SDK = "sdk"        # Uses Claude Agent SDK (query())
    CLI = "cli"        # Uses Claude CLI (claude --print)
    PRIMARY = "primary"  # Configuration-only, not invocable


@dataclass
class AgentConfig:
    """
    Configuration for an agent, loaded from config.yaml.

    Attributes:
        name: Unique identifier for the agent
        type: Implementation type (sdk or cli)
        model: Model to use (sonnet, opus, haiku)
        description: Human-readable description for Claude <3
        tools: List of allowed tools (for SDK agents)
        timeout_seconds: Maximum execution time
        output_format: Structured output schema (for SDK agents)
        prompt: System prompt content (loaded from prompt.md)
        prompt_appendage: Appendage to default prompt (for CLI agents like claude_code)
    """
    name: str
    type: AgentType
    model: str
    description: str
    tools: List[str] = field(default_factory=list)
    timeout_seconds: int = 300
    output_format: Optional[Dict[str, Any]] = None
    prompt: Optional[str] = None
    prompt_appendage: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any], prompt: Optional[str] = None, prompt_appendage: Optional[str] = None) -> "AgentConfig":
        """Create AgentConfig from a dictionary (parsed YAML)."""
        return cls(
            name=data["name"],
            type=AgentType(data.get("type", "sdk")),
            model=data.get("model", "sonnet"),
            description=data.get("description", f"Agent: {data['name']}"),
            tools=data.get("tools", []),
            timeout_seconds=data.get("timeout_seconds", 300 if data.get("type") != "cli" else 900),
            output_format=data.get("output_format"),
            prompt=prompt,
            prompt_appendage=prompt_appendage,
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
    """
    agent: str
    prompt: str
    mode: InvocationMode = InvocationMode.FOREGROUND
    source_chat_id: Optional[str] = None
    model_override: Optional[str] = None
    invoked_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "agent": self.agent,
            "prompt": self.prompt,
            "mode": self.mode.value,
            "source_chat_id": self.source_chat_id,
            "model_override": self.model_override,
            "invoked_at": self.invoked_at.isoformat(),
        }


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

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "agent": self.agent,
            "status": self.status,
            "response": self.response,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "error": self.error,
        }

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

    def is_stale(self, threshold_minutes: int = 15) -> bool:
        """Check if notification is stale (completed > threshold ago and still pending)."""
        if self.status != "pending":
            return False
        from datetime import timedelta
        age = datetime.utcnow() - self.completed_at
        return age > timedelta(minutes=threshold_minutes)
