"""Skills tools - fetch skill instructions on demand."""

# Import to trigger registration
from . import fetch

# Re-export for direct access
from .fetch import fetch_skill

__all__ = [
    "fetch_skill",
]
