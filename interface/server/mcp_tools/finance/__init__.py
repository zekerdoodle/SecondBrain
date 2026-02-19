"""Finance tools (Plaid integration)."""

# Import to trigger registration
from . import tools

# Re-export for direct access
from .tools import (
    finance_accounts,
    finance_transactions,
    finance_spending_analysis,
    finance_connect,
    finance_disconnect,
    finance_status,
)

__all__ = [
    "finance_accounts",
    "finance_transactions",
    "finance_spending_analysis",
    "finance_connect",
    "finance_disconnect",
    "finance_status",
]
