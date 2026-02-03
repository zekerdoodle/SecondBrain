"""Utility tools."""

# Import to trigger registration
from . import page_parser as pp_module
from . import restart as restart_module
from . import web_search as ws_module
from . import notification as notif_module
from . import consult_llm as consult_module

# Re-export for direct access
from .page_parser import page_parser
from .restart import restart_server
from .web_search import web_search
from .notification import send_critical_notification
from .consult_llm import consult_llm

__all__ = [
    "page_parser",
    "restart_server",
    "web_search",
    "send_critical_notification",
    "consult_llm",
]
