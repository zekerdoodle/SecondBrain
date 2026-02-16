"""
Financial Tools for Theo (Layer 4) - Plaid Integration

Provides secure access to financial data through Plaid API. All tools are read-only
and follow Theo's security principles with local vault storage and user control.

Available Tools:
- connect_bank_account: Initialize Plaid Link for bank connection
- get_financial_accounts: List all connected accounts with balances
- get_transactions: Fetch transaction history with filtering
- get_spending_analysis: Analyze spending by category
- disconnect_bank_account: Remove bank connection
- get_connection_status: Check Plaid connection status

All credentials are stored in .env (never in config.yaml or vault).
All user financial data is stored locally in vault/financial/.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .utils.theo_logger import cli_logger

# Use cli_logger for basic logging
logger = cli_logger

# Lazy import to avoid hard dependency failure at module load time
_plaid_client = None


def _get_client():
    """Lazy load the Plaid client to provide helpful error messages."""
    global _plaid_client
    if _plaid_client is None:
        try:
            from .utils.plaid_client import get_plaid_client
            _plaid_client = get_plaid_client()
        except ImportError:
            return None, "Plaid SDK not installed. Install with: pip install plaid-python"
        except ValueError as e:
            return None, str(e)
        except Exception as e:
            return None, f"Failed to initialize Plaid client: {e}"
    return _plaid_client, None


def connect_bank_account(public_token: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """
    Connect a bank account using Plaid Link.
    
    This tool has two modes:
    1. If no public_token is provided: Creates a Link token that the user can use
       to open Plaid Link and connect their bank.
    2. If public_token is provided: Exchanges it for an access token and completes
       the connection process.
    
    Args:
        public_token: Optional public token from Plaid Link (after user connects bank)
    
    Returns:
        Tuple of (message, metadata_dict)
    
    Examples:
        # Step 1: Generate link token
        connect_bank_account()
        # Returns link_token for user to connect bank
        
        # Step 2: Complete connection with public token
        connect_bank_account(public_token="public-sandbox-xxx")
        # Returns success confirmation
    """
    client, error = _get_client()
    if error:
        logger.error(f"L4.financial [tool:connect_bank] - {error}")
        return f"ERROR: {error}", {"success": False, "error": error}
    
    try:
        vault_accounts_path = getattr(client, "accounts_file", None)
        vault_tokens_path = getattr(client, "access_tokens_file", None)

        if public_token:
            # Step 2: Exchange public token for access token
            success, message, access_token, item_id = client.exchange_public_token(public_token)

            if success:
                timestamp = datetime.now().isoformat(timespec="seconds")
                guidance_lines = [
                    f"✅ {message}",
                    "",
                    "Next steps:",
                    "1. Run `get_connection_status()` to confirm the new link.",
                    "2. Ask me for `get_financial_accounts()` or `get_transactions()` to pull fresh data.",
                    "",
                    "The Plaid access token has been stored securely in the vault.",
                ]
                if vault_accounts_path:
                    guidance_lines.append(f"📁 Account cache file: {vault_accounts_path}")
                guidance_lines.append(f"⏱️ Linked at: {timestamp}")

                return (
                    "\n".join(guidance_lines),
                    {
                        "success": True,
                        "action": "bank_connected",
                        "has_access_token": bool(access_token),
                        "item_id": item_id,
                        **({"vault_access_tokens_file": str(vault_tokens_path)} if vault_tokens_path else {}),
                        **({"accounts_cache_file": str(vault_accounts_path)} if vault_accounts_path else {}),
                        "connected_at": timestamp,
                    }
                )
            else:
                return f"ERROR: {message}", {"success": False, "error": message}

        else:
            # Step 1: Create link token
            success, message, link_token = client.create_link_token()

            if success:
                issued_at = datetime.now().isoformat(timespec="seconds")
                token_preview = f"{link_token[:12]}…" if isinstance(link_token, str) and len(link_token) > 12 else link_token
                instructions = [
                    "✅ Bank connection initiated successfully!",
                    "",
                    "📱 The user will be prompted to select and login to their bank account at the end of this turn.",
                    "   They'll complete the secure login flow in their browser via Plaid.",
                    "",
                    "⚠️ DO NOT call this tool again - one call per account connection is sufficient.",
                    "   The system automatically handles the connection after the user completes login.",
                    "",
                    f"🔗 Link token: `{link_token}`",
                    f"⏱️ Generated at: {issued_at}",
                    "",
                    "Next steps:",
                    "• Wait for user to complete the Plaid login flow",
                    "• After connection, use get_financial_accounts() to see their linked accounts",
                ]

                return (
                    "\n".join(instructions),
                    {
                        "success": True,
                        "action": "link_token_created",
                        "link_token": link_token,
                        "link_token_preview": token_preview,
                        "generated_at": issued_at,
                        "user_prompt_at_end_of_turn": True,
                        "instructions": "User will be prompted to login to their bank at the end of this turn. Do not call this tool again.",
                    }
                )
            else:
                return f"ERROR: {message}", {"success": False, "error": message}
    
    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        logger.error(f"L4.financial [tool:connect_bank] - {error_msg}")
        return f"ERROR: {error_msg}", {"success": False, "error": error_msg}


def get_financial_accounts() -> Tuple[str, Dict[str, Any]]:
    """
    Get all connected financial accounts with current balances.
    
    Returns account details including:
    - Account name and type (checking, savings, credit card, etc.)
    - Current balance and available balance
    - Account mask (last 4 digits)
    - Currency information
    
    Returns:
        Tuple of (formatted_message, metadata_dict)
    
    Example:
        get_financial_accounts()
        # Returns formatted list of all accounts with balances
    """
    client, error = _get_client()
    if error:
        logger.error(f"L4.financial [tool:get_accounts] - {error}")
        return f"ERROR: {error}", {"success": False, "error": error}

    try:
        success, message, accounts = client.get_accounts()

        cache_path = getattr(client, "accounts_file", None)

        if not success:
            return f"ERROR: {message}", {"success": False, "error": message}

        if not accounts:
            return (
                "No bank accounts connected yet. Use `connect_bank_account()` to get started!",
                {
                    "success": True,
                    "account_count": 0,
                    "accounts": [],
                    **({"cache_file": str(cache_path)} if cache_path else {}),
                }
            )

        # Format accounts nicely
        lines = [f"💰 Your Connected Accounts ({len(accounts)} total):\n"]

        total_balance = 0.0
        
        for i, account in enumerate(accounts, 1):
            name = account['name']
            account_type = f"{account['type']}/{account['subtype']}"
            mask = account.get('mask', '****')
            current = account.get('balance_current')
            available = account.get('balance_available')
            currency = account.get('currency', 'USD')
            account_id = account.get('account_id', '')
            # Surface the Plaid account_id so downstream tools can reference it directly.
            short_id = account_id[:12] + "…" if account_id and len(account_id) > 12 else account_id

            lines.append(f"\n{i}. {name} (...{mask})")
            lines.append(f"   Type: {account_type}")
            if account_id:
                lines.append(f"   ID: {account_id}")
                if short_id and short_id != account_id:
                    lines.append(f"   Copy ID hint: {short_id}")

            if current is not None:
                lines.append(f"   Balance: {currency} ${current:,.2f}")
                total_balance += current
            
            if available is not None and available != current:
                lines.append(f"   Available: {currency} ${available:,.2f}")
        
        lines.append(f"\n📊 Total Balance: ${total_balance:,.2f}")
        
        lines.append(f"\n✅ This is the COMPLETE account list - all {len(accounts)} accounts shown above.")
        lines.append("ℹ️ Do NOT call this repeatedly - account data only changes with transactions or new connections.")

        if cache_path:
            lines.append(f"\n📁 Full account snapshot cached at: {cache_path}")
            lines.append("   (Ask me to open this file if you need the raw JSON.)")

        # CRITICAL: Include warning/error message from client if present
        formatted_output = "\n".join(lines)
        if any(keyword in message for keyword in ["⚠️", "Warning", "warning", "Error", "cached", "fallback"]):
            formatted_output += f"\n\n{message}"

        return (
            formatted_output,
            {
                "success": True,
                "account_count": len(accounts),
                "total_balance": round(total_balance, 2),
                "accounts": accounts,
                **({"cache_file": str(cache_path)} if cache_path else {}),
            }
        )
    
    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        logger.error(f"L4.financial [tool:get_accounts] - {error_msg}")
        return f"ERROR: {error_msg}", {"success": False, "error": error_msg}


def get_transactions(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    account_id: Optional[str] = None,
    limit: int = 50
) -> Tuple[str, Dict[str, Any]]:
    """
    Get transaction history for connected accounts.
    
    Fetches and formats transaction data with details like merchant name,
    amount, category, and date. Transactions are sorted by date (most recent first).
    
    Args:
        start_date: Start date in YYYY-MM-DD format (defaults to 30 days ago)
        end_date: End date in YYYY-MM-DD format (defaults to today)
        account_id: Optional specific account ID to filter transactions
        limit: Maximum number of transactions to return (default 50, max 100)
    
    Returns:
        Tuple of (formatted_message, metadata_dict)
    
    Examples:
        # Get last 30 days of transactions
        get_transactions()
        
        # Get transactions for specific date range
        get_transactions(start_date="2025-01-01", end_date="2025-01-31")
        
        # Get transactions for specific account
        get_transactions(account_id="abc123", limit=20)
    """
    client, error = _get_client()
    if error:
        logger.error(f"L4.financial [tool:get_transactions] - {error}")
        return f"ERROR: {error}", {"success": False, "error": error}
    
    try:
        # Prepare account filter
        account_ids = [account_id] if account_id else None
        
        success, message, transactions = client.get_transactions(
            start_date=start_date,
            end_date=end_date,
            account_ids=account_ids,
            count=min(limit, 100)
        )

        cache_path = getattr(client, "transactions_file", None)

        if not success:
            return f"ERROR: {message}", {"success": False, "error": message}

        raw_transactions = transactions or []
        valid_transactions = [t for t in raw_transactions if isinstance(t, dict)]
        skipped_transactions = len(raw_transactions) - len(valid_transactions)

        if not valid_transactions:
            period = f"from {start_date or 'last 30 days'} to {end_date or 'today'}"
            message_lines = [f"No transactions found {period}."]
            if skipped_transactions:
                message_lines.append(
                    f"⚠️ Skipped {skipped_transactions} malformed transaction record(s)."
                )
            if cache_path:
                message_lines.append(f"📁 Cached transactions file: {cache_path}")
            return (
                "\n\n".join(message_lines),
                {
                    "success": True,
                    "transaction_count": 0,
                    "displayed": 0,
                    "total_spent": 0.0,
                    "total_income": 0.0,
                    "transactions": [],
                    "skipped_malformed": skipped_transactions,
                    **({"cache_file": str(cache_path)} if cache_path else {}),
                    "raw_transaction_count": 0,
                }
            )

        # Limit display to requested count
        display_transactions = valid_transactions[:limit]

        def _safe_float(value: Any) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0

        # Format transactions
        lines = [f"💳 Recent Transactions ({len(display_transactions)} shown):\n"]

        for i, txn in enumerate(display_transactions, 1):
            amount = _safe_float(txn.get('amount'))
            date = txn.get('date', 'Unknown date')
            name = txn.get('merchant_name') or txn.get('name', 'Unknown merchant')

            raw_categories = txn.get('category') or []
            if not isinstance(raw_categories, (list, tuple)):
                raw_categories = [str(raw_categories)] if raw_categories else []
            if not raw_categories:
                raw_categories = ["Uncategorized"]
            category = ' > '.join(str(c) for c in list(raw_categories)[:2])

            pending = " [PENDING]" if txn.get('pending') else ""

            amount_str = f"${abs(amount):,.2f}"
            amount_str = f"+{amount_str}" if amount < 0 else f"-{amount_str}"

            lines.append(f"\n{i}. {date} | {name}{pending}")
            lines.append(f"   {amount_str} | {category}")

        if len(valid_transactions) > limit:
            lines.append(f"\n... and {len(valid_transactions) - limit} more transactions")

        total_spent = sum(
            amt
            for txn, amt in ((t, _safe_float(t.get('amount'))) for t in valid_transactions)
            if amt > 0 and not txn.get('pending', False)
        )
        total_income = sum(
            abs(amt)
            for txn, amt in ((t, _safe_float(t.get('amount'))) for t in valid_transactions)
            if amt < 0 and not txn.get('pending', False)
        )

        lines.append("\n📊 Summary:")
        lines.append(f"   Spent: ${total_spent:,.2f}")
        lines.append(f"   Income: ${total_income:,.2f}")
        lines.append(f"   Net: ${total_income - total_spent:,.2f}")

        if skipped_transactions:
            lines.append(
                f"\n⚠️ Skipped {skipped_transactions} malformed transaction record(s)."
            )

        if cache_path:
            lines.append(
                f"\n📁 Saved {len(raw_transactions)} total transaction record(s) to: {cache_path}"
            )
            lines.append(
                "   Use `bash` to open the JSON or load it into pandas for deeper analysis."
            )

        # CRITICAL: Include warning/error message from client if present
        formatted_output = "\n".join(lines)
        if "⚠️" in message or "Warning" in message or "Error" in message:
            formatted_output += f"\n\n{message}"

        return (
            formatted_output,
            {
                "success": True,
                "transaction_count": len(valid_transactions),
                "displayed": len(display_transactions),
                "total_spent": round(total_spent, 2),
                "total_income": round(total_income, 2),
                "transactions": display_transactions,
                "skipped_malformed": skipped_transactions,
                **({"cache_file": str(cache_path)} if cache_path else {}),
                "raw_transaction_count": len(raw_transactions),
            }
        )
    
    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        logger.error(f"L4.financial [tool:get_transactions] - {error_msg}")
        return f"ERROR: {error_msg}", {"success": False, "error": error_msg}


def get_spending_analysis(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> Tuple[str, Dict[str, Any]]:
    """
    Analyze spending breakdown by category.
    
    Provides detailed spending analysis including:
    - Total spending and number of transactions
    - Spending by category with amounts and percentages
    - Top spending categories
    
    Args:
        start_date: Start date in YYYY-MM-DD format (defaults to 30 days ago)
        end_date: End date in YYYY-MM-DD format (defaults to today)
    
    Returns:
        Tuple of (formatted_message, metadata_dict)
    
    Examples:
        # Analyze last 30 days
        get_spending_analysis()
        
        # Analyze specific month
        get_spending_analysis(start_date="2025-01-01", end_date="2025-01-31")
    """
    client, error = _get_client()
    if error:
        logger.error(f"L4.financial [tool:get_spending_analysis] - {error}")
        return f"ERROR: {error}", {"success": False, "error": error}

    try:
        success, message, analysis = client.get_spending_by_category(
            start_date=start_date,
            end_date=end_date
        )

        cache_path = getattr(client, "transactions_file", None)

        if not success:
            return f"ERROR: {message}", {"success": False, "error": message}

        if not analysis or analysis.get('total_spending', 0) == 0:
            period = f"from {start_date or 'last 30 days'} to {end_date or 'today'}"
            return (
                f"No spending found {period}.",
                {
                    "success": True,
                    "total_spending": 0,
                    "categories": [],
                    **({"cache_file": str(cache_path)} if cache_path else {}),
                }
            )
        
        # Format spending analysis
        total = analysis['total_spending']
        num_txns = analysis['num_transactions']
        period = analysis['period']
        categories = analysis['categories']
        
        lines = [
            f"📊 Spending Analysis",
            f"Period: {period['start']} to {period['end']}\n",
            f"Total Spent: ${total:,.2f}",
            f"Transactions: {num_txns}\n",
            f"Breakdown by Category:\n"
        ]

        for i, cat in enumerate(categories[:10], 1):  # Show top 10
            name = cat['name']
            amount = cat['amount']
            pct = cat['percentage']

            # Create a simple bar
            bar_length = int(pct / 5)  # Scale to ~20 chars max
            bar = "█" * bar_length

            lines.append(f"{i:2d}. {name:25s} ${amount:8,.2f} ({pct:5.1f}%) {bar}")

        if len(categories) > 10:
            lines.append(f"\n... and {len(categories) - 10} more categories")

        if cache_path:
            lines.append(f"\n📁 Analysis based on transactions cached at: {cache_path}")

        return (
            "\n".join(lines),
            {
                "success": True,
                "total_spending": total,
                "num_transactions": num_txns,
                "num_categories": len(categories),
                "period": period,
                "categories": categories,
                **({"cache_file": str(cache_path)} if cache_path else {}),
            }
        )
    
    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        logger.error(f"L4.financial [tool:get_spending_analysis] - {error_msg}")
        return f"ERROR: {error_msg}", {"success": False, "error": error_msg}


def disconnect_bank_account(item_id: str) -> Tuple[str, Dict[str, Any]]:
    """
    Disconnect a bank account and revoke Plaid access.
    
    This permanently removes the connection to the specified bank account.
    All locally cached data for this account will be removed, and Plaid
    access will be revoked.
    
    Args:
        item_id: The Plaid item ID to disconnect (from get_connection_status)
    
    Returns:
        Tuple of (message, metadata_dict)
    
    Example:
        disconnect_bank_account(item_id="item_abc123")
    """
    client, error = _get_client()
    if error:
        logger.error(f"L4.financial [tool:disconnect_bank] - {error}")
        return f"ERROR: {error}", {"success": False, "error": error}
    
    try:
        success, message = client.disconnect_account(item_id)
        
        if success:
            return (
                f"✅ {message}\n\n"
                f"The bank connection has been removed and all associated data "
                f"has been deleted from local storage.",
                {"success": True, "action": "bank_disconnected", "item_id": item_id}
            )
        else:
            return f"ERROR: {message}", {"success": False, "error": message}
    
    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        logger.error(f"L4.financial [tool:disconnect_bank] - {error_msg}")
        return f"ERROR: {error_msg}", {"success": False, "error": error_msg}


def get_connection_status() -> Tuple[str, Dict[str, Any]]:
    """
    Get the current Plaid connection status.
    
    Shows:
    - Whether any banks are connected
    - Number of connected items and accounts
    - Connection details for each item
    - Plaid environment (sandbox/development/production)
    
    Returns:
        Tuple of (formatted_message, metadata_dict)
    
    Example:
        get_connection_status()
    """
    client, error = _get_client()
    if error:
        logger.error(f"L4.financial [tool:connection_status] - {error}")
        return f"ERROR: {error}", {"success": False, "error": error}
    
    try:
        status = client.get_connection_status()
        tokens_path = getattr(client, "access_tokens_file", None)
        accounts_path = getattr(client, "accounts_file", None)
        checked_at = datetime.now().isoformat(timespec="seconds")

        if not status['connected']:
            guidance_lines = [
                "🔌 No bank accounts connected.",
                "",
                "Next steps:",
                "• Run `connect_bank_account()` to generate a fresh Plaid Link token and share it with the user.",
                "• Ask the user if they already have a Plaid Link session open or need another invite.",
                "• Once the user finishes linking, call `get_financial_accounts()` to refresh balances.",
                "",
                f"Last checked: {checked_at}",
            ]

            link_message: Optional[str] = None
            link_metadata: Dict[str, Any] = {}
            try:
                followup_msg, followup_meta = connect_bank_account()
                if isinstance(followup_msg, str):
                    link_message = followup_msg
                if isinstance(followup_meta, dict):
                    link_metadata = followup_meta
            except Exception as link_error:
                logger.warning(
                    "L4.financial [tool:connection_status] - Auto-link token creation failed: %s",
                    link_error,
                )
                link_message = (
                    "⚠️ Attempted to auto-generate a Plaid Link token but encountered an error. "
                    "Run `connect_bank_account()` manually to try again."
                )
                link_metadata = {
                    "success": False,
                    "error": str(link_error),
                }

            if link_message:
                guidance_lines.append("")
                guidance_lines.append("Auto-generated Plaid Link invite:")
                guidance_lines.append(link_message)

            metadata: Dict[str, Any] = {
                "success": True,
                "connected": False,
                "status": status,
                "checked_at": checked_at,
                "summary": "No accounts connected. Use connect_bank_account() to get started.",
                "next_actions": [
                    "Share the new Plaid link invite with the user so they can connect an account.",
                    "Confirm with the user whether they want to connect a bank now or later.",
                    "After linking, rerun get_financial_accounts() before pulling transactions.",
                ],
                **({"access_tokens_file": str(tokens_path)} if tokens_path else {}),
                **({"accounts_cache_file": str(accounts_path)} if accounts_path else {}),
            }

            if link_metadata:
                metadata.update({
                    k: v
                    for k, v in link_metadata.items()
                    if k not in metadata or metadata[k] in (None, [], "")
                })

            return ("\n".join(guidance_lines), metadata)

        lines = [
            f"✅ Plaid Connection Status (COMPLETE)\n",
            f"Environment: {status['environment']}",
            f"Connected Items: {status['num_items']}",
            f"Total Accounts: {status['num_accounts']}",
            f"Checked at: {checked_at}\n",
        ]

        if status['items']:
            lines.append("Connected Banks:")
            display_items: List[Dict[str, Any]] = []
            for item in status['items']:
                item_id = item['item_id']
                connected_at_raw = item.get('connected_at', 'Unknown')
                connected_at_display: str
                if isinstance(connected_at_raw, (int, float)):
                    connected_at_display = datetime.fromtimestamp(connected_at_raw).strftime("%Y-%m-%d %H:%M:%S")
                else:
                    connected_at_display = str(connected_at_raw)

                display_items.append({
                    **item,
                    "connected_at_display": connected_at_display,
                })
                lines.append(f"  • {item_id} (connected: {connected_at_display})")
        else:
            display_items = []

        if tokens_path:
            lines.append(f"\n🔐 Vault access tokens file: {tokens_path}")
        if accounts_path:
            lines.append(f"📁 Account cache file: {accounts_path}")
        
        lines.append(f"\n📊 This is the COMPLETE connection status - all {status['num_items']} connections shown above.")
        lines.append("ℹ️ Do NOT call this repeatedly - status only changes when accounts are connected/disconnected.")

        return (
            "\n".join(lines),
            {
                "success": True,
                "connected": True,
                "status": status,
                "checked_at": checked_at,
                "items_display": display_items,
                **({"access_tokens_file": str(tokens_path)} if tokens_path else {}),
                **({"accounts_cache_file": str(accounts_path)} if accounts_path else {}),
            }
        )

    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        logger.error(f"L4.financial [tool:connection_status] - {error_msg}")
        return f"ERROR: {error_msg}", {"success": False, "error": error_msg}


__all__ = [
    "connect_bank_account",
    "get_financial_accounts",
    "get_transactions",
    "get_spending_analysis",
    "disconnect_bank_account",
    "get_connection_status",
]
