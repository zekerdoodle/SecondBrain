"""SnapTrade tools (investment brokerage integration)."""

# Import to trigger registration
from . import tools

# Re-export for direct access
from .tools import (
    snaptrade_register,
    snaptrade_connect,
    snaptrade_accounts,
    snaptrade_holdings,
    snaptrade_orders,
    snaptrade_activities,
    snaptrade_performance,
    snaptrade_connections,
    snaptrade_search_symbol,
    snaptrade_preview_order,
    snaptrade_execute_order,
    snaptrade_status,
)
