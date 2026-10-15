"""Push notification system for Presidium.

Provides zero-knowledge push notifications that preserve privacy:
    - Self-hosted push relay (aiohttp): delivers only encrypted blobs
    - Blind fetch: recipients poll by pseudorandom ID, relay can't link
    - FCM/APNS fallback: only sends encrypted_payload_id (16 bytes)
    - Local notification manager: grouped, inline reply, history

No plaintext ever touches the relay. The relay sees only:
    - A 128-bit random blob ID
    - An encrypted payload (opaque to the relay)
    - An HMAC for integrity verification

The recipient discovers which IDs to fetch by computing:
    fetch_id = SHA-256(public_key || timestamp_bucket)
This means the relay can't even tell which IDs belong to whom.
"""

from presidium.push.relay_server import PushRelayServer, PushRelayConfig

# Push client depends on aiohttp; import lazily so modules that only need
# the relay server (e.g., unit tests for storage) can import this package
# without aiohttp installed.
try:
    from presidium.push.client import PushClient, PushClientConfig, PushProvider
except Exception:
    PushClient = None
    PushClientConfig = None
    PushProvider = None

__all__ = [
    "PushRelayServer",
    "PushRelayConfig",
    "PushClient",
    "PushClientConfig",
    "PushProvider",
]
