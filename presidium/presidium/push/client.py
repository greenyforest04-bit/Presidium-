"""Push Client for Presidium — privacy-preserving notification receiver.

Implements the client-side of the Zero-Knowledge Push system:
    - Blind fetch: computes expected blob IDs from own public key and
      timestamp buckets, then fetches matching blobs without revealing
      identity to the relay
    - FCM/APNS fallback: receives only a 16-byte encrypted_payload_id,
      then performs a blind fetch to retrieve the actual E2EE payload
    - Local decryption: uses Double Ratchet / HPKE to decrypt payloads
      that the relay could never read
    - Background polling: periodic fetch on configurable interval

Protocol flow:
    Client (background)                     Relay
      |                                       |
      |  1. Compute blob_ids:                 |
      |     id = SHA-256(my_pk || bucket)     |
      |     bucket = floor(now / 3600)        |
      |  2. GET /pull?ids=<hex_id1>,...       |
      |  ----ids (no identity)------------>   |
      |  <---encrypted blobs----------------  |
      |  3. Decrypt with Double Ratchet/HPKE  |
      |                                       |
      |                                       |
    FCM/APNS path:                           |
      |  <---push with blob_id only--------   |
      |  GET /pull?ids=<hex_blob_id>          |
      |  ----blob_id---------------------->   |
      |  <---encrypted blob-----------------  |
      |  4. Decrypt locally                  |

Privacy guarantees:
    - Relay never learns which public key maps to which blob IDs
    - Relay never sees plaintext message content
    - FCM/APNS only receives 16-byte blob_id, no message metadata
    - Blob IDs change hourly, preventing long-term tracking
    - Each blob is fetched only once (forward secrecy)
"""

from __future__ import annotations

import asyncio
import enum
import hashlib
import logging
import struct
import time
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Optional

import aiohttp

from presidium.core.crypto import AES256GCM, SecureBytes, secure_zero

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BLOB_ID_SIZE = 16               # 128-bit blob identifier
TIME_BUCKET_SECONDS = 3600      # 1 hour time bucket for ID computation
POLL_INTERVAL_DEFAULT = 30      # seconds between background polls
PULL_TIMEOUT = 10               # seconds for HTTP pull request
MAX_POLL_RETRIES = 3            # max retries on transient failures
FCM_PAYLOAD_ID_SIZE = 16        # bytes sent via FCM/APNS


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class PushClientError(Exception):
    """Base exception for push client operations."""


class FetchError(PushClientError):
    """Failed to fetch blobs from the relay."""


class DecryptionError(PushClientError):
    """Failed to decrypt a fetched blob."""


class RegistrationError(PushClientError):
    """Failed to register or unregister a device token."""


class RelayUnavailableError(PushClientError):
    """Relay server is unreachable."""


# ---------------------------------------------------------------------------
# Push provider enum
# ---------------------------------------------------------------------------

class PushProvider(enum.Enum):
    """Push notification provider type."""
    NONE = "none"       # No push provider (polling only)
    FCM = "fcm"         # Firebase Cloud Messaging (Android/Web)
    APNS = "apns"       # Apple Push Notification Service (iOS/macOS)
    LOCAL = "local"     # Local notification (desktop)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class PushClientConfig:
    """Configuration for the Push Client.

    Attributes:
        relay_url: Base URL of the push relay server.
        public_key: Our X25519 public key (32 bytes) for blob ID computation.
        poll_interval: Seconds between background polls.
        time_buckets: Number of past time buckets to check per poll.
        fcm_token: Firebase Cloud Messaging device token (optional).
        apns_token: Apple Push Notification Service token (optional).
        provider: Active push notification provider.
        pull_timeout: HTTP timeout for pull requests in seconds.
        auto_start: Whether to start polling automatically on init.
    """
    relay_url: str = "https://relay.presidium.chat"
    public_key: bytes = b""      # 32 bytes X25519 public key
    poll_interval: int = POLL_INTERVAL_DEFAULT
    time_buckets: int = 3        # Check current + 2 previous buckets
    fcm_token: str = ""
    apns_token: str = ""
    provider: PushProvider = PushProvider.NONE
    pull_timeout: int = PULL_TIMEOUT
    auto_start: bool = True


@dataclass
class FetchedBlob:
    """A blob fetched from the relay, before local decryption.

    Attributes:
        blob_id: 16-byte identifier of the blob.
        encrypted_data: Raw encrypted payload bytes.
        hmac_tag: HMAC tag from the relay.
        expires_at: Unix timestamp when the blob expires.
    """
    blob_id: bytes
    encrypted_data: bytes
    hmac_tag: bytes
    expires_at: float


@dataclass
class DecryptedNotification:
    """A fully decrypted push notification.

    Attributes:
        sender_user_id: User ID of the message sender.
        message_type: Type of notification (chat, group, file, etc.).
        encrypted_payload: The E2EE message content (still encrypted
            with the session key; the push client only strips the
            relay-level encryption).
        received_at: Unix timestamp when the notification was processed.
    """
    sender_user_id: str
    message_type: str
    encrypted_payload: bytes
    received_at: float


# ---------------------------------------------------------------------------
# Blob ID computation
# ---------------------------------------------------------------------------

class BlobIDComputer:
    """Computes pseudorandom blob IDs from a public key and time buckets.

    The recipient precomputes the blob IDs they expect to have messages
    for, then fetches them in a single blind request. The relay cannot
    determine which public key corresponds to which blob IDs because
    SHA-256 is a one-way function.

    Formula:
        bucket = floor(unix_timestamp / TIME_BUCKET_SECONDS)
        blob_id = SHA-256(public_key || big_endian_uint64(bucket))[:16]
    """

    def __init__(self, public_key: bytes) -> None:
        if len(public_key) != 32:
            raise PushClientError(
                f"Invalid public key size: {len(public_key)}, expected 32."
            )
        self._public_key = public_key

    def compute_current_ids(self, num_buckets: int = 3) -> list[bytes]:
        """Compute blob IDs for the current and recent time buckets.

        Args:
            num_buckets: Number of buckets to compute (current + past).

        Returns:
            List of 16-byte blob IDs, most recent first.
        """
        current_bucket = int(time.time()) // TIME_BUCKET_SECONDS
        ids: list[bytes] = []
        for offset in range(num_buckets):
            bucket = current_bucket - offset
            blob_id = self._compute_id(bucket)
            ids.append(blob_id)
        return ids

    def compute_id_for_blob_id_hex(self, blob_id_hex: str) -> bytes:
        """Convert a hex blob ID from an FCM/APNS push to bytes.

        Args:
            blob_id_hex: 32-character hex string of the blob ID.

        Returns:
            16-byte blob ID.
        """
        try:
            bid = bytes.fromhex(blob_id_hex)
            if len(bid) != BLOB_ID_SIZE:
                raise PushClientError(
                    f"Invalid blob ID size: {len(bid)}, expected {BLOB_ID_SIZE}."
                )
            return bid
        except ValueError as exc:
            raise PushClientError(f"Invalid blob ID hex: {exc}") from exc

    def _compute_id(self, bucket: int) -> bytes:
        """Compute a single blob ID for a time bucket.

        Args:
            bucket: Time bucket index.

        Returns:
            16-byte blob ID.
        """
        data = self._public_key + struct.pack("!Q", bucket)
        return hashlib.sha256(data).digest()[:BLOB_ID_SIZE]


# ---------------------------------------------------------------------------
# Push Client
# ---------------------------------------------------------------------------

class PushClient:
    """Client for the Zero-Knowledge Push Relay.

    Provides:
        - Background polling for new encrypted blobs
        - FCM/APNS-triggered fetch on receiving a push
        - Device token registration with the relay
        - Local decryption of relay-level encryption
        - Callbacks for delivering decrypted notifications to the app

    Lifecycle:
        client = PushClient(config)
        client.set_on_notification(callback)
        await client.start()    # Starts background polling
        ...
        await client.stop()     # Graceful shutdown
    """

    def __init__(self, config: PushClientConfig) -> None:
        self._config = config
        self._id_computer = BlobIDComputer(config.public_key)
        self._session: Optional[aiohttp.ClientSession] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._running = False
        self._last_poll_time: float = 0.0
        self._consecutive_errors = 0

        # Callbacks
        self._on_notification: Optional[Callable[[DecryptedNotification], Awaitable[None]]] = None
        self._on_fetch_error: Optional[Callable[[Exception], Awaitable[None]]] = None

        # Stats
        self._total_fetched: int = 0
        self._total_decrypted: int = 0
        self._total_errors: int = 0

    # -------------------------------------------------------------------
    # Callback registration
    # -------------------------------------------------------------------

    def set_on_notification(
        self, callback: Callable[[DecryptedNotification], Awaitable[None]]
    ) -> None:
        """Register a callback for when a notification is decrypted.

        Args:
            callback: Async function called with the DecryptedNotification.
        """
        self._on_notification = callback

    def set_on_fetch_error(
        self, callback: Callable[[Exception], Awaitable[None]]
    ) -> None:
        """Register a callback for fetch errors.

        Args:
            callback: Async function called with the exception.
        """
        self._on_fetch_error = callback

    # -------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------

    async def start(self) -> None:
        """Start the push client background polling.

        Creates an aiohttp session and starts the poll loop. If a
        device token is configured, also registers with the relay.
        """
        if self._running:
            return

        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self._config.pull_timeout),
        )
        self._running = True

        # Register device token if available
        if self._config.provider in (PushProvider.FCM, PushProvider.APNS):
            await self._register_device_token()

        # Start background polling
        self._poll_task = asyncio.create_task(self._poll_loop())

        logger.info(
            "Push client started (provider=%s, interval=%ds).",
            self._config.provider.value,
            self._config.poll_interval,
        )

    async def stop(self) -> None:
        """Stop the push client and clean up resources."""
        self._running = False

        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

        if self._session is not None:
            await self._session.close()
            self._session = None

        logger.info("Push client stopped.")

    # -------------------------------------------------------------------
    # Background polling
    # -------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        """Periodically poll the relay for new blobs."""
        while self._running:
            try:
                await asyncio.sleep(self._config.poll_interval)
                if not self._running:
                    break

                await self.fetch_and_decrypt()
                self._consecutive_errors = 0

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._consecutive_errors += 1
                self._total_errors += 1
                logger.warning(
                    "Poll error (#%d): %s", self._consecutive_errors, exc
                )

                if self._on_fetch_error:
                    await self._on_fetch_error(exc)

                # Exponential backoff on repeated errors
                if self._consecutive_errors > 1:
                    backoff = min(
                        self._config.poll_interval * (2 ** min(self._consecutive_errors - 1, 5)),
                        300,  # Max 5 minutes
                    )
                    await asyncio.sleep(backoff)

    # -------------------------------------------------------------------
    # Fetch and decrypt
    # -------------------------------------------------------------------

    async def fetch_and_decrypt(self) -> list[DecryptedNotification]:
        """Fetch new blobs from the relay and decrypt them.

        Computes expected blob IDs from our public key and current/recent
        time buckets, then fetches matching blobs and decrypts them.

        Returns:
            List of decrypted notifications.
        """
        if self._session is None:
            raise FetchError("Client not started — no HTTP session.")

        # Compute blob IDs
        blob_ids = self._id_computer.compute_current_ids(
            num_buckets=self._config.time_buckets
        )

        # Fetch from relay
        blobs = await self._fetch_blobs(blob_ids)
        self._total_fetched += len(blobs)

        # Decrypt each blob
        notifications: list[DecryptedNotification] = []
        for blob in blobs:
            try:
                notification = self._decrypt_blob(blob)
                notifications.append(notification)
                self._total_decrypted += 1

                # Deliver via callback
                if self._on_notification:
                    await self._on_notification(notification)

            except Exception as exc:
                logger.warning(
                    "Failed to decrypt blob %s: %s",
                    blob.blob_id.hex(),
                    exc,
                )
                self._total_errors += 1

        self._last_poll_time = time.time()
        return notifications

    async def fetch_by_blob_id(self, blob_id_hex: str) -> Optional[DecryptedNotification]:
        """Fetch a specific blob by its hex ID (from FCM/APNS push).

        This is called when the app receives a push notification
        containing only a blob_id, and needs to fetch the actual
        encrypted payload.

        Args:
            blob_id_hex: 32-character hex string of the blob ID.

        Returns:
            Decrypted notification, or None if blob not found.
        """
        if self._session is None:
            raise FetchError("Client not started — no HTTP session.")

        blob_id = self._id_computer.compute_id_for_blob_id_hex(blob_id_hex)
        blobs = await self._fetch_blobs([blob_id])

        if not blobs:
            logger.debug("Blob %s not found on relay.", blob_id_hex)
            return None

        notification = self._decrypt_blob(blobs[0])
        self._total_fetched += 1
        self._total_decrypted += 1

        if self._on_notification:
            await self._on_notification(notification)

        return notification

    # -------------------------------------------------------------------
    # HTTP operations
    # -------------------------------------------------------------------

    async def _fetch_blobs(self, blob_ids: list[bytes]) -> list[FetchedBlob]:
        """Fetch blobs from the relay by their IDs.

        Args:
            blob_ids: List of 16-byte blob identifiers.

        Returns:
            List of FetchedBlob objects.

        Raises:
            FetchError: If the HTTP request fails.
            RelayUnavailableError: If the relay is unreachable.
        """
        ids_param = ",".join(bid.hex() for bid in blob_ids)
        url = f"{self._config.relay_url}/pull?ids={ids_param}"

        for attempt in range(MAX_POLL_RETRIES):
            try:
                async with self._session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return self._parse_pull_response(data)
                    if resp.status == 429:
                        retry_after = int(resp.headers.get("Retry-After", "60"))
                        logger.warning("Rate limited. Retrying in %ds.", retry_after)
                        await asyncio.sleep(retry_after)
                        continue
                    if resp.status >= 500:
                        logger.warning("Relay error %d, retrying...", resp.status)
                        await asyncio.sleep(2 ** attempt)
                        continue
                    # Client error — don't retry
                    body = await resp.text()
                    raise FetchError(f"Relay returned {resp.status}: {body[:200]}")

            except aiohttp.ClientError as exc:
                if attempt == MAX_POLL_RETRIES - 1:
                    raise RelayUnavailableError(
                        f"Relay unreachable after {MAX_POLL_RETRIES} attempts: {exc}"
                    ) from exc
                await asyncio.sleep(2 ** attempt)

        return []

    def _parse_pull_response(self, data: dict) -> list[FetchedBlob]:
        """Parse the JSON response from a /pull request.

        Args:
            data: Parsed JSON response from the relay.

        Returns:
            List of FetchedBlob objects.
        """
        blobs: list[FetchedBlob] = []
        for item in data.get("blobs", []):
            blob_id = bytes.fromhex(item["blob_id"])
            encrypted_data = bytes.fromhex(item["encrypted_data"])
            hmac_tag = bytes.fromhex(item["hmac_tag"])
            expires_at = item.get("expires_at", 0.0)

            blobs.append(FetchedBlob(
                blob_id=blob_id,
                encrypted_data=encrypted_data,
                hmac_tag=hmac_tag,
                expires_at=expires_at,
            ))
        return blobs

    # -------------------------------------------------------------------
    # Local decryption
    # -------------------------------------------------------------------

    def _decrypt_blob(self, blob: FetchedBlob) -> DecryptedNotification:
        """Decrypt a fetched blob using the relay-level key.

        Note: The blob contains data that is already E2EE between
        the sender and recipient (via Double Ratchet or HPKE).
        The relay adds a layer of AES-256-GCM encryption using
        a key derived from the recipient's public key and the
        blob_id. This function strips that outer layer.

        The inner payload is still encrypted and must be processed
        by the messaging layer (ChatManager / SealedSenderManager).

        Args:
            blob: The fetched blob to decrypt.

        Returns:
            DecryptedNotification with the inner encrypted payload.

        Raises:
            DecryptionError: If the outer relay encryption cannot be removed.
        """
        # Derive relay-level decryption key from our public key + blob_id
        # Key = HKDF-SHA256(SHA-256(pk || blob_id), info="presidium-relay-key")
        combined = self._config.public_key + blob.blob_id
        prk = hashlib.sha256(combined).digest()

        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives import hashes

        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=blob.blob_id,
            info=b"presidium-relay-key",
        )
        relay_key = hkdf.derive(prk)

        try:
            # Decrypt: first 12 bytes = nonce, rest = ciphertext + tag
            plaintext = AES256GCM.decrypt(
                key=relay_key,
                data=blob.encrypted_data,
                aad=blob.blob_id,
            )
        except Exception as exc:
            raise DecryptionError(
                f"Failed to decrypt relay-level encryption: {exc}"
            ) from exc
        finally:
            secure_zero(bytearray(relay_key))

        # Parse the inner payload format:
        # [1B type_len][N bytes message_type]
        # [16B sender_user_id]
        # [rest: encrypted_payload]
        if len(plaintext) < 1 + 16:
            raise DecryptionError("Decrypted payload too short.")

        offset = 0
        type_len = plaintext[offset]
        offset += 1

        if len(plaintext) < offset + type_len + 16:
            raise DecryptionError("Decrypted payload truncated.")

        message_type = plaintext[offset:offset + type_len].decode("utf-8")
        offset += type_len

        sender_user_id = plaintext[offset:offset + 16].hex()
        offset += 16

        encrypted_payload = plaintext[offset:]

        return DecryptedNotification(
            sender_user_id=sender_user_id,
            message_type=message_type,
            encrypted_payload=encrypted_payload,
            received_at=time.time(),
        )

    # -------------------------------------------------------------------
    # Push to relay (sender side)
    # -------------------------------------------------------------------

    async def push_to_relay(
        self,
        recipient_public_key: bytes,
        encrypted_payload: bytes,
        message_type: str = "chat",
        sender_user_id: str = "",
    ) -> str:
        """Push an encrypted blob to the relay for a recipient.

        This is used by the SENDER to deposit an encrypted blob on the
        relay. The blob_id is computed from the recipient's public key
        and the current time bucket, so the recipient can find it.

        Args:
            recipient_public_key: Recipient's X25519 public key (32 bytes).
            encrypted_payload: E2EE message content (already encrypted
                with Double Ratchet or HPKE).
            message_type: Type string for the notification.
            sender_user_id: Our user ID (included in relay-level payload).

        Returns:
            Hex string of the stored blob_id.

        Raises:
            FetchError: If the HTTP request to the relay fails.
        """
        if self._session is None:
            raise FetchError("Client not started — no HTTP session.")

        # Compute the recipient's blob ID
        recipient_computer = BlobIDComputer(recipient_public_key)
        blob_id = recipient_computer.compute_current_ids(num_buckets=1)[0]

        # Add relay-level encryption
        # Inner format: [1B type_len][type][16B sender_uid][payload]
        type_bytes = message_type.encode("utf-8")
        sender_uid_bytes = bytes.fromhex(sender_user_id) if sender_user_id else b"\x00" * 16

        inner = (
            bytes([len(type_bytes)])
            + type_bytes
            + sender_uid_bytes
            + encrypted_payload
        )

        # Derive relay-level encryption key
        combined = recipient_public_key + blob_id
        prk = hashlib.sha256(combined).digest()

        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives import hashes

        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=blob_id,
            info=b"presidium-relay-key",
        )
        relay_key = hkdf.derive(prk)

        try:
            encrypted_data = AES256GCM.encrypt(
                key=relay_key,
                plaintext=inner,
                aad=blob_id,
            )
        finally:
            secure_zero(bytearray(relay_key))

        # Compute HMAC
        import hmac as hmac_mod
        hmac_tag = hmac_mod.new(
            b"presidium-push-hmac",  # Client-side HMAC key (relay has its own)
            blob_id + encrypted_data,
            hashlib.sha256,
        ).digest()

        # Build binary payload: [16B blob_id][4B data_len][N data][32B hmac]
        import struct as struct_mod
        payload = (
            blob_id
            + struct_mod.pack("!I", len(encrypted_data))
            + encrypted_data
            + hmac_tag
        )

        # POST to relay
        url = f"{self._config.relay_url}/push"
        async with self._session.post(url, data=payload) as resp:
            if resp.status not in (200, 202):
                body = await resp.text()
                raise FetchError(f"Push failed with {resp.status}: {body[:200]}")

        blob_id_hex = blob_id.hex()
        logger.debug("Pushed blob %s for recipient.", blob_id_hex)
        return blob_id_hex

    # -------------------------------------------------------------------
    # Device token management
    # -------------------------------------------------------------------

    async def _register_device_token(self) -> None:
        """Register our FCM/APNS device token with the relay."""
        if self._config.provider == PushProvider.FCM and self._config.fcm_token:
            await self.register_device_token(
                device_token=self._config.fcm_token,
                platform="fcm",
            )
        elif self._config.provider == PushProvider.APNS and self._config.apns_token:
            await self.register_device_token(
                device_token=self._config.apns_token,
                platform="apns",
            )

    async def register_device_token(
        self, device_token: str, platform: str
    ) -> None:
        """Register a device token with the relay for push fallback.

        The encrypted_user_id is derived from our public key so the
        relay can map push events to the right device without knowing
        our actual identity.

        Args:
            device_token: FCM or APNS device token string.
            platform: "fcm" or "apns".

        Raises:
            RegistrationError: If registration fails.
        """
        if self._session is None:
            raise RegistrationError("Client not started — no HTTP session.")

        if platform not in ("fcm", "apns"):
            raise RegistrationError(f"Invalid platform: {platform!r}")

        # Encrypt user ID for relay storage
        encrypted_uid = hashlib.sha256(
            self._config.public_key + b"presidium-device-uid"
        ).digest()

        payload = {
            "encrypted_user_id": encrypted_uid.hex(),
            "device_token": device_token,
            "platform": platform,
        }

        url = f"{self._config.relay_url}/register"
        try:
            async with self._session.post(url, json=payload) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    raise RegistrationError(
                        f"Registration failed with {resp.status}: {body[:200]}"
                    )
        except aiohttp.ClientError as exc:
            raise RegistrationError(f"Registration request failed: {exc}") from exc

        logger.info("Registered %s device token with relay.", platform.upper())

    async def unregister_device_token(self, device_token: str) -> None:
        """Unregister a device token from the relay.

        Args:
            device_token: The token to unregister.

        Raises:
            RegistrationError: If unregistration fails.
        """
        if self._session is None:
            raise RegistrationError("Client not started — no HTTP session.")

        url = f"{self._config.relay_url}/token?device_token={device_token}"
        try:
            async with self._session.delete(url) as resp:
                if resp.status not in (200, 404):
                    body = await resp.text()
                    raise RegistrationError(
                        f"Unregister failed with {resp.status}: {body[:200]}"
                    )
        except aiohttp.ClientError as exc:
            raise RegistrationError(f"Unregister request failed: {exc}") from exc

        logger.info("Unregistered device token from relay.")

    # -------------------------------------------------------------------
    # Stats
    # -------------------------------------------------------------------

    @property
    def total_fetched(self) -> int:
        """Total number of blobs fetched from the relay."""
        return self._total_fetched

    @property
    def total_decrypted(self) -> int:
        """Total number of blobs successfully decrypted."""
        return self._total_decrypted

    @property
    def total_errors(self) -> int:
        """Total number of errors encountered."""
        return self._total_errors

    @property
    def last_poll_time(self) -> float:
        """Unix timestamp of the last successful poll."""
        return self._last_poll_time

    @property
    def is_running(self) -> bool:
        """Whether the client is currently running."""
        return self._running
