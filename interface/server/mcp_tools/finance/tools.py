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

import json
import os
import sys
import time
from typing import Any, Dict

from claude_agent_sdk import tool

from ..registry import register_tool
from .categorizer import (
    categorize_batch,
    format_summary_report,
    add_merchant as _add_merchant,
    override_txn_category as _override_txn_category,
)

# Add scripts directory to path so we can import theo_ports
SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.claude/scripts"))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

# App data directory for the Plaid Link HTML app
APP_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../05_App_Data"))
PLAID_LINK_DIR = os.path.join(APP_DATA_DIR, "plaid-link")


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
    """Get transaction history with Second Brain categorization applied."""
    try:
        tools = _import_financial_tools()
        message, metadata = tools["transactions"](
            start_date=args.get("start_date"),
            end_date=args.get("end_date"),
            account_id=args.get("account_id"),
            limit=args.get("limit", 50)
        )

        # Short-circuit on failure or empty
        if not metadata.get("success"):
            return _result_to_mcp(message, metadata)

        raw_txns = metadata.get("transactions", []) or []
        if not raw_txns:
            return _result_to_mcp(message, metadata)

        # Run Second Brain categorizer
        try:
            batch = categorize_batch(raw_txns)
            summary_report = format_summary_report(
                batch,
                start_date=args.get("start_date"),
                end_date=args.get("end_date"),
            )
            # Prepend the SB summary so Finance reads it first
            enriched_message = (
                "=== SECOND BRAIN CATEGORIZATION ===\n"
                f"{summary_report}\n"
                "===================================\n\n"
                f"{message}"
            )
            # Keep raw metadata + add enriched view
            enriched_meta = dict(metadata)
            enriched_meta["transactions"] = batch["categorized"]
            enriched_meta["sb_summary"] = batch["summary_by_category"]
            enriched_meta["sb_unknown"] = batch["unknown"]
            enriched_meta["sb_alerts"] = batch["alerts"]
            return _result_to_mcp(enriched_message, enriched_meta)
        except Exception as e:
            # Never let categorization failure block the core data
            import traceback
            err = f"⚠️ Categorizer error (raw txns still returned): {e}\n{traceback.format_exc()}"
            return _result_to_mcp(f"{err}\n\n{message}", metadata)

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


def _write_link_token_file(
    link_token: str,
    environment: str = "sandbox",
    mode: str = "new",
    item_id: str | None = None,
    exchange_required: bool = True,
) -> None:
    """Write the Plaid link token to a file the HTML app can read."""
    os.makedirs(PLAID_LINK_DIR, exist_ok=True)
    token_file = os.path.join(PLAID_LINK_DIR, "link_token.json")
    data = {
        "link_token": link_token,
        "generated_at": time.time(),
        "environment": environment,
        "mode": mode,
        "exchange_required": exchange_required,
    }
    if item_id:
        data["item_id"] = item_id
    with open(token_file, "w") as f:
        json.dump(data, f, indent=2)


@register_tool("finance")
@tool(
    name="finance_connect",
    description="""Connect or repair a bank account via Plaid Link.

With no args, generates a new-link token and writes it to the Plaid Link app.
With item_id, generates an update-mode token for that existing Plaid Item.
Tell the user to open the "Link Bank Account" app from the app drawer (or open the
file 05_App_Data/plaid-link/index.html). The app handles the Plaid Link flow.

If called with a public_token, exchanges it for an access token (manual fallback).""",
    input_schema={
        "type": "object",
        "properties": {
            "public_token": {"type": "string", "description": "Public token from Plaid Link (manual fallback only, normally not needed)"},
            "item_id": {"type": "string", "description": "Existing Plaid item_id to repair with Link update mode"}
        }
    }
)
async def finance_connect(args: Dict[str, Any]) -> Dict[str, Any]:
    """Connect a bank account via Plaid."""
    try:
        tools = _import_financial_tools()
        message, metadata = tools["connect"](
            public_token=args.get("public_token"),
            item_id=args.get("item_id"),
        )

        # When a link token is generated (step 1), write it for the HTML app
        if metadata.get("success") and metadata.get("action") == "link_token_created":
            link_token = metadata.get("link_token")
            if link_token:
                env = "sandbox"
                try:
                    env = os.getenv("PLAID_ENV", "sandbox")
                except Exception:
                    pass
                mode = metadata.get("link_mode", "new")
                item_id = metadata.get("item_id")
                exchange_required = bool(metadata.get("exchange_required", mode != "update"))
                _write_link_token_file(
                    link_token,
                    env,
                    mode=mode,
                    item_id=item_id,
                    exchange_required=exchange_required,
                )

                if mode == "update":
                    text = (
                        "Plaid update-mode token generated and written to the app.\n\n"
                        "Tell the user to open the **Link Bank Account** app from the app drawer "
                        "(or open `05_App_Data/plaid-link/index.html`).\n\n"
                        f"The app will repair the existing connection for item `{item_id}` and will not "
                        "exchange a public token after success.\n\n"
                        "If the app is already open, it will auto-detect the new token."
                    )
                else:
                    text = (
                        "Plaid Link token generated and written to the app.\n\n"
                        "Tell the user to open the **Link Bank Account** app from the app drawer "
                        "(or open `05_App_Data/plaid-link/index.html`).\n\n"
                        "The app will handle the bank login flow and automatically "
                        "exchange the token when the user completes it.\n\n"
                        "If the app is already open, it will auto-detect the new token."
                    )

                return {
                    "content": [{"type": "text", "text": text}]
                }

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


@register_tool("finance")
@tool(
    name="finance_add_merchant",
    description="""Add a merchant pattern→category rule to the Second Brain categorizer.

Use this when the user labels an unknown merchant in a daily ping. The rule gets appended
to 20_Areas/finance/merchants.json and takes effect on the next finance_transactions call.

Pattern is a regex (case-insensitive). Keep patterns tight — "walmart" not "wal.*" — to
avoid false matches. Use "|" to combine aliases, e.g. "doordash|door dash".

Confidence levels:
  - "locked": the user has confirmed this is always this category. Never re-ask.
  - "learn":  default is this category but specific txns can be re-tagged. Ask in ping if unsure.""",
    input_schema={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Case-insensitive regex matched against merchant_name (fallback: name)"},
            "category": {"type": "string", "description": "Target category (e.g. 'groceries', 'gas', 'dining_out', 'allowance')"},
            "confidence": {"type": "string", "enum": ["locked", "learn"], "description": "'locked' (never re-ask) or 'learn' (may re-tag individual txns). Default: 'locked'"},
            "note": {"type": "string", "description": "Optional context note stored with the rule"},
            "reimbursable": {"type": "boolean", "description": "Mark txns from this merchant as reimbursable (excluded from caps). Default: false"},
            "alert": {"type": "boolean", "description": "Fire an alert on any match (e.g. for nicotine/weed lapses). Default: false"}
        },
        "required": ["pattern", "category"]
    }
)
async def finance_add_merchant(args: Dict[str, Any]) -> Dict[str, Any]:
    """Append a merchant rule to the categorizer."""
    try:
        entry = _add_merchant(
            pattern=args["pattern"],
            category=args["category"],
            confidence=args.get("confidence", "locked"),
            note=args.get("note"),
            reimbursable=bool(args.get("reimbursable", False)),
            alert=bool(args.get("alert", False)),
        )
        return {
            "content": [{"type": "text", "text": (
                f"✅ Added merchant rule:\n"
                f"  pattern:    {entry['pattern']}\n"
                f"  category:   {entry['category']}\n"
                f"  confidence: {entry.get('confidence', 'locked')}\n"
                + (f"  note:       {entry['note']}\n" if entry.get('note') else "")
                + (f"  reimbursable: True\n" if entry.get('reimbursable') else "")
                + (f"  alert:      True\n" if entry.get('alert') else "")
                + "\nTakes effect on the next finance_transactions call."
            )}]
        }
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Invalid input: {e}"}], "is_error": True}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}], "is_error": True}


@register_tool("finance")
@tool(
    name="finance_override_txn",
    description="""Override the category for a single transaction by its Plaid transaction_id.

Use this when a specific charge needs a different category than its merchant rule would assign.
Example: "this particular Amazon charge was a birthday gift, tag as 'gift' not 'allowance'".

Does NOT modify merchant rules — just records a per-txn override in 20_Areas/finance/txn_overrides.json.
Overrides win over merchant patterns on subsequent reads.

Get the txn_id from the [id=...] field in the UNKNOWN TXNS list or from finance_transactions output.""",
    input_schema={
        "type": "object",
        "properties": {
            "txn_id": {"type": "string", "description": "Plaid transaction_id to override"},
            "category": {"type": "string", "description": "Category to assign (e.g. 'gift', 'reimbursable', 'groceries')"},
            "note": {"type": "string", "description": "Optional context for why this one was re-tagged"},
            "reimbursable": {"type": "boolean", "description": "Mark this specific txn as reimbursable. Default: false"},
            "alert": {"type": "boolean", "description": "Fire an alert on this txn. Default: false"}
        },
        "required": ["txn_id", "category"]
    }
)
async def finance_override_txn(args: Dict[str, Any]) -> Dict[str, Any]:
    """Override the category of a specific transaction."""
    try:
        entry = _override_txn_category(
            txn_id=args["txn_id"],
            new_category=args["category"],
            note=args.get("note"),
            reimbursable=bool(args.get("reimbursable", False)),
            alert=bool(args.get("alert", False)),
        )
        return {
            "content": [{"type": "text", "text": (
                f"✅ Override recorded for txn_id={args['txn_id']}:\n"
                f"  category: {entry['category']}\n"
                + (f"  note:     {entry['note']}\n" if entry.get('note') else "")
                + (f"  reimbursable: True\n" if entry.get('reimbursable') else "")
                + (f"  alert:    True\n" if entry.get('alert') else "")
                + f"  set_at:   {entry['set_at']}\n"
                + "\nTakes effect on the next finance_transactions call."
            )}]
        }
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Invalid input: {e}"}], "is_error": True}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}], "is_error": True}
