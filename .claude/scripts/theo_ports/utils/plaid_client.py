"""
Plaid API Client for Theo

This module handles all interactions with the Plaid API for financial data access.
It provides secure authentication, account linking, transaction fetching, and balance
checking. All credentials are stored in .env, and all user data is stored locally in vault/.

Key Features:
- Link Token generation for secure bank connection
- Access Token management with encryption
- Account and balance retrieval
- Transaction history with categorization
- Error handling with user-friendly messages
- Sandbox/Development/Production environment support

Dependencies: plaid-python, python-dotenv
Storage: vault/financial/
"""

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dotenv import load_dotenv

try:
    from plaid import ApiException
    from plaid.api import plaid_api
    from plaid.model.link_token_create_request import LinkTokenCreateRequest
    from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
    from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
    from plaid.model.accounts_balance_get_request import AccountsBalanceGetRequest
    from plaid.model.transactions_sync_request import TransactionsSyncRequest
    from plaid.model.transactions_get_request import TransactionsGetRequest
    from plaid.model.country_code import CountryCode
    from plaid.model.products import Products
    from plaid.model.item_remove_request import ItemRemoveRequest
    from plaid.configuration import Configuration
    from plaid.api_client import ApiClient
    PLAID_AVAILABLE = True
except ImportError:
    PLAID_AVAILABLE = False

from .theo_logger import cli_logger
from .vault_paths import get_vault_root
from .atomic_file_ops import save_json, load_json

# Use cli_logger for basic logging
logger = cli_logger

# Load environment variables
load_dotenv()


class PlaidClient:
    """
    Client for interacting with the Plaid API.
    
    Handles authentication, bank linking, account access, and transaction retrieval.
    All sensitive data is stored locally in vault/financial/ with encryption.
    """
    
    def __init__(self):
        """Initialize the Plaid client with credentials from .env"""
        
        if not PLAID_AVAILABLE:
            raise ImportError(
                "plaid-python is not installed. Install with: pip install plaid-python"
            )
        
        # Load credentials from environment
        self.client_id = os.getenv("PLAID_CLIENT_ID", "")
        self.secret = os.getenv("PLAID_SECRET", "")
        self.env = os.getenv("PLAID_ENV", "sandbox")
        
        if not self.client_id or not self.secret:
            raise ValueError(
                "Plaid credentials not found. Please set PLAID_CLIENT_ID and PLAID_SECRET in .env file.\n"
                "Get credentials from: https://dashboard.plaid.com/developers/keys"
            )
        
        # Configure Plaid client based on environment
        host_map = {
            "sandbox": "https://sandbox.plaid.com",
            "development": "https://development.plaid.com",
            "production": "https://production.plaid.com"
        }
        
        configuration = Configuration(
            host=host_map.get(self.env, host_map["sandbox"]),
            api_key={
                'clientId': self.client_id,
                'secret': self.secret,
            }
        )
        
        api_client = ApiClient(configuration)
        self.client = plaid_api.PlaidApi(api_client)
        
        # Setup vault paths
        self.vault_root = get_vault_root()
        self.financial_dir = self.vault_root / "financial"
        self.financial_dir.mkdir(parents=True, exist_ok=True)
        
        self.access_tokens_file = self.financial_dir / "access_tokens.json"
        self.accounts_file = self.financial_dir / "accounts.json"
        self.transactions_file = self.financial_dir / "transactions.json"

        try:
            self.account_refresh_seconds = int(
                os.getenv("PLAID_ACCOUNTS_MIN_REFRESH_SECONDS", "60") or 60
            )
        except Exception:
            self.account_refresh_seconds = 60

        logger.info(
            "L4.financial [init] - PlaidClient initialized (env=%s, min_refresh=%ss)",
            self.env,
            self.account_refresh_seconds,
        )
    
    def _load_access_tokens(self) -> Dict[str, Any]:
        """Load stored access tokens from vault."""
        return load_json(self.access_tokens_file, default={}) or {}
    
    def _save_access_tokens(self, tokens: Dict[str, Any]) -> None:
        """Save access tokens to vault."""
        save_json(self.access_tokens_file, tokens)
    
    def _load_accounts(self) -> List[Dict[str, Any]]:
        """Load stored accounts from vault."""
        data = load_json(self.accounts_file, default=[])
        if data and isinstance(data, list):
            return data
        return []
    
    def _save_accounts(self, accounts: List[Dict[str, Any]]) -> None:
        """Save accounts to vault."""
        save_json(self.accounts_file, accounts)
    
    def _load_transactions(self) -> List[Dict[str, Any]]:
        """Load stored transactions from vault."""
        data = load_json(self.transactions_file, default=[])
        if data and isinstance(data, list):
            return data
        return []
    
    def _save_transactions(self, transactions: List[Dict[str, Any]]) -> None:
        """Save transactions to vault."""
        save_json(self.transactions_file, transactions)
    
    def create_link_token(self, user_id: str = "theo_user") -> Tuple[bool, str, Optional[str]]:
        """
        Create a Link token for initiating the Plaid Link flow.
        
        The Link token is used by the frontend to open the Plaid Link interface
        where users securely connect their bank accounts.
        
        Args:
            user_id: Unique identifier for the user (defaults to "theo_user")
        
        Returns:
            Tuple of (success, message, link_token)
        """
        try:
            logger.info(f"L4.financial [tool:connect_bank] - Creating link token for user: {user_id}")
            
            request = LinkTokenCreateRequest(
                user=LinkTokenCreateRequestUser(client_user_id=user_id),
                client_name="Theo AI Assistant",
                products=[Products("transactions")],
                country_codes=[CountryCode("US")],
                language="en",
            )
            
            response = self.client.link_token_create(request)
            link_token = response['link_token']
            
            logger.info(f"L4.financial [tool:connect_bank] - Link token created successfully")
            
            return (
                True,
                "Link token created. Use this token to open Plaid Link and connect your bank.",
                link_token
            )
            
        except ApiException as e:
            error_msg = f"Failed to create link token: {e}"
            logger.error(f"L4.financial [tool:connect_bank] - {error_msg}")
            return False, error_msg, None
        except Exception as e:
            error_msg = f"Unexpected error creating link token: {e}"
            logger.error(f"L4.financial [tool:connect_bank] - {error_msg}")
            return False, error_msg, None
    
    def exchange_public_token(
        self, public_token: str
    ) -> Tuple[bool, str, Optional[str], Optional[str]]:
        """
        Exchange a public token for an access token.
        
        After a user successfully connects their bank through Plaid Link, a public token
        is generated. This method exchanges it for a permanent access token that can be
        used to fetch account data.
        
        Args:
            public_token: The public token from Plaid Link
        
        Returns:
            Tuple of (success, message, access_token, item_id)
        """
        try:
            logger.info("L4.financial [tool:connect_bank] - Exchanging public token for access token")
            
            request = ItemPublicTokenExchangeRequest(public_token=public_token)
            response = self.client.item_public_token_exchange(request)
            
            access_token = response['access_token']
            item_id = response['item_id']
            
            # Store the access token
            tokens = self._load_access_tokens()
            tokens[item_id] = {
                "access_token": access_token,
                "item_id": item_id,
                "created_at": time.time(),
            }
            self._save_access_tokens(tokens)
            
            logger.info(f"L4.financial [tool:connect_bank] - Access token stored for item: {item_id}")
            
            # Immediately fetch and cache accounts
            self._fetch_and_cache_accounts(access_token, item_id)
            
            return (
                True,
                f"Bank account connected successfully (item_id: {item_id})",
                access_token,
                item_id,
            )
            
        except ApiException as e:
            error_msg = f"Failed to exchange public token: {e}"
            logger.error(f"L4.financial [tool:connect_bank] - {error_msg}")
            return False, error_msg, None, None
        except Exception as e:
            error_msg = f"Unexpected error exchanging token: {e}"
            logger.error(f"L4.financial [tool:connect_bank] - {error_msg}")
            return False, error_msg, None, None
    
    def _fetch_and_cache_accounts(self, access_token: str, item_id: str) -> None:
        """Internal helper to fetch and cache account information."""
        try:
            request = AccountsBalanceGetRequest(access_token=access_token)
            response = self.client.accounts_balance_get(request)
            
            accounts = self._load_accounts()
            
            # Add or update accounts for this item
            now = time.time()
            for account in response['accounts']:
                balances = account.get('balances', {}) if isinstance(account, dict) else {}
                account_data = {
                    "account_id": account['account_id'],
                    "item_id": item_id,
                    "name": account['name'],
                    "official_name": account.get('official_name'),
                    "type": account['type'],
                    "subtype": account['subtype'],
                    "mask": account.get('mask'),
                    "balance_current": balances.get('current'),
                    "balance_available": balances.get('available'),
                    "balance_limit": balances.get('limit'),
                    "currency": balances.get('iso_currency_code', 'USD'),
                    "last_updated": now,
                }
                
                # Update or append
                existing_idx = next(
                    (i for i, a in enumerate(accounts) if a['account_id'] == account_data['account_id']),
                    None
                )
                if existing_idx is not None:
                    accounts[existing_idx] = account_data
                else:
                    accounts.append(account_data)
            
            self._save_accounts(accounts)
            logger.info(f"L4.financial [cache] - Cached {len(response['accounts'])} accounts for item {item_id}")
            
        except Exception as e:
            logger.error(f"L4.financial [cache] - Failed to fetch accounts: {e}")
    
    def get_accounts(self) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        Get all connected accounts with current balances, using cached data when
        Plaid rate limits are in effect or a recent fetch is still fresh.

        Returns:
            Tuple of (success flag, human-readable message, account list)
        """
        try:
            tokens = self._load_access_tokens()

            if not tokens:
                return True, "No bank accounts connected yet.", []

            cached_accounts = [
                acct for acct in self._load_accounts() if isinstance(acct, dict)
            ]
            cache_by_item: Dict[str, List[Dict[str, Any]]] = {}
            for acct in cached_accounts:
                cache_by_item.setdefault(acct.get("item_id"), []).append(acct)

            all_accounts: List[Dict[str, Any]] = []
            failed_items: List[Dict[str, Any]] = []
            fresh_items: Dict[str, List[Dict[str, Any]]] = {}
            cache_hits: Dict[str, float] = {}
            cache_fallbacks: Dict[str, float] = {}

            now = time.time()

            for item_id, token_data in tokens.items():
                access_token = token_data['access_token']
                cached_list = cache_by_item.get(item_id, [])
                last_cached = max(
                    (acct.get('last_updated', 0) for acct in cached_list if isinstance(acct, dict)),
                    default=0,
                )

                if cached_list and now - last_cached < self.account_refresh_seconds:
                    all_accounts.extend(cached_list)
                    cache_hits[item_id] = last_cached
                    continue

                try:
                    request = AccountsBalanceGetRequest(access_token=access_token)
                    response = self.client.accounts_balance_get(request)

                    refreshed_accounts: List[Dict[str, Any]] = []
                    for account in response['accounts']:
                        balances = account['balances']
                        refreshed_accounts.append({
                            "account_id": account['account_id'],
                            "item_id": item_id,
                            "name": account['name'],
                            "official_name": account.get('official_name'),
                            "type": account['type'],
                            "subtype": account['subtype'],
                            "mask": account.get('mask'),
                            "balance_current": balances.get('current'),
                            "balance_available": balances.get('available'),
                            "balance_limit": balances.get('limit'),
                            "currency": balances.get('iso_currency_code', 'USD'),
                            "last_updated": now,
                        })

                    all_accounts.extend(refreshed_accounts)
                    fresh_items[item_id] = refreshed_accounts

                except ApiException as e:
                    error_code = "UNKNOWN"
                    error_msg = str(e)

                    try:
                        if hasattr(e, 'body') and e.body:
                            error_data = json.loads(e.body) if isinstance(e.body, str) else e.body
                            error_code = error_data.get('error_code', 'UNKNOWN')
                            error_msg = error_data.get('error_message', str(e))
                    except Exception:
                        pass

                    logger.error(
                        "L4.financial [tool:get_accounts] - Failed for item %s: %s - %s",
                        item_id,
                        error_code,
                        error_msg,
                    )

                    if cached_list:
                        all_accounts.extend(cached_list)
                        cache_fallbacks[item_id] = last_cached
                        logger.info(
                            "L4.financial [tool:get_accounts] - Using cached balances for %s after %s",
                            item_id,
                            error_code,
                        )
                    else:
                        failed_items.append({
                            "item_id": item_id,
                            "error_code": error_code,
                            "error_message": error_msg,
                        })

            if fresh_items:
                refreshed_ids = set(fresh_items.keys())
                updated_accounts: List[Dict[str, Any]] = [
                    acct for acct in cached_accounts if acct.get('item_id') not in refreshed_ids
                ]
                for acct_list in fresh_items.values():
                    updated_accounts.extend(acct_list)
                self._save_accounts(updated_accounts)

            if not all_accounts and failed_items:
                error_summary = "; ".join([
                    f"{item['error_code']}: {item['error_message']}"
                    for item in failed_items
                ])
                return False, f"Failed to retrieve accounts: {error_summary}", []

            timestamp = datetime.now().strftime("%H:%M:%S")
            cache_descriptions: List[str] = []
            if cache_hits:
                newest = max(cache_hits.values())
                cache_descriptions.append(
                    f"cached {len(cache_hits)} item(s) refreshed at {datetime.fromtimestamp(newest).strftime('%H:%M:%S')}"
                )
            if cache_fallbacks:
                cache_descriptions.append("fallback to cached data after Plaid warnings")

            if failed_items:
                failed_ids = ", ".join([item['item_id'][:16] + "..." for item in failed_items[:3]])
                warning = (
                    f"⚠️ Warning: {len(failed_items)} connection(s) failed (items: {failed_ids}). "
                    f"See logs for details [checked at {timestamp}]"
                )
                message = f"Found {len(all_accounts)} account(s). {warning}"
            else:
                message = f"Found {len(all_accounts)} account(s)"

            if cache_descriptions:
                message += " (" + "; ".join(cache_descriptions) + ")"

            logger.info(
                "L4.financial [tool:get_accounts] - Accounts fetched (fresh=%d, cache_hits=%d, fallbacks=%d, failures=%d)",
                len(fresh_items),
                len(cache_hits),
                len(cache_fallbacks),
                len(failed_items),
            )

            return True, message, all_accounts

        except Exception as e:
            error_msg = f"Error retrieving accounts: {e}"
            logger.error(f"L4.financial [tool:get_accounts] - {error_msg}")
            return False, error_msg, []
    
    def get_transactions(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        account_ids: Optional[List[str]] = None,
        count: int = 100
    ) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        Get transactions for connected accounts.
        
        Args:
            start_date: Start date in YYYY-MM-DD format (defaults to 30 days ago)
            end_date: End date in YYYY-MM-DD format (defaults to today)
            account_ids: Optional list of specific account IDs to filter
            count: Maximum number of transactions to retrieve (default 100, max 500)
        
        Returns:
            Tuple of (success, message, transactions_list)
        """
        try:
            # Default date range: last 30 days
            if not start_date:
                start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
            if not end_date:
                end_date = datetime.now().strftime("%Y-%m-%d")

            tokens = self._load_access_tokens()

            if not tokens:
                return True, "No bank accounts connected yet.", []

            # Build lookup so we can map an account_id to its Plaid item.
            account_records = self._load_accounts()
            account_to_item = {
                record["account_id"]: record.get("item_id")
                for record in account_records
                if isinstance(record, dict) and record.get("account_id") and record.get("item_id")
            }

            # Sanitize requested account IDs (if any) so Plaid never sees None/empty strings.
            sanitized_account_ids: List[str] = []
            if account_ids:
                for acct in account_ids:
                    if isinstance(acct, str) and acct.strip():
                        sanitized_account_ids.append(acct.strip())

            if sanitized_account_ids:
                unknown_accounts = [acct for acct in sanitized_account_ids if acct not in account_to_item]
                if unknown_accounts:
                    return False, (
                        "Unknown account_id(s): " + ", ".join(sorted(set(unknown_accounts)))
                    ), []

            all_transactions = []
            failed_items = []
            matched_account_ids: set[str] = set()

            for item_id, token_data in tokens.items():
                access_token = token_data['access_token']

                # Determine which account IDs apply to this item (if any were provided).
                item_account_ids: Optional[List[str]] = None
                if sanitized_account_ids:
                    filtered_ids = [
                        acct for acct in sanitized_account_ids if account_to_item.get(acct) == item_id
                    ]
                    if not filtered_ids:
                        # User requested specific accounts that belong to other items; skip this one quietly.
                        continue
                    item_account_ids = filtered_ids
                    matched_account_ids.update(filtered_ids)

                try:
                    options: Dict[str, Any] = {"count": min(count, 500)}
                    if item_account_ids:
                        options["account_ids"] = item_account_ids
                    
                    request = TransactionsGetRequest(
                        access_token=access_token,
                        start_date=datetime.strptime(start_date, "%Y-%m-%d").date(),
                        end_date=datetime.strptime(end_date, "%Y-%m-%d").date(),
                        options=options
                    )
                    
                    response = self.client.transactions_get(request)
                    
                    for txn in response['transactions']:
                        all_transactions.append({
                            "transaction_id": txn['transaction_id'],
                            "account_id": txn['account_id'],
                            "item_id": item_id,
                            "date": str(txn['date']),
                            "authorized_date": str(txn.get('authorized_date')) if txn.get('authorized_date') else None,
                            "name": txn['name'],
                            "merchant_name": txn.get('merchant_name'),
                            "amount": float(txn['amount']),
                            "currency": txn.get('iso_currency_code', 'USD'),
                            "category": txn.get('category', []),
                            "category_id": txn.get('category_id'),
                            "pending": txn.get('pending', False),
                            "payment_channel": txn.get('payment_channel'),
                            "location": {
                                "city": txn.get('location', {}).get('city'),
                                "state": txn.get('location', {}).get('region'),
                            } if txn.get('location') else None,
                        })
                    
                    logger.info(
                        f"L4.financial [tool:get_transactions] - "
                        f"Retrieved {len(response['transactions'])} transactions for item {item_id}"
                    )
                    
                except ApiException as e:
                    # Parse error details from the exception
                    error_code = "UNKNOWN"
                    error_msg = str(e)
                    
                    # Try to extract structured error info
                    try:
                        if hasattr(e, 'body') and e.body:
                            error_data = json.loads(e.body) if isinstance(e.body, str) else e.body
                            error_code = error_data.get('error_code', 'UNKNOWN')
                            error_msg = error_data.get('error_message', str(e))
                    except:
                        pass
                    
                    logger.error(
                        f"L4.financial [tool:get_transactions] - Failed for item {item_id}: "
                        f"{error_code} - {error_msg}"
                    )
                    
                    failed_items.append({
                        "item_id": item_id,
                        "error_code": error_code,
                        "error_message": error_msg
                    })
                    continue
            
            if sanitized_account_ids:
                unmatched_accounts = sorted(set(sanitized_account_ids) - matched_account_ids)
                if unmatched_accounts:
                    return False, (
                        "Requested account_id(s) are not currently connected: "
                        + ", ".join(unmatched_accounts)
                    ), []

            # Sort by date (most recent first)
            all_transactions.sort(key=lambda x: x['date'], reverse=True)
            
            # Cache transactions
            self._save_transactions(all_transactions)
            
            # Build result message
            if not all_transactions and failed_items:
                # All items failed
                error_summary = "; ".join([
                    f"{item['error_code']}: {item['error_message']}"
                    for item in failed_items
                ])
                return False, f"Failed to retrieve transactions: {error_summary}", []
            
            elif failed_items:
                # Partial success
                # Include item IDs and timestamp to make error unique (prevents deduplication)
                failed_ids = ", ".join([item['item_id'][:16] + "..." for item in failed_items[:3]])
                timestamp = datetime.now().strftime("%H:%M:%S")
                warning = (
                    f"⚠️ Warning: {len(failed_items)} connection(s) failed (items: {failed_ids}). "
                    f"Error: {failed_items[0]['error_code']} - {failed_items[0]['error_message']} "
                    f"[checked at {timestamp}]"
                )
                msg = f"Retrieved {len(all_transactions)} transaction(s) from {start_date} to {end_date}. {warning}"
                logger.info(f"L4.financial [tool:get_transactions] - {msg} (with {len(failed_items)} failures)")
                return True, msg, all_transactions
            
            else:
                # Complete success
                msg = f"Retrieved {len(all_transactions)} transaction(s) from {start_date} to {end_date}"
                logger.info(f"L4.financial [tool:get_transactions] - {msg}")
                return True, msg, all_transactions
            
        except Exception as e:
            error_msg = f"Error retrieving transactions: {e}"
            logger.error(f"L4.financial [tool:get_transactions] - {error_msg}")
            return False, error_msg, []
    
    def get_spending_by_category(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Analyze spending by category.
        
        Args:
            start_date: Start date in YYYY-MM-DD format (defaults to 30 days ago)
            end_date: End date in YYYY-MM-DD format (defaults to today)
        
        Returns:
            Tuple of (success, message, category_breakdown)
        """
        try:
            success, msg, transactions = self.get_transactions(start_date, end_date)
            
            if not success:
                return False, msg, {}
            
            if not transactions:
                return True, "No transactions found in this period.", {}
            
            # Aggregate by category
            category_totals: Dict[str, float] = {}
            total_spending = 0.0
            
            for txn in transactions:
                # Only count positive amounts (expenses, not income)
                if txn['amount'] > 0 and not txn['pending']:
                    amount = txn['amount']
                    categories = txn.get('category', ['Uncategorized'])
                    
                    # Use the first (most specific) category
                    category = categories[0] if categories else 'Uncategorized'
                    
                    category_totals[category] = category_totals.get(category, 0.0) + amount
                    total_spending += amount
            
            # Sort by amount
            sorted_categories = sorted(
                category_totals.items(),
                key=lambda x: x[1],
                reverse=True
            )
            
            result = {
                "total_spending": round(total_spending, 2),
                "num_transactions": len([t for t in transactions if t['amount'] > 0 and not t['pending']]),
                "period": {
                    "start": start_date or (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"),
                    "end": end_date or datetime.now().strftime("%Y-%m-%d"),
                },
                "categories": [
                    {
                        "name": cat,
                        "amount": round(amt, 2),
                        "percentage": round((amt / total_spending * 100) if total_spending > 0 else 0, 1)
                    }
                    for cat, amt in sorted_categories
                ]
            }
            
            logger.info(
                f"L4.financial [tool:get_spending_by_category] - "
                f"Analyzed {len(sorted_categories)} categories, ${total_spending:.2f} total"
            )
            
            return True, f"Analyzed spending across {len(sorted_categories)} categories", result
            
        except Exception as e:
            error_msg = f"Error analyzing spending: {e}"
            logger.error(f"L4.financial [tool:get_spending_by_category] - {error_msg}")
            return False, error_msg, {}
    
    def disconnect_account(self, item_id: str) -> Tuple[bool, str]:
        """
        Disconnect a bank account and revoke access.
        
        Args:
            item_id: The Plaid item ID to disconnect
        
        Returns:
            Tuple of (success, message)
        """
        try:
            tokens = self._load_access_tokens()
            
            if item_id not in tokens:
                return False, f"Item {item_id} not found in connected accounts."
            
            access_token = tokens[item_id]['access_token']
            
            # Remove from Plaid
            request = ItemRemoveRequest(access_token=access_token)
            self.client.item_remove(request)
            
            # Remove from local storage
            del tokens[item_id]
            self._save_access_tokens(tokens)
            
            # Remove associated accounts
            accounts = self._load_accounts()
            accounts = [a for a in accounts if a['item_id'] != item_id]
            self._save_accounts(accounts)
            
            logger.info(f"L4.financial [tool:disconnect_account] - Disconnected item {item_id}")
            
            return True, f"Successfully disconnected bank account (item_id: {item_id})"
            
        except ApiException as e:
            error_msg = f"Failed to disconnect account: {e}"
            logger.error(f"L4.financial [tool:disconnect_account] - {error_msg}")
            return False, error_msg
        except Exception as e:
            error_msg = f"Unexpected error disconnecting account: {e}"
            logger.error(f"L4.financial [tool:disconnect_account] - {error_msg}")
            return False, error_msg
    
    def get_connection_status(self) -> Dict[str, Any]:
        """
        Get the current connection status of all linked accounts.
        
        Returns:
            Dict with connection information
        """
        tokens = self._load_access_tokens()
        accounts = self._load_accounts()
        
        return {
            "connected": len(tokens) > 0,
            "num_items": len(tokens),
            "num_accounts": len(accounts),
            "items": [
                {
                    "item_id": item_id,
                    "connected_at": token_data.get('created_at'),
                }
                for item_id, token_data in tokens.items()
            ],
            "environment": self.env,
        }


# Global client instance (lazy initialization)
_plaid_client: Optional[PlaidClient] = None


def get_plaid_client() -> PlaidClient:
    """Get or create the global Plaid client instance."""
    global _plaid_client
    if _plaid_client is None:
        _plaid_client = PlaidClient()
    return _plaid_client


__all__ = [
    "PlaidClient",
    "get_plaid_client",
]
