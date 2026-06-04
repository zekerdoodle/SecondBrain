"""
SnapTrade API Client

Handles all interactions with the SnapTrade API for investment account access.
Provides brokerage connection, account data retrieval, position tracking,
and order management for Fidelity, Schwab, and other supported brokerages.

Key Features:
- User registration and brokerage connection via OAuth
- Account listing with balances
- Position/holdings retrieval
- Order history and status
- Account return rates / performance
- Trading support (validate + execute pattern)
- Connection health monitoring

Dependencies: snaptrade-python-sdk, python-dotenv
Storage: vault/financial/snaptrade/
"""

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dotenv import load_dotenv

SnapTrade = None
ApiException = Exception
SNAPTRADE_AVAILABLE = False


def _ensure_snaptrade_sdk() -> None:
    """Import the SnapTrade SDK lazily so installs can heal a live process."""
    global SnapTrade, ApiException, SNAPTRADE_AVAILABLE

    if SNAPTRADE_AVAILABLE and SnapTrade is not None:
        return

    try:
        from snaptrade_client import SnapTrade as _SnapTrade
        from snaptrade_client.exceptions import ApiException as _ApiException
    except ImportError as exc:
        SNAPTRADE_AVAILABLE = False
        raise ImportError(
            "snaptrade-python-sdk is not installed. Install with: "
            "pip install snaptrade-python-sdk"
        ) from exc

    SnapTrade = _SnapTrade
    ApiException = _ApiException
    SNAPTRADE_AVAILABLE = True

from .theo_logger import cli_logger
from .vault_paths import get_vault_root
from .atomic_file_ops import save_json, load_json

logger = cli_logger

# Load environment variables
load_dotenv()


class SnapTradeClient:
    """
    Client for interacting with the SnapTrade API.

    Handles authentication, brokerage connection, account access,
    position tracking, and order management. User credentials are
    stored locally in vault/financial/snaptrade/.
    """

    def __init__(self):
        """Initialize the SnapTrade client with credentials from .env"""

        _ensure_snaptrade_sdk()

        # Load credentials from environment
        self.client_id = os.getenv("SNAPTRADE_CLIENT_ID", "")
        self.consumer_key = os.getenv("SNAPTRADE_CONSUMER_KEY", "")

        if not self.client_id or not self.consumer_key:
            raise ValueError(
                "SnapTrade credentials not found. Please set SNAPTRADE_CLIENT_ID and "
                "SNAPTRADE_CONSUMER_KEY in .env file.\n"
                "Get credentials from: https://dashboard.snaptrade.com/"
            )

        # Initialize the SDK client
        self.client = SnapTrade(
            consumer_key=self.consumer_key,
            client_id=self.client_id,
        )

        # Setup vault paths
        self.vault_root = get_vault_root()
        self.snaptrade_dir = self.vault_root / "financial" / "snaptrade"
        self.snaptrade_dir.mkdir(parents=True, exist_ok=True)

        self.credentials_file = self.snaptrade_dir / "credentials.json"
        self.accounts_cache_file = self.snaptrade_dir / "accounts_cache.json"
        self.holdings_cache_file = self.snaptrade_dir / "holdings_cache.json"

        try:
            self.cache_ttl_seconds = int(
                os.getenv("SNAPTRADE_CACHE_TTL_SECONDS", "120") or 120
            )
        except Exception:
            self.cache_ttl_seconds = 120

        logger.info(
            "L4.financial [init] - SnapTradeClient initialized (cache_ttl=%ss)",
            self.cache_ttl_seconds,
        )

    # =========================================================================
    # Credential Management
    # =========================================================================

    def _load_credentials(self) -> Dict[str, Any]:
        """Load stored SnapTrade user credentials from vault."""
        return load_json(self.credentials_file, default={}) or {}

    def _save_credentials(self, creds: Dict[str, Any]) -> None:
        """Save SnapTrade user credentials to vault."""
        save_json(self.credentials_file, creds)

    def _get_user_credentials(self) -> Tuple[Optional[str], Optional[str]]:
        """Get user_id and user_secret from stored credentials."""
        creds = self._load_credentials()
        return creds.get("user_id"), creds.get("user_secret")

    def _require_credentials(self) -> Tuple[str, str]:
        """Get credentials or raise an error."""
        user_id, user_secret = self._get_user_credentials()
        if not user_id or not user_secret:
            raise ValueError(
                "SnapTrade user not registered. Run snaptrade_register first to "
                "create a user and obtain credentials."
            )
        return user_id, user_secret

    # =========================================================================
    # Cache Management
    # =========================================================================

    def _load_cache(self, cache_file: Path) -> Dict[str, Any]:
        """Load cached data if still fresh."""
        data = load_json(cache_file, default={})
        if not data:
            return {}
        cached_at = data.get("cached_at", 0)
        if time.time() - cached_at > self.cache_ttl_seconds:
            return {}  # Expired
        return data

    def _save_cache(self, cache_file: Path, data: Dict[str, Any]) -> None:
        """Save data to cache with timestamp."""
        data["cached_at"] = time.time()
        save_json(cache_file, data)

    # =========================================================================
    # Authentication & Registration
    # =========================================================================

    def register_user(self, user_id: str = "user") -> Tuple[bool, str, Dict[str, Any]]:
        """
        Register a new SnapTrade user (one-time setup).

        Args:
            user_id: Unique identifier for the user

        Returns:
            Tuple of (success, message, metadata)
        """
        try:
            # Check if already registered
            existing_id, existing_secret = self._get_user_credentials()
            if existing_id and existing_secret:
                return (
                    True,
                    f"User '{existing_id}' already registered. Use snaptrade_connect to link brokerages.",
                    {"user_id": existing_id, "already_registered": True},
                )

            logger.info("L4.snaptrade [register] - Registering user: %s", user_id)

            response = self.client.authentication.register_snap_trade_user(
                user_id=user_id,
            )

            result = response.body
            user_secret = result.get("userSecret", "")

            # Store credentials
            self._save_credentials({
                "user_id": user_id,
                "user_secret": user_secret,
                "registered_at": time.time(),
            })

            logger.info("L4.snaptrade [register] - User registered successfully")

            return (
                True,
                f"User '{user_id}' registered successfully. Credentials stored in vault.",
                {"user_id": user_id, "has_secret": bool(user_secret)},
            )

        except ApiException as e:
            error_msg = f"Failed to register user: {e}"
            logger.error("L4.snaptrade [register] - %s", error_msg)
            return False, error_msg, {"error": str(e)}
        except Exception as e:
            error_msg = f"Unexpected error registering user: {e}"
            logger.error("L4.snaptrade [register] - %s", error_msg)
            return False, error_msg, {"error": str(e)}

    def generate_connect_url(
        self, broker: Optional[str] = None
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Generate a URL for the user to connect a brokerage account.

        Args:
            broker: Optional broker slug to pre-select (e.g., "FIDELITY", "SCHWAB")

        Returns:
            Tuple of (success, message, metadata with redirect_url)
        """
        try:
            user_id, user_secret = self._require_credentials()

            logger.info(
                "L4.snaptrade [connect] - Generating connect URL (broker=%s)",
                broker or "any",
            )

            kwargs = {
                "user_id": user_id,
                "user_secret": user_secret,
            }
            if broker:
                kwargs["broker"] = broker

            response = self.client.authentication.login_snap_trade_user(**kwargs)
            result = response.body

            # The response contains a redirectURI
            redirect_url = ""
            if isinstance(result, dict):
                redirect_url = result.get("redirectURI", "") or result.get("loginLink", "")
            elif hasattr(result, "redirect_uri"):
                redirect_url = result.redirect_uri
            # Sometimes it's at the top level
            if not redirect_url and hasattr(response, 'body'):
                body = response.body
                if isinstance(body, dict):
                    redirect_url = body.get("redirectURI", "") or body.get("loginLink", "")

            if not redirect_url:
                return (
                    False,
                    "Failed to get redirect URL from SnapTrade. Response may have changed.",
                    {"response": str(result)},
                )

            return (
                True,
                f"Open this URL in your browser to connect your brokerage:\n{redirect_url}",
                {
                    "redirect_url": redirect_url,
                    "broker": broker,
                },
            )

        except ValueError as e:
            return False, str(e), {"error": str(e)}
        except ApiException as e:
            error_msg = f"Failed to generate connect URL: {e}"
            logger.error("L4.snaptrade [connect] - %s", error_msg)
            return False, error_msg, {"error": str(e)}
        except Exception as e:
            error_msg = f"Unexpected error generating connect URL: {e}"
            logger.error("L4.snaptrade [connect] - %s", error_msg)
            return False, error_msg, {"error": str(e)}

    # =========================================================================
    # Account Information
    # =========================================================================

    def list_accounts(self) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        List all connected investment accounts.

        Returns:
            Tuple of (success, message, accounts_list)
        """
        try:
            user_id, user_secret = self._require_credentials()

            # Check cache
            cached = self._load_cache(self.accounts_cache_file)
            if cached and cached.get("accounts"):
                accounts = cached["accounts"]
                cached_at = datetime.fromtimestamp(cached["cached_at"]).strftime("%H:%M:%S")
                logger.info(
                    "L4.snaptrade [accounts] - Returning %d cached accounts (from %s)",
                    len(accounts), cached_at,
                )
                return (
                    True,
                    f"Found {len(accounts)} account(s) (cached at {cached_at})",
                    accounts,
                )

            logger.info("L4.snaptrade [accounts] - Fetching accounts from API")

            response = self.client.account_information.list_user_accounts(
                user_id=user_id,
                user_secret=user_secret,
            )

            raw_accounts = response.body if response.body else []
            accounts = []

            for acct in raw_accounts:
                acct_dict = acct if isinstance(acct, dict) else dict(acct)
                accounts.append({
                    "account_id": acct_dict.get("id", ""),
                    "brokerage_authorization_id": acct_dict.get("brokerage_authorization", {}).get("id", "") if isinstance(acct_dict.get("brokerage_authorization"), dict) else str(acct_dict.get("brokerage_authorization", "")),
                    "name": acct_dict.get("name", "Unknown"),
                    "number": acct_dict.get("number", ""),
                    "institution_name": acct_dict.get("institution_name", ""),
                    "sync_status": acct_dict.get("sync_status", {}) if isinstance(acct_dict.get("sync_status"), dict) else {},
                    "meta": acct_dict.get("meta", {}),
                    "raw": acct_dict,
                })

            # Cache the results
            self._save_cache(self.accounts_cache_file, {"accounts": accounts})

            logger.info("L4.snaptrade [accounts] - Found %d accounts", len(accounts))

            return (
                True,
                f"Found {len(accounts)} investment account(s)",
                accounts,
            )

        except ValueError as e:
            return False, str(e), []
        except ApiException as e:
            error_msg = f"Failed to list accounts: {e}"
            logger.error("L4.snaptrade [accounts] - %s", error_msg)
            return False, error_msg, []
        except Exception as e:
            error_msg = f"Unexpected error listing accounts: {e}"
            logger.error("L4.snaptrade [accounts] - %s", error_msg)
            return False, error_msg, []

    def get_account_balances(
        self, account_id: str
    ) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        Get balances for a specific account.

        Args:
            account_id: The SnapTrade account ID

        Returns:
            Tuple of (success, message, balances_list)
        """
        try:
            user_id, user_secret = self._require_credentials()

            logger.info("L4.snaptrade [balances] - Fetching balances for %s", account_id)

            response = self.client.account_information.get_user_account_balance(
                user_id=user_id,
                user_secret=user_secret,
                account_id=account_id,
            )

            raw_balances = response.body if response.body else []
            balances = []

            for bal in raw_balances:
                bal_dict = bal if isinstance(bal, dict) else dict(bal)
                balances.append({
                    "currency": bal_dict.get("currency", {}).get("code", "USD") if isinstance(bal_dict.get("currency"), dict) else str(bal_dict.get("currency", "USD")),
                    "cash": bal_dict.get("cash"),
                    "buying_power": bal_dict.get("buying_power"),
                    "raw": bal_dict,
                })

            return (
                True,
                f"Retrieved balances for account {account_id}",
                balances,
            )

        except ValueError as e:
            return False, str(e), []
        except ApiException as e:
            error_msg = f"Failed to get balances: {e}"
            logger.error("L4.snaptrade [balances] - %s", error_msg)
            return False, error_msg, []
        except Exception as e:
            error_msg = f"Unexpected error getting balances: {e}"
            logger.error("L4.snaptrade [balances] - %s", error_msg)
            return False, error_msg, []

    def get_account_positions(
        self, account_id: str
    ) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        Get positions/holdings for a specific account.

        Args:
            account_id: The SnapTrade account ID

        Returns:
            Tuple of (success, message, positions_list)
        """
        try:
            user_id, user_secret = self._require_credentials()

            logger.info("L4.snaptrade [positions] - Fetching positions for %s", account_id)

            response = self.client.account_information.get_user_account_positions(
                user_id=user_id,
                user_secret=user_secret,
                account_id=account_id,
            )

            raw_positions = response.body if response.body else []
            positions = []

            for pos in raw_positions:
                pos_dict = pos if isinstance(pos, dict) else dict(pos)
                symbol_info = pos_dict.get("symbol", {})
                if isinstance(symbol_info, dict):
                    symbol_name = symbol_info.get("symbol", {}).get("symbol", "") if isinstance(symbol_info.get("symbol"), dict) else str(symbol_info.get("symbol", ""))
                    symbol_description = symbol_info.get("description", "")
                else:
                    symbol_name = str(symbol_info)
                    symbol_description = ""

                positions.append({
                    "symbol": symbol_name,
                    "description": symbol_description,
                    "units": pos_dict.get("units"),
                    "price": pos_dict.get("price"),
                    "open_pnl": pos_dict.get("open_pnl"),
                    "average_purchase_price": pos_dict.get("average_purchase_price"),
                    "fractional_units": pos_dict.get("fractional_units"),
                    "raw": pos_dict,
                })

            # Cache holdings
            cached_holdings = load_json(self.holdings_cache_file, default={}) or {}
            cached_holdings[account_id] = {
                "positions": positions,
                "cached_at": time.time(),
            }
            save_json(self.holdings_cache_file, cached_holdings)

            logger.info(
                "L4.snaptrade [positions] - Found %d positions for %s",
                len(positions), account_id,
            )

            return (
                True,
                f"Found {len(positions)} position(s) in account {account_id}",
                positions,
            )

        except ValueError as e:
            return False, str(e), []
        except ApiException as e:
            error_msg = f"Failed to get positions: {e}"
            logger.error("L4.snaptrade [positions] - %s", error_msg)
            return False, error_msg, []
        except Exception as e:
            error_msg = f"Unexpected error getting positions: {e}"
            logger.error("L4.snaptrade [positions] - %s", error_msg)
            return False, error_msg, []

    def get_all_holdings(self) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        Get all holdings across all accounts.

        Returns:
            Tuple of (success, message, holdings_list)
        """
        try:
            user_id, user_secret = self._require_credentials()

            logger.info("L4.snaptrade [holdings] - Fetching all holdings")

            response = self.client.account_information.get_all_user_holdings(
                user_id=user_id,
                user_secret=user_secret,
            )

            raw_holdings = response.body if response.body else []
            holdings = []

            for holding in raw_holdings:
                h_dict = holding if isinstance(holding, dict) else dict(holding)
                holdings.append(h_dict)

            logger.info("L4.snaptrade [holdings] - Found %d holding groups", len(holdings))

            return (
                True,
                f"Retrieved holdings across all accounts",
                holdings,
            )

        except ValueError as e:
            return False, str(e), []
        except ApiException as e:
            error_msg = f"Failed to get holdings: {e}"
            logger.error("L4.snaptrade [holdings] - %s", error_msg)
            return False, error_msg, []
        except Exception as e:
            error_msg = f"Unexpected error getting holdings: {e}"
            logger.error("L4.snaptrade [holdings] - %s", error_msg)
            return False, error_msg, []

    # =========================================================================
    # Orders & Activities
    # =========================================================================

    def get_account_orders(
        self,
        account_id: str,
        state: Optional[str] = None,
        days: int = 30,
    ) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        Get orders for a specific account.

        Args:
            account_id: The SnapTrade account ID
            state: Filter by state (e.g., "Executed", "Cancelled")
            days: Number of days to look back (default 30)

        Returns:
            Tuple of (success, message, orders_list)
        """
        try:
            user_id, user_secret = self._require_credentials()

            logger.info(
                "L4.snaptrade [orders] - Fetching orders for %s (days=%d, state=%s)",
                account_id, days, state or "all",
            )

            kwargs = {
                "user_id": user_id,
                "user_secret": user_secret,
                "account_id": account_id,
                "days": days,
            }
            if state:
                kwargs["state"] = state

            response = self.client.account_information.get_user_account_orders(**kwargs)

            raw_orders = response.body if response.body else []
            orders = []

            for order in raw_orders:
                o_dict = order if isinstance(order, dict) else dict(order)
                orders.append(o_dict)

            logger.info(
                "L4.snaptrade [orders] - Found %d orders for %s",
                len(orders), account_id,
            )

            return (
                True,
                f"Found {len(orders)} order(s) for account {account_id}",
                orders,
            )

        except ValueError as e:
            return False, str(e), []
        except ApiException as e:
            error_msg = f"Failed to get orders: {e}"
            logger.error("L4.snaptrade [orders] - %s", error_msg)
            return False, error_msg, []
        except Exception as e:
            error_msg = f"Unexpected error getting orders: {e}"
            logger.error("L4.snaptrade [orders] - %s", error_msg)
            return False, error_msg, []

    def get_activities(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        account_id: Optional[str] = None,
        activity_type: Optional[str] = None,
    ) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        Get account activities (dividends, trades, transfers, etc.)

        Args:
            start_date: Start date YYYY-MM-DD (defaults to 30 days ago)
            end_date: End date YYYY-MM-DD (defaults to today)
            account_id: Optional account ID filter
            activity_type: Optional type filter (e.g., "DIVIDEND", "BUY", "SELL")

        Returns:
            Tuple of (success, message, activities_list)
        """
        try:
            user_id, user_secret = self._require_credentials()

            if not start_date:
                start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
            if not end_date:
                end_date = datetime.now().strftime("%Y-%m-%d")

            logger.info(
                "L4.snaptrade [activities] - Fetching activities %s to %s",
                start_date, end_date,
            )

            response = self.client.transactions_and_reporting.get_activities(
                user_id=user_id,
                user_secret=user_secret,
                start_date=start_date,
                end_date=end_date,
            )

            raw_activities = response.body if response.body else []
            activities = []

            for act in raw_activities:
                a_dict = act if isinstance(act, dict) else dict(act)

                # Filter by account if requested
                if account_id and a_dict.get("account", {}).get("id") != account_id:
                    continue

                # Filter by type if requested
                if activity_type and a_dict.get("type") != activity_type:
                    continue

                activities.append(a_dict)

            logger.info(
                "L4.snaptrade [activities] - Found %d activities",
                len(activities),
            )

            return (
                True,
                f"Found {len(activities)} activities from {start_date} to {end_date}",
                activities,
            )

        except ValueError as e:
            return False, str(e), []
        except ApiException as e:
            error_msg = f"Failed to get activities: {e}"
            logger.error("L4.snaptrade [activities] - %s", error_msg)
            return False, error_msg, []
        except Exception as e:
            error_msg = f"Unexpected error getting activities: {e}"
            logger.error("L4.snaptrade [activities] - %s", error_msg)
            return False, error_msg, []

    # =========================================================================
    # Performance
    # =========================================================================

    def get_account_performance(
        self, account_id: str
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Get return rates for a specific account.

        Args:
            account_id: The SnapTrade account ID

        Returns:
            Tuple of (success, message, performance_data)
        """
        try:
            user_id, user_secret = self._require_credentials()

            logger.info("L4.snaptrade [performance] - Fetching return rates for %s", account_id)

            response = self.client.account_information.get_user_account_return_rates(
                user_id=user_id,
                user_secret=user_secret,
                account_id=account_id,
            )

            result = response.body if response.body else {}
            perf_dict = result if isinstance(result, dict) else dict(result) if result else {}

            return (
                True,
                f"Retrieved performance data for account {account_id}",
                perf_dict,
            )

        except ValueError as e:
            return False, str(e), {}
        except ApiException as e:
            error_msg = f"Failed to get performance: {e}"
            logger.error("L4.snaptrade [performance] - %s", error_msg)
            return False, error_msg, {}
        except Exception as e:
            error_msg = f"Unexpected error getting performance: {e}"
            logger.error("L4.snaptrade [performance] - %s", error_msg)
            return False, error_msg, {}

    # =========================================================================
    # Connections
    # =========================================================================

    def list_connections(self) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        List all brokerage connections/authorizations.

        Returns:
            Tuple of (success, message, connections_list)
        """
        try:
            user_id, user_secret = self._require_credentials()

            logger.info("L4.snaptrade [connections] - Listing connections")

            response = self.client.connections.list_brokerage_authorizations(
                user_id=user_id,
                user_secret=user_secret,
            )

            raw_connections = response.body if response.body else []
            connections = []

            for conn in raw_connections:
                c_dict = conn if isinstance(conn, dict) else dict(conn)
                brokerage = c_dict.get("brokerage", {})
                if isinstance(brokerage, dict):
                    brokerage_name = brokerage.get("name", "Unknown")
                else:
                    brokerage_name = str(brokerage)

                connections.append({
                    "authorization_id": c_dict.get("id", ""),
                    "brokerage_name": brokerage_name,
                    "created_at": c_dict.get("created_date", ""),
                    "updated_at": c_dict.get("updated_date", ""),
                    "disabled": c_dict.get("disabled", False),
                    "disabled_date": c_dict.get("disabled_date"),
                    "raw": c_dict,
                })

            logger.info(
                "L4.snaptrade [connections] - Found %d connections",
                len(connections),
            )

            return (
                True,
                f"Found {len(connections)} brokerage connection(s)",
                connections,
            )

        except ValueError as e:
            return False, str(e), []
        except ApiException as e:
            error_msg = f"Failed to list connections: {e}"
            logger.error("L4.snaptrade [connections] - %s", error_msg)
            return False, error_msg, []
        except Exception as e:
            error_msg = f"Unexpected error listing connections: {e}"
            logger.error("L4.snaptrade [connections] - %s", error_msg)
            return False, error_msg, []

    def refresh_connection(
        self, authorization_id: str
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Refresh a brokerage connection to sync latest data.

        Args:
            authorization_id: The brokerage authorization ID

        Returns:
            Tuple of (success, message, result)
        """
        try:
            user_id, user_secret = self._require_credentials()

            logger.info(
                "L4.snaptrade [refresh] - Refreshing connection %s",
                authorization_id,
            )

            response = self.client.connections.refresh_brokerage_authorization(
                authorization_id=authorization_id,
                user_id=user_id,
                user_secret=user_secret,
            )

            result = response.body if response.body else {}
            result_dict = result if isinstance(result, dict) else dict(result) if result else {}

            # Invalidate account cache to force fresh data on next query
            if self.accounts_cache_file.exists():
                self.accounts_cache_file.unlink()

            logger.info("L4.snaptrade [refresh] - Connection refreshed")

            return (
                True,
                f"Connection {authorization_id} refreshed successfully",
                result_dict,
            )

        except ValueError as e:
            return False, str(e), {}
        except ApiException as e:
            error_msg = f"Failed to refresh connection: {e}"
            logger.error("L4.snaptrade [refresh] - %s", error_msg)
            return False, error_msg, {}
        except Exception as e:
            error_msg = f"Unexpected error refreshing connection: {e}"
            logger.error("L4.snaptrade [refresh] - %s", error_msg)
            return False, error_msg, {}

    def remove_connection(
        self, authorization_id: str
    ) -> Tuple[bool, str]:
        """
        Remove a brokerage connection.

        Args:
            authorization_id: The brokerage authorization ID

        Returns:
            Tuple of (success, message)
        """
        try:
            user_id, user_secret = self._require_credentials()

            logger.info(
                "L4.snaptrade [disconnect] - Removing connection %s",
                authorization_id,
            )

            self.client.connections.remove_brokerage_authorization(
                authorization_id=authorization_id,
                user_id=user_id,
                user_secret=user_secret,
            )

            # Invalidate caches
            if self.accounts_cache_file.exists():
                self.accounts_cache_file.unlink()
            if self.holdings_cache_file.exists():
                self.holdings_cache_file.unlink()

            logger.info("L4.snaptrade [disconnect] - Connection removed")

            return (
                True,
                f"Brokerage connection {authorization_id} removed successfully",
            )

        except ValueError as e:
            return False, str(e)
        except ApiException as e:
            error_msg = f"Failed to remove connection: {e}"
            logger.error("L4.snaptrade [disconnect] - %s", error_msg)
            return False, error_msg
        except Exception as e:
            error_msg = f"Unexpected error removing connection: {e}"
            logger.error("L4.snaptrade [disconnect] - %s", error_msg)
            return False, error_msg

    # =========================================================================
    # Symbol Lookup
    # =========================================================================

    def search_symbols(self, query: str) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        Search for a symbol/ticker.

        Args:
            query: Ticker or company name to search

        Returns:
            Tuple of (success, message, symbols_list)
        """
        try:
            logger.info("L4.snaptrade [symbols] - Searching for: %s", query)

            response = self.client.reference_data.get_symbols_by_ticker(
                query=query,
            )

            raw_symbols = response.body if response.body else []
            symbols = []

            for sym in raw_symbols:
                s_dict = sym if isinstance(sym, dict) else dict(sym)
                symbols.append(s_dict)

            return (
                True,
                f"Found {len(symbols)} symbol(s) matching '{query}'",
                symbols,
            )

        except ApiException as e:
            error_msg = f"Failed to search symbols: {e}"
            logger.error("L4.snaptrade [symbols] - %s", error_msg)
            return False, error_msg, []
        except Exception as e:
            error_msg = f"Unexpected error searching symbols: {e}"
            logger.error("L4.snaptrade [symbols] - %s", error_msg)
            return False, error_msg, []

    # =========================================================================
    # Trading (Schwab only — Fidelity is read-only)
    # =========================================================================

    def preview_order(
        self,
        account_id: str,
        action: str,
        symbol_id: str,
        order_type: str = "Market",
        time_in_force: str = "Day",
        units: Optional[float] = None,
        price: Optional[float] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Preview an order to see its impact before placing it.

        Args:
            account_id: Account to trade in
            action: "BUY" or "SELL"
            symbol_id: Universal symbol ID (from search_symbols)
            order_type: "Market", "Limit", "Stop", "StopLimit"
            time_in_force: "Day", "GTC", "FOK", "IOC"
            units: Number of shares
            price: Limit price (required for Limit/StopLimit orders)

        Returns:
            Tuple of (success, message, impact_data with trade_id)
        """
        try:
            user_id, user_secret = self._require_credentials()

            logger.info(
                "L4.snaptrade [trade] - Previewing %s order: %s x %s",
                action, units, symbol_id,
            )

            kwargs = {
                "user_id": user_id,
                "user_secret": user_secret,
                "account_id": account_id,
                "action": action,
                "universal_symbol_id": symbol_id,
                "order_type": order_type,
                "time_in_force": time_in_force,
            }
            if units is not None:
                kwargs["units"] = units
            if price is not None:
                kwargs["price"] = price

            response = self.client.trading.get_order_impact(**kwargs)

            result = response.body if response.body else {}
            result_dict = result if isinstance(result, dict) else dict(result) if result else {}

            trade_id = result_dict.get("trade", {}).get("id", "") if isinstance(result_dict.get("trade"), dict) else ""

            return (
                True,
                f"Order preview generated. Trade ID: {trade_id}. Review impact before executing.",
                result_dict,
            )

        except ValueError as e:
            return False, str(e), {}
        except ApiException as e:
            error_msg = f"Failed to preview order: {e}"
            logger.error("L4.snaptrade [trade] - %s", error_msg)
            return False, error_msg, {}
        except Exception as e:
            error_msg = f"Unexpected error previewing order: {e}"
            logger.error("L4.snaptrade [trade] - %s", error_msg)
            return False, error_msg, {}

    def execute_order(self, trade_id: str) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Execute a previously previewed order.

        Args:
            trade_id: Trade ID from preview_order

        Returns:
            Tuple of (success, message, order_result)
        """
        try:
            user_id, user_secret = self._require_credentials()

            logger.info(
                "L4.snaptrade [trade] - Executing order %s",
                trade_id,
            )

            response = self.client.trading.place_order(
                trade_id=trade_id,
                user_id=user_id,
                user_secret=user_secret,
            )

            result = response.body if response.body else {}
            result_dict = result if isinstance(result, dict) else dict(result) if result else {}

            return (
                True,
                f"Order {trade_id} executed successfully",
                result_dict,
            )

        except ValueError as e:
            return False, str(e), {}
        except ApiException as e:
            error_msg = f"Failed to execute order: {e}"
            logger.error("L4.snaptrade [trade] - %s", error_msg)
            return False, error_msg, {}
        except Exception as e:
            error_msg = f"Unexpected error executing order: {e}"
            logger.error("L4.snaptrade [trade] - %s", error_msg)
            return False, error_msg, {}

    # =========================================================================
    # API Status
    # =========================================================================

    def check_api_status(self) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Check SnapTrade API status.

        Returns:
            Tuple of (success, message, status_data)
        """
        try:
            response = self.client.api_status.check()
            result = response.body if response.body else {}
            result_dict = result if isinstance(result, dict) else dict(result) if result else {}

            return (
                True,
                "SnapTrade API is operational",
                result_dict,
            )

        except Exception as e:
            return False, f"SnapTrade API may be down: {e}", {"error": str(e)}


# Global client instance (lazy initialization)
_snaptrade_client: Optional[SnapTradeClient] = None


def get_snaptrade_client() -> SnapTradeClient:
    """Get or create the global SnapTrade client instance."""
    global _snaptrade_client
    if _snaptrade_client is None:
        _snaptrade_client = SnapTradeClient()
    return _snaptrade_client


__all__ = [
    "SnapTradeClient",
    "get_snaptrade_client",
]
