"""Push notification system for Presidium.

Provides privacy-preserving push notifications through self-hosted
relay servers and optional FCM/APNS fallbacks.

Components:
    - relay_server.py: Self-hosted push relay (aiohttp)
    - client.py: Push notification client (blind fetch + FCM/APNS)
    - notifications.py: Local notification manager (Qt integration)

Security model:
    The relay stores only encrypted blobs keyed by pseudorandom IDs.
    It cannot determine sender, recipient, or content. Recipients
    fetch blobs by ID = SHA-256(their_public_key), making all fetches
    indistinguishable from random lookups.
"""

from presidium.push.client import PushClient, PushTransport
from presidium.push.relay_server import PushRelayServer, PushRelayConfig

__all__ = ["PushClient", "PushTransport", "PushRelayServer", "PushRelayConfig"]
