"""
Web Push Notification Service

Handles sending push notifications to subscribed clients using VAPID.
"""

import json
import os
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Paths
SECRETS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.claude/secrets"))
CLAUDE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.claude"))
VAPID_KEYS_FILE = os.path.join(SECRETS_DIR, "vapid_keys.json")
SUBSCRIPTIONS_FILE = os.path.join(CLAUDE_DIR, "push_subscriptions.json")


@dataclass
class PushSubscription:
    """Represents a push subscription from a client."""
    endpoint: str
    keys: Dict[str, str]  # p256dh and auth keys
    created_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "endpoint": self.endpoint,
            "keys": self.keys,
            "created_at": self.created_at
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PushSubscription":
        return cls(
            endpoint=data["endpoint"],
            keys=data["keys"],
            created_at=data.get("created_at")
        )


def load_vapid_keys() -> Optional[Dict[str, str]]:
    """Load VAPID keys from secrets file."""
    try:
        if not os.path.exists(VAPID_KEYS_FILE):
            logger.warning(f"VAPID keys file not found: {VAPID_KEYS_FILE}")
            return None
        with open(VAPID_KEYS_FILE) as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load VAPID keys: {e}")
        return None


def get_vapid_public_key() -> Optional[str]:
    """Get the VAPID public key for client subscriptions."""
    keys = load_vapid_keys()
    return keys.get("publicKey") if keys else None


def load_subscriptions() -> List[PushSubscription]:
    """Load all push subscriptions."""
    try:
        if not os.path.exists(SUBSCRIPTIONS_FILE):
            return []
        with open(SUBSCRIPTIONS_FILE) as f:
            data = json.load(f)
            return [PushSubscription.from_dict(s) for s in data.get("subscriptions", [])]
    except Exception as e:
        logger.error(f"Failed to load subscriptions: {e}")
        return []


def save_subscriptions(subscriptions: List[PushSubscription]):
    """Save all push subscriptions."""
    try:
        data = {"subscriptions": [s.to_dict() for s in subscriptions]}
        with open(SUBSCRIPTIONS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save subscriptions: {e}")


def add_subscription(subscription: PushSubscription) -> bool:
    """Add a new push subscription."""
    subscriptions = load_subscriptions()

    # Check if already exists (by endpoint)
    for existing in subscriptions:
        if existing.endpoint == subscription.endpoint:
            # Update keys if different
            existing.keys = subscription.keys
            save_subscriptions(subscriptions)
            return True

    subscriptions.append(subscription)
    save_subscriptions(subscriptions)
    return True


def remove_subscription(endpoint: str) -> bool:
    """Remove a push subscription by endpoint."""
    subscriptions = load_subscriptions()
    original_count = len(subscriptions)
    subscriptions = [s for s in subscriptions if s.endpoint != endpoint]

    if len(subscriptions) < original_count:
        save_subscriptions(subscriptions)
        return True
    return False


async def send_push_notification(
    title: str,
    body: str,
    chat_id: str,
    critical: bool = False
) -> int:
    """
    Send push notification to all subscriptions.

    Returns number of successful sends.
    """
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        logger.error("pywebpush not installed. Run: pip install pywebpush")
        return 0

    vapid_keys = load_vapid_keys()
    if not vapid_keys:
        logger.error("Cannot send push: VAPID keys not configured")
        return 0

    subscriptions = load_subscriptions()
    if not subscriptions:
        logger.debug("No push subscriptions to notify")
        return 0

    payload = json.dumps({
        "title": title,
        "body": body,
        "chat_id": chat_id,
        "critical": critical,
        "icon": "/icons/icon-192.png",
        "badge": "/icons/icon-192.png"
    })

    vapid_claims = {
        "sub": vapid_keys.get("subject", "mailto:noreply@secondbrain.local")
    }

    # Extract raw base64 key from PEM format (py_vapid doesn't accept PEM headers)
    private_key_pem = vapid_keys["privateKeyPem"]
    private_key_raw = private_key_pem.replace('-----BEGIN PRIVATE KEY-----', '').replace('-----END PRIVATE KEY-----', '').replace('\n', '').strip()

    success_count = 0
    expired_endpoints = []

    for subscription in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": subscription.keys
                },
                data=payload,
                vapid_private_key=private_key_raw,
                vapid_claims=vapid_claims
            )
            success_count += 1
            logger.debug(f"Push sent to {subscription.endpoint[:50]}...")
        except WebPushException as e:
            if e.response and e.response.status_code == 410:
                # Subscription expired
                expired_endpoints.append(subscription.endpoint)
                logger.info(f"Push subscription expired: {subscription.endpoint[:50]}...")
            else:
                logger.error(f"Push failed: {e}")
        except Exception as e:
            logger.error(f"Push failed: {e}")

    # Clean up expired subscriptions
    for endpoint in expired_endpoints:
        remove_subscription(endpoint)

    logger.info(f"Push notifications sent: {success_count}/{len(subscriptions)} successful")
    return success_count
