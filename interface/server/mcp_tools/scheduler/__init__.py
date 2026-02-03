"""Scheduler tools."""

# Import to trigger registration
from . import scheduler as sched_module

# Re-export for direct access
from .scheduler import (
    schedule_self,
    scheduler_list,
    scheduler_update,
    scheduler_remove,
)

__all__ = [
    "schedule_self",
    "scheduler_list",
    "scheduler_update",
    "scheduler_remove",
]
