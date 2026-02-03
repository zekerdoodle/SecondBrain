"""
Agents v2 - Independent schedulable agents for Second Brain.

This module provides:
- AgentRegistry: Load and manage agent configurations
- invoke_agent: Execute agents in different modes (foreground, ping, trust, scheduled)
- NotificationQueue: Manage ping mode notifications

Usage:
    from agents import get_registry, invoke_agent

    registry = get_registry()
    result = await invoke_agent("information_gatherer", "Research Python", mode="foreground")
"""

# Re-export from submodules for package-level access
from .registry import get_registry, reset_registry
from .runner import invoke_agent
from .agent_notifications import NotificationQueue, get_notification_queue

__all__ = [
    "get_registry",
    "reset_registry",
    "invoke_agent",
    "NotificationQueue",
    "get_notification_queue",
]
