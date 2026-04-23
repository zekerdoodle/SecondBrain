"""
SnapTrade Investment Tools — Backend Integration

Provides access to investment account data through the SnapTrade API.
Complements Plaid (banking/spending) with brokerage account data
(positions, holdings, orders, performance).

Available Tools:
- snaptrade_register: One-time user registration
- snaptrade_connect: Generate brokerage connection URL
- snaptrade_accounts: List connected investment accounts
- snaptrade_holdings: Get positions/holdings for an account
- snaptrade_orders: Get order history for an account
- snaptrade_activities: Get account activities (dividends, trades, etc.)
- snaptrade_performance: Get account return rates
- snaptrade_connections: List/manage brokerage connections
- snaptrade_search_symbol: Look up ticker symbols
- snaptrade_preview_order: Preview a trade before placing it
- snaptrade_execute_order: Execute a previewed trade
- snaptrade_status: Check SnapTrade API status

All credentials are stored in .env (never in config or vault directly).
User secrets and cached data are stored in vault/financial/snaptrade/.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .utils.theo_logger import cli_logger

logger = cli_logger

# Lazy import to avoid hard dependency failure at module load time
_snaptrade_client = None


def _get_client():
    """Lazy load the SnapTrade client to provide helpful error messages."""
    global _snaptrade_client
    if _snaptrade_client is None:
        try:
            from .utils.snaptrade_client import get_snaptrade_client
            _snaptrade_client = get_snaptrade_client()
        except ImportError:
            return None, "SnapTrade SDK not installed. Install with: pip install snaptrade-python-sdk"
        except ValueError as e:
            return None, str(e)
        except Exception as e:
            return None, f"Failed to initialize SnapTrade client: {e}"
    return _snaptrade_client, None


def snaptrade_register(user_id: str = "user") -> Tuple[str, Dict[str, Any]]:
    """
    Register a SnapTrade user (one-time setup).

    Args:
        user_id: Unique user identifier (default: "user")

    Returns:
        Tuple of (message, metadata)
    """
    client, error = _get_client()
    if error:
        logger.error("L4.snaptrade [tool:register] - %s", error)
        return f"ERROR: {error}", {"success": False, "error": error}

    try:
        success, message, metadata = client.register_user(user_id)

        if success:
            if metadata.get("already_registered"):
                return (
                    f"✅ {message}\n\nYou're already registered — no action needed.",
                    {"success": True, **metadata},
                )
            return (
                f"✅ {message}\n\n"
                "Next: Run `snaptrade_connect` to link your brokerage accounts.",
                {"success": True, **metadata},
            )
        else:
            return f"ERROR: {message}", {"success": False, "error": message}

    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        logger.error("L4.snaptrade [tool:register] - %s", error_msg)
        return f"ERROR: {error_msg}", {"success": False, "error": error_msg}


def snaptrade_connect(broker: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """
    Generate a URL to connect a brokerage account.

    Args:
        broker: Optional broker slug (e.g., "FIDELITY", "SCHWAB")

    Returns:
        Tuple of (message, metadata with URL)
    """
    client, error = _get_client()
    if error:
        logger.error("L4.snaptrade [tool:connect] - %s", error)
        return f"ERROR: {error}", {"success": False, "error": error}

    try:
        success, message, metadata = client.generate_connect_url(broker)

        if success:
            url = metadata.get("redirect_url", "")
            lines = [
                "🔗 Brokerage Connection Link Generated",
                "",
                f"Open this URL in your browser to connect{f' {broker}' if broker else ''}:",
                f"  {url}",
                "",
                "After completing the OAuth flow, your account will be automatically linked.",
                "Then run `snaptrade_accounts` to see your connected accounts.",
            ]
            return "\n".join(lines), {"success": True, **metadata}
        else:
            return f"ERROR: {message}", {"success": False, "error": message}

    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        logger.error("L4.snaptrade [tool:connect] - %s", error_msg)
        return f"ERROR: {error_msg}", {"success": False, "error": error_msg}


def snaptrade_accounts() -> Tuple[str, Dict[str, Any]]:
    """
    List all connected investment accounts.

    Returns:
        Tuple of (formatted message, metadata)
    """
    client, error = _get_client()
    if error:
        logger.error("L4.snaptrade [tool:accounts] - %s", error)
        return f"ERROR: {error}", {"success": False, "error": error}

    try:
        success, message, accounts = client.list_accounts()

        if not success:
            return f"ERROR: {message}", {"success": False, "error": message}

        if not accounts:
            return (
                "No investment accounts connected yet.\n"
                "Run `snaptrade_connect` to link a brokerage (Fidelity, Schwab, etc.)",
                {"success": True, "account_count": 0, "accounts": []},
            )

        lines = [f"📈 Connected Investment Accounts ({len(accounts)}):\n"]

        for i, acct in enumerate(accounts, 1):
            name = acct.get("name", "Unknown")
            institution = acct.get("institution_name", "")
            number = acct.get("number", "")
            acct_id = acct.get("account_id", "")

            lines.append(f"{i}. {name}")
            if institution:
                lines.append(f"   Institution: {institution}")
            if number:
                lines.append(f"   Account #: ...{number[-4:]}" if len(number) > 4 else f"   Account #: {number}")
            lines.append(f"   ID: {acct_id}")
            lines.append("")

        return (
            "\n".join(lines),
            {
                "success": True,
                "account_count": len(accounts),
                "accounts": accounts,
            },
        )

    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        logger.error("L4.snaptrade [tool:accounts] - %s", error_msg)
        return f"ERROR: {error_msg}", {"success": False, "error": error_msg}


def snaptrade_holdings(account_id: str) -> Tuple[str, Dict[str, Any]]:
    """
    Get positions/holdings for a specific account.

    Args:
        account_id: The account ID (from snaptrade_accounts)

    Returns:
        Tuple of (formatted message, metadata)
    """
    client, error = _get_client()
    if error:
        logger.error("L4.snaptrade [tool:holdings] - %s", error)
        return f"ERROR: {error}", {"success": False, "error": error}

    try:
        # Get positions
        success, message, positions = client.get_account_positions(account_id)

        if not success:
            return f"ERROR: {message}", {"success": False, "error": message}

        # Also get balances
        bal_success, bal_msg, balances = client.get_account_balances(account_id)

        lines = [f"📊 Holdings for Account {account_id}\n"]

        # Show balances
        if bal_success and balances:
            for bal in balances:
                currency = bal.get("currency", "USD")
                cash = bal.get("cash")
                buying_power = bal.get("buying_power")
                if cash is not None:
                    lines.append(f"💵 Cash ({currency}): ${float(cash):,.2f}")
                if buying_power is not None:
                    lines.append(f"💳 Buying Power ({currency}): ${float(buying_power):,.2f}")
            lines.append("")

        if not positions:
            lines.append("No positions found in this account.")
        else:
            lines.append(f"Positions ({len(positions)}):\n")

            total_value = 0.0
            for i, pos in enumerate(positions, 1):
                symbol = pos.get("symbol", "???")
                description = pos.get("description", "")
                units = pos.get("units")
                price = pos.get("price")
                open_pnl = pos.get("open_pnl")
                avg_price = pos.get("average_purchase_price")

                label = f"{symbol}"
                if description:
                    label += f" — {description}"

                lines.append(f"{i}. {label}")
                if units is not None:
                    lines.append(f"   Shares: {float(units):,.4f}")
                if price is not None:
                    lines.append(f"   Price: ${float(price):,.2f}")
                    if units is not None:
                        value = float(units) * float(price)
                        total_value += value
                        lines.append(f"   Value: ${value:,.2f}")
                if avg_price is not None:
                    lines.append(f"   Avg Cost: ${float(avg_price):,.2f}")
                if open_pnl is not None:
                    pnl_val = float(open_pnl)
                    pnl_str = f"+${pnl_val:,.2f}" if pnl_val >= 0 else f"-${abs(pnl_val):,.2f}"
                    lines.append(f"   P&L: {pnl_str}")
                lines.append("")

            if total_value > 0:
                lines.append(f"📈 Total Portfolio Value: ${total_value:,.2f}")

        return (
            "\n".join(lines),
            {
                "success": True,
                "position_count": len(positions),
                "positions": positions,
                "balances": balances if bal_success else [],
            },
        )

    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        logger.error("L4.snaptrade [tool:holdings] - %s", error_msg)
        return f"ERROR: {error_msg}", {"success": False, "error": error_msg}


def snaptrade_orders(
    account_id: str,
    state: Optional[str] = None,
    days: int = 30,
) -> Tuple[str, Dict[str, Any]]:
    """
    Get order history for an account.

    Args:
        account_id: The account ID
        state: Filter by state ("Executed", "Cancelled", etc.)
        days: Days to look back (default 30)

    Returns:
        Tuple of (formatted message, metadata)
    """
    client, error = _get_client()
    if error:
        logger.error("L4.snaptrade [tool:orders] - %s", error)
        return f"ERROR: {error}", {"success": False, "error": error}

    try:
        success, message, orders = client.get_account_orders(account_id, state, days)

        if not success:
            return f"ERROR: {message}", {"success": False, "error": message}

        if not orders:
            return (
                f"No orders found for account {account_id} in the last {days} days.",
                {"success": True, "order_count": 0, "orders": []},
            )

        lines = [f"📋 Orders for Account {account_id} (last {days} days):\n"]

        for i, order in enumerate(orders, 1):
            lines.append(f"{i}. {json.dumps(order, indent=2, default=str)}")
            lines.append("")

        return (
            "\n".join(lines),
            {"success": True, "order_count": len(orders), "orders": orders},
        )

    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        logger.error("L4.snaptrade [tool:orders] - %s", error_msg)
        return f"ERROR: {error_msg}", {"success": False, "error": error_msg}


def snaptrade_activities(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    account_id: Optional[str] = None,
    activity_type: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Get account activities (dividends, trades, transfers, etc.)

    Args:
        start_date: Start date YYYY-MM-DD
        end_date: End date YYYY-MM-DD
        account_id: Optional account filter
        activity_type: Optional type filter

    Returns:
        Tuple of (formatted message, metadata)
    """
    client, error = _get_client()
    if error:
        logger.error("L4.snaptrade [tool:activities] - %s", error)
        return f"ERROR: {error}", {"success": False, "error": error}

    try:
        success, message, activities = client.get_activities(
            start_date, end_date, account_id, activity_type
        )

        if not success:
            return f"ERROR: {message}", {"success": False, "error": message}

        if not activities:
            period = f"from {start_date or 'last 30 days'} to {end_date or 'today'}"
            return (
                f"No activities found {period}.",
                {"success": True, "activity_count": 0, "activities": []},
            )

        lines = [f"📜 Account Activities ({len(activities)}):\n"]

        for i, act in enumerate(activities[:50], 1):  # Limit display
            lines.append(f"{i}. {json.dumps(act, indent=2, default=str)}")
            lines.append("")

        if len(activities) > 50:
            lines.append(f"... and {len(activities) - 50} more activities")

        return (
            "\n".join(lines),
            {"success": True, "activity_count": len(activities), "activities": activities},
        )

    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        logger.error("L4.snaptrade [tool:activities] - %s", error_msg)
        return f"ERROR: {error_msg}", {"success": False, "error": error_msg}


def snaptrade_performance(account_id: str) -> Tuple[str, Dict[str, Any]]:
    """
    Get return rates/performance for an account.

    Args:
        account_id: The account ID

    Returns:
        Tuple of (formatted message, metadata)
    """
    client, error = _get_client()
    if error:
        logger.error("L4.snaptrade [tool:performance] - %s", error)
        return f"ERROR: {error}", {"success": False, "error": error}

    try:
        success, message, perf_data = client.get_account_performance(account_id)

        if not success:
            return f"ERROR: {message}", {"success": False, "error": message}

        lines = [f"📈 Performance for Account {account_id}\n"]
        lines.append(json.dumps(perf_data, indent=2, default=str))

        return (
            "\n".join(lines),
            {"success": True, "performance": perf_data},
        )

    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        logger.error("L4.snaptrade [tool:performance] - %s", error_msg)
        return f"ERROR: {error_msg}", {"success": False, "error": error_msg}


def snaptrade_connections() -> Tuple[str, Dict[str, Any]]:
    """
    List all brokerage connections and their status.

    Returns:
        Tuple of (formatted message, metadata)
    """
    client, error = _get_client()
    if error:
        logger.error("L4.snaptrade [tool:connections] - %s", error)
        return f"ERROR: {error}", {"success": False, "error": error}

    try:
        success, message, connections = client.list_connections()

        if not success:
            return f"ERROR: {message}", {"success": False, "error": message}

        if not connections:
            return (
                "🔌 No brokerage connections found.\n"
                "Run `snaptrade_connect` to link a brokerage account.",
                {"success": True, "connection_count": 0, "connections": []},
            )

        lines = [f"🔗 Brokerage Connections ({len(connections)}):\n"]

        for i, conn in enumerate(connections, 1):
            name = conn.get("brokerage_name", "Unknown")
            auth_id = conn.get("authorization_id", "")
            created = conn.get("created_at", "")
            disabled = conn.get("disabled", False)

            status = "🔴 Disabled" if disabled else "🟢 Active"

            lines.append(f"{i}. {name} ({status})")
            lines.append(f"   Authorization ID: {auth_id}")
            if created:
                lines.append(f"   Connected: {created}")
            lines.append("")

        return (
            "\n".join(lines),
            {
                "success": True,
                "connection_count": len(connections),
                "connections": connections,
            },
        )

    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        logger.error("L4.snaptrade [tool:connections] - %s", error_msg)
        return f"ERROR: {error_msg}", {"success": False, "error": error_msg}


def snaptrade_disconnect(authorization_id: str) -> Tuple[str, Dict[str, Any]]:
    """
    Remove a single brokerage authorization by its ID.

    Use this to kill zombie / disabled auth records after a reauth.

    Args:
        authorization_id: The authorization ID to remove

    Returns:
        Tuple of (formatted message, metadata)
    """
    client, error = _get_client()
    if error:
        logger.error("L4.snaptrade [tool:disconnect] - %s", error)
        return f"ERROR: {error}", {"success": False, "error": error}

    try:
        success, message = client.remove_connection(authorization_id)

        if not success:
            return f"ERROR: {message}", {
                "success": False,
                "error": message,
                "authorization_id": authorization_id,
            }

        return (
            f"✅ {message}",
            {"success": True, "authorization_id": authorization_id},
        )

    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        logger.error("L4.snaptrade [tool:disconnect] - %s", error_msg)
        return f"ERROR: {error_msg}", {"success": False, "error": error_msg}


def snaptrade_cleanup_dead() -> Tuple[str, Dict[str, Any]]:
    """
    Sweep all disabled brokerage authorizations and remove them.

    Lists connections, filters to disabled=True, and calls remove_connection
    on each. Safe to run any time — a no-op if no dead auths exist.

    Returns:
        Tuple of (formatted message, metadata)
    """
    client, error = _get_client()
    if error:
        logger.error("L4.snaptrade [tool:cleanup_dead] - %s", error)
        return f"ERROR: {error}", {"success": False, "error": error}

    try:
        success, message, connections = client.list_connections()
        if not success:
            return f"ERROR: {message}", {"success": False, "error": message}

        dead = [c for c in connections if c.get("disabled")]

        if not dead:
            return (
                "✨ No dead authorizations to clean up. All connections are active.",
                {
                    "success": True,
                    "removed_count": 0,
                    "total_connections": len(connections),
                },
            )

        removed = []
        failed = []

        for conn in dead:
            auth_id = conn.get("authorization_id", "")
            name = conn.get("brokerage_name", "Unknown")
            ok, msg = client.remove_connection(auth_id)
            if ok:
                removed.append({"authorization_id": auth_id, "brokerage_name": name})
            else:
                failed.append(
                    {
                        "authorization_id": auth_id,
                        "brokerage_name": name,
                        "error": msg,
                    }
                )

        lines = []
        if removed:
            lines.append(f"🧹 Removed {len(removed)} dead authorization(s):")
            for r in removed:
                lines.append(f"  • {r['brokerage_name']} ({r['authorization_id']})")
        if failed:
            lines.append(f"\n⚠️ Failed to remove {len(failed)}:")
            for f in failed:
                lines.append(
                    f"  • {f['brokerage_name']} ({f['authorization_id']}): {f['error']}"
                )

        return (
            "\n".join(lines),
            {
                "success": len(failed) == 0,
                "removed_count": len(removed),
                "failed_count": len(failed),
                "removed": removed,
                "failed": failed,
            },
        )

    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        logger.error("L4.snaptrade [tool:cleanup_dead] - %s", error_msg)
        return f"ERROR: {error_msg}", {"success": False, "error": error_msg}


def snaptrade_search_symbol(query: str) -> Tuple[str, Dict[str, Any]]:
    """
    Search for a ticker symbol.

    Args:
        query: Ticker or company name

    Returns:
        Tuple of (formatted message, metadata)
    """
    client, error = _get_client()
    if error:
        logger.error("L4.snaptrade [tool:search] - %s", error)
        return f"ERROR: {error}", {"success": False, "error": error}

    try:
        success, message, symbols = client.search_symbols(query)

        if not success:
            return f"ERROR: {message}", {"success": False, "error": message}

        if not symbols:
            return (
                f"No symbols found matching '{query}'.",
                {"success": True, "symbol_count": 0, "symbols": []},
            )

        lines = [f"🔍 Symbol Search Results for '{query}':\n"]

        for i, sym in enumerate(symbols[:20], 1):
            lines.append(f"{i}. {json.dumps(sym, indent=2, default=str)}")
            lines.append("")

        return (
            "\n".join(lines),
            {"success": True, "symbol_count": len(symbols), "symbols": symbols},
        )

    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        logger.error("L4.snaptrade [tool:search] - %s", error_msg)
        return f"ERROR: {error_msg}", {"success": False, "error": error_msg}


def snaptrade_preview_order(
    account_id: str,
    action: str,
    symbol_id: str,
    order_type: str = "Market",
    time_in_force: str = "Day",
    units: Optional[float] = None,
    price: Optional[float] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Preview a trade before placing it.

    Args:
        account_id: Account to trade in
        action: "BUY" or "SELL"
        symbol_id: Universal symbol ID
        order_type: "Market", "Limit", "Stop", "StopLimit"
        time_in_force: "Day", "GTC", "FOK", "IOC"
        units: Number of shares
        price: Limit price (if applicable)

    Returns:
        Tuple of (message with impact, metadata with trade_id)
    """
    client, error = _get_client()
    if error:
        logger.error("L4.snaptrade [tool:preview_order] - %s", error)
        return f"ERROR: {error}", {"success": False, "error": error}

    try:
        success, message, impact = client.preview_order(
            account_id=account_id,
            action=action,
            symbol_id=symbol_id,
            order_type=order_type,
            time_in_force=time_in_force,
            units=units,
            price=price,
        )

        if not success:
            return f"ERROR: {message}", {"success": False, "error": message}

        lines = [
            "⚠️ Trade Preview — Review Before Executing\n",
            f"Action: {action}",
            f"Order Type: {order_type}",
            f"Time in Force: {time_in_force}",
            "",
            "Impact:",
            json.dumps(impact, indent=2, default=str),
            "",
            "To execute this trade, run `snaptrade_execute_order` with the trade_id above.",
        ]

        return (
            "\n".join(lines),
            {"success": True, "impact": impact},
        )

    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        logger.error("L4.snaptrade [tool:preview_order] - %s", error_msg)
        return f"ERROR: {error_msg}", {"success": False, "error": error_msg}


def snaptrade_execute_order(trade_id: str) -> Tuple[str, Dict[str, Any]]:
    """
    Execute a previously previewed trade.

    Args:
        trade_id: Trade ID from snaptrade_preview_order

    Returns:
        Tuple of (message, metadata)
    """
    client, error = _get_client()
    if error:
        logger.error("L4.snaptrade [tool:execute_order] - %s", error)
        return f"ERROR: {error}", {"success": False, "error": error}

    try:
        success, message, result = client.execute_order(trade_id)

        if not success:
            return f"ERROR: {message}", {"success": False, "error": message}

        return (
            f"✅ {message}\n\n{json.dumps(result, indent=2, default=str)}",
            {"success": True, "order_result": result},
        )

    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        logger.error("L4.snaptrade [tool:execute_order] - %s", error_msg)
        return f"ERROR: {error_msg}", {"success": False, "error": error_msg}


def snaptrade_status() -> Tuple[str, Dict[str, Any]]:
    """
    Check SnapTrade API and connection status.

    Returns:
        Tuple of (formatted message, metadata)
    """
    client, error = _get_client()
    if error:
        logger.error("L4.snaptrade [tool:status] - %s", error)
        return f"ERROR: {error}", {"success": False, "error": error}

    try:
        # Check API status
        api_ok, api_msg, api_data = client.check_api_status()

        # Check credentials
        user_id, user_secret = client._get_user_credentials()
        has_credentials = bool(user_id and user_secret)

        # Check connections
        conn_msg = "Not checked"
        conn_count = 0
        connections = []
        if has_credentials:
            conn_ok, conn_msg, connections = client.list_connections()
            conn_count = len(connections) if conn_ok else 0

        lines = [
            "📊 SnapTrade Status\n",
            f"API: {'✅ Operational' if api_ok else '❌ ' + api_msg}",
            f"User Registered: {'✅ ' + user_id if has_credentials else '❌ Not registered'}",
            f"Brokerage Connections: {conn_count}",
        ]

        if connections:
            for conn in connections:
                status = "🔴 Disabled" if conn.get("disabled") else "🟢 Active"
                lines.append(f"  • {conn.get('brokerage_name', 'Unknown')} ({status})")

        checked_at = datetime.now().isoformat(timespec="seconds")
        lines.append(f"\nChecked at: {checked_at}")

        return (
            "\n".join(lines),
            {
                "success": True,
                "api_status": api_data,
                "has_credentials": has_credentials,
                "user_id": user_id,
                "connection_count": conn_count,
                "connections": connections,
                "checked_at": checked_at,
            },
        )

    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        logger.error("L4.snaptrade [tool:status] - %s", error_msg)
        return f"ERROR: {error_msg}", {"success": False, "error": error_msg}


__all__ = [
    "snaptrade_register",
    "snaptrade_connect",
    "snaptrade_accounts",
    "snaptrade_holdings",
    "snaptrade_orders",
    "snaptrade_activities",
    "snaptrade_performance",
    "snaptrade_connections",
    "snaptrade_disconnect",
    "snaptrade_cleanup_dead",
    "snaptrade_search_symbol",
    "snaptrade_preview_order",
    "snaptrade_execute_order",
    "snaptrade_status",
]
