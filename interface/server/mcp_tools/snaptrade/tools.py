"""
SnapTrade tools — Investment brokerage integration wrappers.

MCP tool wrappers around the SnapTrade backend in
.claude/scripts/theo_ports/snaptrade_tools.py.

Tools:
- snaptrade_register: One-time user registration
- snaptrade_connect: Generate brokerage connection URL
- snaptrade_accounts: List connected investment accounts
- snaptrade_holdings: Get positions/holdings for an account
- snaptrade_orders: Get order history
- snaptrade_activities: Get account activities (dividends, trades, etc.)
- snaptrade_performance: Get account return rates
- snaptrade_connections: List/manage brokerage connections
- snaptrade_search_symbol: Look up ticker symbols
- snaptrade_preview_order: Preview a trade
- snaptrade_execute_order: Execute a previewed trade
- snaptrade_status: Check SnapTrade connection status
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


def _import_snaptrade_tools():
    """Lazy import the SnapTrade tools backend."""
    from theo_ports.snaptrade_tools import (
        snaptrade_register as _register,
        snaptrade_connect as _connect,
        snaptrade_accounts as _accounts,
        snaptrade_holdings as _holdings,
        snaptrade_orders as _orders,
        snaptrade_activities as _activities,
        snaptrade_performance as _performance,
        snaptrade_connections as _connections,
        snaptrade_disconnect as _disconnect,
        snaptrade_cleanup_dead as _cleanup_dead,
        snaptrade_search_symbol as _search_symbol,
        snaptrade_preview_order as _preview_order,
        snaptrade_execute_order as _execute_order,
        snaptrade_status as _status,
    )
    return {
        "register": _register,
        "connect": _connect,
        "accounts": _accounts,
        "holdings": _holdings,
        "orders": _orders,
        "activities": _activities,
        "performance": _performance,
        "connections": _connections,
        "disconnect": _disconnect,
        "cleanup_dead": _cleanup_dead,
        "search_symbol": _search_symbol,
        "preview_order": _preview_order,
        "execute_order": _execute_order,
        "status": _status,
    }


def _result_to_mcp(message: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Convert (message, metadata) tuple to MCP response format."""
    is_error = not metadata.get("success", True)
    return {
        "content": [{"type": "text", "text": message}],
        **({"is_error": True} if is_error else {})
    }


# =============================================================================
# Setup & Connection Tools
# =============================================================================

@register_tool("snaptrade")
@tool(
    name="snaptrade_register",
    description="""Register a SnapTrade user (one-time setup).

Creates a user in SnapTrade and stores credentials in the vault.
Must be done before connecting any brokerages.""",
    input_schema={
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "User identifier (default: 'user')", "default": "user"}
        }
    }
)
async def snaptrade_register(args: Dict[str, Any]) -> Dict[str, Any]:
    """Register a SnapTrade user."""
    try:
        tools = _import_snaptrade_tools()
        message, metadata = tools["register"](
            user_id=args.get("user_id", "user")
        )
        return _result_to_mcp(message, metadata)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("snaptrade")
@tool(
    name="snaptrade_connect",
    description="""Generate a URL to connect a brokerage account (Fidelity, Schwab, etc.).

Opens a secure OAuth flow in the browser. After the user completes it,
the brokerage account is automatically linked.""",
    input_schema={
        "type": "object",
        "properties": {
            "broker": {"type": "string", "description": "Optional broker to pre-select (e.g., 'FIDELITY', 'SCHWAB')"}
        }
    }
)
async def snaptrade_connect(args: Dict[str, Any]) -> Dict[str, Any]:
    """Generate brokerage connection URL."""
    try:
        tools = _import_snaptrade_tools()
        message, metadata = tools["connect"](
            broker=args.get("broker")
        )
        return _result_to_mcp(message, metadata)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


# =============================================================================
# Account Data Tools
# =============================================================================

@register_tool("snaptrade")
@tool(
    name="snaptrade_accounts",
    description="""List all connected investment/brokerage accounts.

Shows account names, institutions, and IDs. Use the account IDs
with other snaptrade tools to get holdings, orders, etc.""",
    input_schema={
        "type": "object",
        "properties": {}
    }
)
async def snaptrade_accounts(args: Dict[str, Any]) -> Dict[str, Any]:
    """List investment accounts."""
    try:
        tools = _import_snaptrade_tools()
        message, metadata = tools["accounts"]()
        return _result_to_mcp(message, metadata)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("snaptrade")
@tool(
    name="snaptrade_holdings",
    description="""Get positions/holdings and balances for a specific investment account.

Shows all securities held, quantities, prices, P&L, and cash balances.
Requires an account_id from snaptrade_accounts.""",
    input_schema={
        "type": "object",
        "properties": {
            "account_id": {"type": "string", "description": "The account ID (from snaptrade_accounts)"}
        },
        "required": ["account_id"]
    }
)
async def snaptrade_holdings(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get account holdings/positions."""
    try:
        account_id = args.get("account_id")
        if not account_id:
            return {"content": [{"type": "text", "text": "account_id is required"}], "is_error": True}

        tools = _import_snaptrade_tools()
        message, metadata = tools["holdings"](account_id=account_id)
        return _result_to_mcp(message, metadata)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("snaptrade")
@tool(
    name="snaptrade_orders",
    description="""Get order history for an investment account.

Shows recent buy/sell orders with status, fill prices, etc.""",
    input_schema={
        "type": "object",
        "properties": {
            "account_id": {"type": "string", "description": "The account ID"},
            "state": {"type": "string", "description": "Filter by state (e.g., 'Executed', 'Cancelled')"},
            "days": {"type": "integer", "description": "Days to look back (default: 30)", "default": 30}
        },
        "required": ["account_id"]
    }
)
async def snaptrade_orders(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get order history."""
    try:
        account_id = args.get("account_id")
        if not account_id:
            return {"content": [{"type": "text", "text": "account_id is required"}], "is_error": True}

        tools = _import_snaptrade_tools()
        message, metadata = tools["orders"](
            account_id=account_id,
            state=args.get("state"),
            days=args.get("days", 30),
        )
        return _result_to_mcp(message, metadata)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("snaptrade")
@tool(
    name="snaptrade_activities",
    description="""Get account activities: dividends, trades, transfers, fees, etc.

Shows a feed of all activity across investment accounts.""",
    input_schema={
        "type": "object",
        "properties": {
            "start_date": {"type": "string", "description": "Start date YYYY-MM-DD (default: 30 days ago)"},
            "end_date": {"type": "string", "description": "End date YYYY-MM-DD (default: today)"},
            "account_id": {"type": "string", "description": "Optional account ID filter"},
            "activity_type": {"type": "string", "description": "Optional type filter (DIVIDEND, BUY, SELL, etc.)"}
        }
    }
)
async def snaptrade_activities(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get account activities."""
    try:
        tools = _import_snaptrade_tools()
        message, metadata = tools["activities"](
            start_date=args.get("start_date"),
            end_date=args.get("end_date"),
            account_id=args.get("account_id"),
            activity_type=args.get("activity_type"),
        )
        return _result_to_mcp(message, metadata)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("snaptrade")
@tool(
    name="snaptrade_performance",
    description="""Get return rates and performance data for an investment account.

Shows historical returns and performance metrics.""",
    input_schema={
        "type": "object",
        "properties": {
            "account_id": {"type": "string", "description": "The account ID"}
        },
        "required": ["account_id"]
    }
)
async def snaptrade_performance(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get account performance."""
    try:
        account_id = args.get("account_id")
        if not account_id:
            return {"content": [{"type": "text", "text": "account_id is required"}], "is_error": True}

        tools = _import_snaptrade_tools()
        message, metadata = tools["performance"](account_id=account_id)
        return _result_to_mcp(message, metadata)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


# =============================================================================
# Connection Management Tools
# =============================================================================

@register_tool("snaptrade")
@tool(
    name="snaptrade_connections",
    description="""List all brokerage connections and their status.

Shows which brokerages are connected, active/disabled status,
and authorization IDs for management.""",
    input_schema={
        "type": "object",
        "properties": {}
    }
)
async def snaptrade_connections(args: Dict[str, Any]) -> Dict[str, Any]:
    """List brokerage connections."""
    try:
        tools = _import_snaptrade_tools()
        message, metadata = tools["connections"]()
        return _result_to_mcp(message, metadata)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("snaptrade")
@tool(
    name="snaptrade_disconnect",
    description="""Remove a single brokerage authorization by ID.

Use this to kill zombie auth records — SnapTrade's reauth flow soft-disables
old tokens (status=disabled) instead of deleting them, so disabled auths
accumulate unless explicitly removed.

Get the authorization_id from snaptrade_connections. Safe to call on both
active and disabled auths, but you almost always want to only target
disabled ones.""",
    input_schema={
        "type": "object",
        "properties": {
            "authorization_id": {
                "type": "string",
                "description": "The authorization ID to remove (from snaptrade_connections)"
            }
        },
        "required": ["authorization_id"]
    }
)
async def snaptrade_disconnect(args: Dict[str, Any]) -> Dict[str, Any]:
    """Remove a single brokerage authorization."""
    try:
        authorization_id = args.get("authorization_id")
        if not authorization_id:
            return {"content": [{"type": "text", "text": "authorization_id is required"}], "is_error": True}

        tools = _import_snaptrade_tools()
        message, metadata = tools["disconnect"](authorization_id=authorization_id)
        return _result_to_mcp(message, metadata)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("snaptrade")
@tool(
    name="snaptrade_cleanup_dead",
    description="""Sweep all disabled brokerage authorizations and remove them.

Lists connections, filters to disabled=True, and removes each one.
Safe to run any time — a no-op if no dead auths exist.

Use this after a reauth to clean up zombie tokens left behind by
SnapTrade's "append, don't replace" auth behavior.""",
    input_schema={
        "type": "object",
        "properties": {}
    }
)
async def snaptrade_cleanup_dead(args: Dict[str, Any]) -> Dict[str, Any]:
    """Remove all disabled brokerage authorizations."""
    try:
        tools = _import_snaptrade_tools()
        message, metadata = tools["cleanup_dead"]()
        return _result_to_mcp(message, metadata)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("snaptrade")
@tool(
    name="snaptrade_status",
    description="""Check SnapTrade overall status: API health, user registration, and connection count.""",
    input_schema={
        "type": "object",
        "properties": {}
    }
)
async def snaptrade_status(args: Dict[str, Any]) -> Dict[str, Any]:
    """Check SnapTrade status."""
    try:
        tools = _import_snaptrade_tools()
        message, metadata = tools["status"]()
        return _result_to_mcp(message, metadata)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


# =============================================================================
# Symbol Search
# =============================================================================

@register_tool("snaptrade")
@tool(
    name="snaptrade_search_symbol",
    description="""Search for a ticker symbol or security.

Look up stocks, ETFs, etc. by ticker or company name.
Returns universal symbol IDs needed for trading.""",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Ticker symbol or company name to search"}
        },
        "required": ["query"]
    }
)
async def snaptrade_search_symbol(args: Dict[str, Any]) -> Dict[str, Any]:
    """Search for symbols."""
    try:
        query = args.get("query")
        if not query:
            return {"content": [{"type": "text", "text": "query is required"}], "is_error": True}

        tools = _import_snaptrade_tools()
        message, metadata = tools["search_symbol"](query=query)
        return _result_to_mcp(message, metadata)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


# =============================================================================
# Trading Tools (Schwab only — Fidelity is read-only)
# =============================================================================

@register_tool("snaptrade")
@tool(
    name="snaptrade_preview_order",
    description="""Preview a trade before placing it. Shows estimated cost, fees, and impact.

IMPORTANT: This only previews — it does NOT execute the trade.
Use snaptrade_execute_order with the returned trade_id to actually place the order.

Note: Trading is only available for brokerages that support it (e.g., Schwab).
Fidelity accounts are read-only.""",
    input_schema={
        "type": "object",
        "properties": {
            "account_id": {"type": "string", "description": "Account to trade in"},
            "action": {"type": "string", "enum": ["BUY", "SELL"], "description": "Buy or sell"},
            "symbol_id": {"type": "string", "description": "Universal symbol ID (from snaptrade_search_symbol)"},
            "order_type": {"type": "string", "enum": ["Market", "Limit", "Stop", "StopLimit"], "description": "Order type (default: Market)", "default": "Market"},
            "time_in_force": {"type": "string", "enum": ["Day", "GTC", "FOK", "IOC"], "description": "Time in force (default: Day)", "default": "Day"},
            "units": {"type": "number", "description": "Number of shares"},
            "price": {"type": "number", "description": "Limit price (required for Limit/StopLimit orders)"}
        },
        "required": ["account_id", "action", "symbol_id"]
    }
)
async def snaptrade_preview_order(args: Dict[str, Any]) -> Dict[str, Any]:
    """Preview a trade."""
    try:
        for field in ["account_id", "action", "symbol_id"]:
            if not args.get(field):
                return {"content": [{"type": "text", "text": f"{field} is required"}], "is_error": True}

        tools = _import_snaptrade_tools()
        message, metadata = tools["preview_order"](
            account_id=args["account_id"],
            action=args["action"],
            symbol_id=args["symbol_id"],
            order_type=args.get("order_type", "Market"),
            time_in_force=args.get("time_in_force", "Day"),
            units=args.get("units"),
            price=args.get("price"),
        )
        return _result_to_mcp(message, metadata)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("snaptrade")
@tool(
    name="snaptrade_execute_order",
    description="""Execute a previously previewed trade.

IMPORTANT: This actually places the order! Make sure the user has reviewed
the preview from snaptrade_preview_order first.

Requires the trade_id returned from snaptrade_preview_order.""",
    input_schema={
        "type": "object",
        "properties": {
            "trade_id": {"type": "string", "description": "Trade ID from snaptrade_preview_order"}
        },
        "required": ["trade_id"]
    }
)
async def snaptrade_execute_order(args: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a previewed trade."""
    try:
        trade_id = args.get("trade_id")
        if not trade_id:
            return {"content": [{"type": "text", "text": "trade_id is required"}], "is_error": True}

        tools = _import_snaptrade_tools()
        message, metadata = tools["execute_order"](trade_id=trade_id)
        return _result_to_mcp(message, metadata)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}
