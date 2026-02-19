"""
Finance tools — Plaid integration wrappers.

MCP tool wrappers around the existing Plaid backend in
.claude/scripts/theo_ports/financial_tools.py.

Tools:
- finance_accounts: List connected accounts with balances
- finance_transactions: Fetch transaction history
- finance_spending_analysis: Spending breakdown by category
- finance_connect: Connect a new bank account via Plaid Link
- finance_disconnect: Remove a bank connection
- finance_status: Check Plaid connection status
"""

import os
import sys
from typing import Any, Dict

from claude_agent_sdk import tool

from ..registry import register_tool

# Add scripts directory to path so we can import theo_ports
SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.claude/scripts"))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


def _import_financial_tools():
    """Lazy import the financial tools backend."""
    from theo_ports.financial_tools import (
        get_financial_accounts,
        get_transactions,
        get_spending_analysis,
        connect_bank_account,
        disconnect_bank_account,
        get_connection_status,
    )
    return {
        "accounts": get_financial_accounts,
        "transactions": get_transactions,
        "spending": get_spending_analysis,
        "connect": connect_bank_account,
        "disconnect": disconnect_bank_account,
        "status": get_connection_status,
    }


def _result_to_mcp(message: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Convert (message, metadata) tuple from financial_tools to MCP response format."""
    is_error = not metadata.get("success", True)
    return {
        "content": [{"type": "text", "text": message}],
        **({"is_error": True} if is_error else {})
    }


@register_tool("finance")
@tool(
    name="finance_accounts",
    description="""List all connected financial accounts with current balances.

Returns account names, types, balances, and account IDs. Use this before fetching transactions to know which accounts are available.""",
    input_schema={
        "type": "object",
        "properties": {}
    }
)
async def finance_accounts(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get connected financial accounts with balances."""
    try:
        tools = _import_financial_tools()
        message, metadata = tools["accounts"]()
        return _result_to_mcp(message, metadata)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("finance")
@tool(
    name="finance_transactions",
    description="""Fetch transaction history for connected accounts.

Returns transactions with merchant name, amount, category, and date. Sorted most recent first.""",
    input_schema={
        "type": "object",
        "properties": {
            "start_date": {"type": "string", "description": "Start date in YYYY-MM-DD format (defaults to 30 days ago)"},
            "end_date": {"type": "string", "description": "End date in YYYY-MM-DD format (defaults to today)"},
            "account_id": {"type": "string", "description": "Optional account ID to filter transactions (from finance_accounts)"},
            "limit": {"type": "integer", "description": "Max transactions to return (default 50, max 100)", "default": 50}
        }
    }
)
async def finance_transactions(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get transaction history."""
    try:
        tools = _import_financial_tools()
        message, metadata = tools["transactions"](
            start_date=args.get("start_date"),
            end_date=args.get("end_date"),
            account_id=args.get("account_id"),
            limit=args.get("limit", 50)
        )
        return _result_to_mcp(message, metadata)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("finance")
@tool(
    name="finance_spending_analysis",
    description="""Analyze spending breakdown by category.

Shows total spending, transaction count, and category-by-category breakdown with percentages.""",
    input_schema={
        "type": "object",
        "properties": {
            "start_date": {"type": "string", "description": "Start date in YYYY-MM-DD format (defaults to 30 days ago)"},
            "end_date": {"type": "string", "description": "End date in YYYY-MM-DD format (defaults to today)"}
        }
    }
)
async def finance_spending_analysis(args: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze spending by category."""
    try:
        tools = _import_financial_tools()
        message, metadata = tools["spending"](
            start_date=args.get("start_date"),
            end_date=args.get("end_date")
        )
        return _result_to_mcp(message, metadata)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("finance")
@tool(
    name="finance_connect",
    description="""Connect a bank account via Plaid Link.

Two-step process:
1. Call without public_token to generate a Plaid Link token (user completes bank login)
2. Call with public_token after user finishes to complete the connection""",
    input_schema={
        "type": "object",
        "properties": {
            "public_token": {"type": "string", "description": "Public token from Plaid Link (step 2 only, omit for step 1)"}
        }
    }
)
async def finance_connect(args: Dict[str, Any]) -> Dict[str, Any]:
    """Connect a bank account via Plaid."""
    try:
        tools = _import_financial_tools()
        message, metadata = tools["connect"](
            public_token=args.get("public_token")
        )
        return _result_to_mcp(message, metadata)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("finance")
@tool(
    name="finance_disconnect",
    description="""Disconnect a bank account and revoke Plaid access.

Permanently removes the connection. Get the item_id from finance_status first.""",
    input_schema={
        "type": "object",
        "properties": {
            "item_id": {"type": "string", "description": "The Plaid item ID to disconnect (from finance_status)"}
        },
        "required": ["item_id"]
    }
)
async def finance_disconnect(args: Dict[str, Any]) -> Dict[str, Any]:
    """Disconnect a bank account."""
    try:
        item_id = args.get("item_id")
        if not item_id:
            return {"content": [{"type": "text", "text": "item_id is required"}], "is_error": True}

        tools = _import_financial_tools()
        message, metadata = tools["disconnect"](item_id=item_id)
        return _result_to_mcp(message, metadata)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("finance")
@tool(
    name="finance_status",
    description="""Check Plaid connection status.

Shows whether banks are connected, number of items/accounts, and connection details.""",
    input_schema={
        "type": "object",
        "properties": {}
    }
)
async def finance_status(args: Dict[str, Any]) -> Dict[str, Any]:
    """Check Plaid connection status."""
    try:
        tools = _import_financial_tools()
        message, metadata = tools["status"]()
        return _result_to_mcp(message, metadata)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}
