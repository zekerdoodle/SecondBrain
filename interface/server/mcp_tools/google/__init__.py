"""Google Tasks and Calendar tools."""

# Import to trigger registration
from . import tasks
from . import auth

# Re-export for direct access
from .tasks import (
    google_create, google_list, google_delete_task, google_update_task,
    google_get_event, google_update_event, google_delete_event,
)
from .auth import google_auth

__all__ = [
    "google_create",
    "google_list",
    "google_delete_task",
    "google_update_task",
    "google_get_event",
    "google_update_event",
    "google_delete_event",
    "google_auth",
]
