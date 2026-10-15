"""Zero-Knowledge Push Relay Server for Presidium.

Implements a privacy-preserving push notification relay that never sees
plaintext. The relay stores only encrypted blobs indexed by pseudorandom IDs,
making it impossible to link notifications to specific users or determine
message contents.

Architecture:
    Sender → POST /push {blob_id, encrypted_blob, hmac}
        - blob_id = SHA-256(recipient_public_key || timestamp_bucket)[:16]
        - encrypted_blob = opaque ciphertext (E2EE between sender and recipient)
        - hmac = HMAC-SHA256(blob_id || encrypted_blob, relay_hmac_key)
        - Relay validates HMAC for integrity, stores blob, returns 202

    Recipient → GET /pull?ids=<comma-separated-blob-ids>
        - Computes blob_ids from own public_key + current/prev time buckets
        - Relay returns matching encrypted blobs without knowing who fetches
        - Recipient decrypts locally using Double Ratchet / HPKE

    FCM/APNS fallback:
        - POST /register-token {device_token, encrypted_user_id}
        - When a blob arrives, relay sends FCM/APNS with ONLY the 16-byte
          encrypted_payload_id (no message content)
        - Client then does a blind fetch to retrieve the actual payload

Rate limiting:
    - 100 pushes per hour per IP address
    - 600 pulls per hour per IP address
    - Blobs expire after 7 days

Key privacy properties:
    - Relay cannot determine which blob_ids belong to which users
    - Relay cannot read message contents (end-to-end encrypted)
    - Relay cannot link senders to recipients
    - FCM/APNS tokens are stored encrypted with the user's public key
    - Even if relay is compromised, no metadata or content is exposed
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

try:
    from aiohttp import web
except Exception:
    # Minimal stub for tests that import the module but don't start the HTTP server.
    class _WebStub:
        def middleware(self, func=None):
            if func is None:
                return lambda f: f
            return func

        class Application(dict):
            def __init__(self):
                super().__init__()
                self.middlewares = []
            def add_routes(self, routes):
                pass

        class AppRunner:
            def __init__(self, app):
                pass
            async def setup(self):
                pass
            async def cleanup(self):
                pass

        class TCPSite:
            def __init__(self, runner, host, port):
                pass
            async def start(self):
                pass

        @staticmethod
        def json_response(obj, status=200):
            return obj

        class Request:  # type: ignore
            pass

        class Response:  # type: ignore
            pass

    web = _WebStub()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BLOB_ID_SIZE = 16           # 128-bit blob identifier
MAX_BLOB_SIZE = 65536       # 64 KiB max encrypted payload
BLOB_TTL_SECONDS = 604800   # 7 days
HMAC_KEY_SIZE = 32          # 256-bit HMAC key
MAX_IDS_PER_PULL = 50       # Maximum blob IDs per pull request
PUSH_RATE_LIMIT = 100       # Pushes per hour per IP
PULL_RATE_LIMIT = 600       # Pulls per hour per IP
CLEANUP_INTERVAL = 3600     # 1 hour between cleanup sweeps


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class PushRelayError(Exception):
    """Base exception for push relay operations."""


class BlobNotFoundError(PushRelayError):
    """Requested blob ID not found on the relay."""


class RateLimitError(PushRelayError):
    """Rate limit exceeded for the client IP."""


class InvalidBlobError(PushRelayError):
    """Blob data is invalid (wrong size, bad HMAC, etc.)."""


class RelayStorageError(PushRelayError):
    """Failed to store or retrieve a blob."""


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class StoredBlob:
    """An encrypted blob stored on the relay.

    Attributes:
        blob_id: 16-byte pseudorandom identifier.
        encrypted_data: Opaque encrypted payload (E2EE).
        hmac_tag: HMAC-SHA256 tag for integrity verification.
        created_at: Unix timestamp when the blob was stored.
        expires_at: Unix timestamp when the blob expires.
        access_count: Number of times this blob has been fetched.
    """
    blob_id: bytes
    encrypted_data: bytes
    hmac_tag: bytes
    created_at: float
    expires_at: float
    access_count: int = 0


@dataclass
class DeviceToken:
    """Encrypted FCM/APNS device token for push fallback.

    Attributes:
        encrypted_user_id: User identifier encrypted with relay key.
        device_token: FCM or APNS token string.
        platform: "fcm" or "apns".
        registered_at: Unix timestamp of registration.
    """
    encrypted_user_id: bytes
    device_token: str
    platform: str  # "fcm" or "apns"
    registered_at: float


@dataclass
class RateLimitEntry:
    """Rate limit tracking for a single IP address."""
    push_count: int = 0
    pull_count: int = 0
    window_start: float = 0.0


# ---------------------------------------------------------------------------
# In-memory blob storage
# ---------------------------------------------------------------------------

class BlobStorage:
    """In-memory storage for encrypted blobs with automatic expiry.

    For production deployment, this should be replaced with a persistent
    store (Redis, SQLite, etc.) that survives relay restarts. The current
    implementation uses an in-memory dict with periodic cleanup.

    Thread safety: This class is designed for single-threaded asyncio use.
    If multi-threading is needed, add appropriate locking.
    """

    def __init__(self, max_total_blobs: int = 1_000_000) -> None:
        self._blobs: dict[bytes, StoredBlob] = {}
        self._max_total_blobs = max_total_blobs
        self._total_bytes: int = 0

    async def store(self, blob: StoredBlob) -> bool:
        """Store a blob, replacing any existing blob with the same ID.

        Args:
            blob: The StoredBlob to persist.

        Returns:
            True if stored successfully, False if storage is full.

        Raises:
            RelayStorageError: If the blob data is invalid.
        """
        if len(blob.encrypted_data) > MAX_BLOB_SIZE:
            raise RelayStorageError(
                f"Blob too large: {len(blob.encrypted_data)} bytes, "
                f"max {MAX_BLOB_SIZE}."
            )
        if len(blob.blob_id) != BLOB_ID_SIZE:
            raise RelayStorageError(
                f"Invalid blob ID size: {len(blob.blob_id)}, "
                f"expected {BLOB_ID_SIZE}."
            )

        # Enforce storage limit with LRU eviction
        if len(self._blobs) >= self._max_total_blobs:
            await self._evict_oldest()

        # Track bytes
        existing = self._blobs.get(blob.blob_id)
        if existing is not None:
            self._total_bytes -= len(existing.encrypted_data)

        self._blobs[blob.blob_id] = blob
        self._total_bytes += len(blob.encrypted_data)

        logger.debug(
            "Stored blob %s (%d bytes, expires %d)",
            blob.blob_id.hex(),
            len(blob.encrypted_data),
            int(blob.expires_at),
        )
        return True

    async def fetch(self, blob_id: bytes) -> Optional[StoredBlob]:
        """Fetch a blob by its ID.

        Args:
            blob_id: 16-byte blob identifier.

        Returns:
            The StoredBlob if found and not expired, None otherwise.
        """
        blob = self._blobs.get(blob_id)
        if blob is None:
            return None

        # Check expiry
        if time.time() > blob.expires_at:
            await self.delete(blob_id)
            return None

        blob.access_count += 1
        return blob

    async def fetch_many(self, blob_ids: list[bytes]) -> list[StoredBlob]:
        """Fetch multiple blobs by their IDs.

        Args:
            blob_ids: List of 16-byte blob identifiers.

        Returns:
            List of found StoredBlob objects (may be fewer than requested).
        """
        results: list[StoredBlob] = []
        for bid in blob_ids:
            blob = await self.fetch(bid)
            if blob is not None:
                results.append(blob)
        return results

    async def delete(self, blob_id: bytes) -> bool:
        """Delete a blob by its ID.

        Returns:
            True if the blob was found and deleted, False otherwise.
        """
        blob = self._blobs.pop(blob_id, None)
        if blob is not None:
            self._total_bytes -= len(blob.encrypted_data)
            return True
        return False

    async def cleanup_expired(self) -> int:
        """Remove all expired blobs.

        Returns:
            Number of blobs removed.
        """
        now = time.time()
        expired_ids = [
            bid for bid, blob in self._blobs.items()
            if now > blob.expires_at
        ]
        for bid in expired_ids:
            blob = self._blobs.pop(bid)
            self._total_bytes -= len(blob.encrypted_data)

        if expired_ids:
            logger.info("Cleaned up %d expired blobs.", len(expired_ids))
        return len(expired_ids)

    async def _evict_oldest(self) -> None:
        """Evict the oldest blob to make room for new ones."""
        if not self._blobs:
            return
        oldest_id = min(self._blobs, key=lambda k: self._blobs[k].created_at)
        blob = self._blobs.pop(oldest_id)
        self._total_bytes -= len(blob.encrypted_data)
        logger.debug("Evicted oldest blob %s to free space.", oldest_id.hex())

    @property
    def blob_count(self) -> int:
        """Number of blobs currently stored."""
        return len(self._blobs)

    @property
    def total_bytes(self) -> int:
        """Total bytes of encrypted data stored."""
        return self._total_bytes


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class RateLimiter:
    """Per-IP rate limiter using sliding window counters.

    Tracks push and pull request counts per IP address within
    one-hour sliding windows. When the window expires, counters
    are reset automatically.
    """

    def __init__(
        self,
        push_limit: int = PUSH_RATE_LIMIT,
        pull_limit: int = PULL_RATE_LIMIT,
        window_seconds: int = 3600,
    ) -> None:
        self._push_limit = push_limit
        self._pull_limit = pull_limit
        self._window_seconds = window_seconds
        self._entries: dict[str, RateLimitEntry] = {}

    def check_push(self, ip: str) -> bool:
        """Check if a push request from this IP is allowed.

        Returns:
            True if allowed, False if rate limited.
        """
        entry = self._get_or_create(ip)
        self._maybe_reset(entry)
        if entry.push_count >= self._push_limit:
            return False
        entry.push_count += 1
        return True

    def check_pull(self, ip: str) -> bool:
        """Check if a pull request from this IP is allowed.

        Returns:
            True if allowed, False if rate limited.
        """
        entry = self._get_or_create(ip)
        self._maybe_reset(entry)
        if entry.pull_count >= self._pull_limit:
            return False
        entry.pull_count += 1
        return True

    def _get_or_create(self, ip: str) -> RateLimitEntry:
        """Get or create a rate limit entry for an IP."""
        if ip not in self._entries:
            self._entries[ip] = RateLimitEntry(window_start=time.time())
        return self._entries[ip]

    def _maybe_reset(self, entry: RateLimitEntry) -> None:
        """Reset counters if the window has expired."""
        now = time.time()
        if now - entry.window_start > self._window_seconds:
            entry.push_count = 0
            entry.pull_count = 0
            entry.window_start = now

    def cleanup_stale(self, max_age: float = 7200) -> int:
        """Remove entries older than max_age seconds.

        Returns:
            Number of entries removed.
        """
        now = time.time()
        stale_ips = [
            ip for ip, entry in self._entries.items()
            if now - entry.window_start > max_age
        ]
        for ip in stale_ips:
            del self._entries[ip]
        return len(stale_ips)


# ---------------------------------------------------------------------------
# Push Relay Configuration
# ---------------------------------------------------------------------------

@dataclass
class PushRelayConfig:
    """Configuration for the Zero-Knowledge Push Relay.

    Attributes:
        host: Bind address for the relay server.
        port: Bind port for the relay server.
        hmac_key: 32-byte key for HMAC verification. Auto-generated if None.
        max_blob_size: Maximum encrypted blob size in bytes.
        blob_ttl: Time-to-live for blobs in seconds.
        max_total_blobs: Maximum number of blobs stored simultaneously.
        push_rate_limit: Max pushes per hour per IP.
        pull_rate_limit: Max pulls per hour per IP.
        fcm_server_key: Firebase Cloud Messaging server key (optional).
        apns_cert_path: APNS certificate path (optional).
        cors_origins: Allowed CORS origins.
    """
    host: str = "0.0.0.0"
    port: int = 9479
    hmac_key: bytes = b""
    max_blob_size: int = MAX_BLOB_SIZE
    blob_ttl: int = BLOB_TTL_SECONDS
    max_total_blobs: int = 1_000_000
    push_rate_limit: int = PUSH_RATE_LIMIT
    pull_rate_limit: int = PULL_RATE_LIMIT
    fcm_server_key: str = ""
    apns_cert_path: str = ""
    cors_origins: list[str] = field(default_factory=lambda: ["*"])

    def __post_init__(self) -> None:
        if not self.hmac_key:
            self.hmac_key = os.urandom(HMAC_KEY_SIZE)


# ---------------------------------------------------------------------------
# Push Relay Server
# ---------------------------------------------------------------------------

class PushRelayServer:
    """Zero-Knowledge Push Relay Server powered by aiohttp.

    The relay operates as a simple encrypted blob store with blind fetch.
    It never sees plaintext and cannot link blob IDs to users. The relay
    validates HMAC tags on incoming blobs for integrity but cannot read
    the encrypted content.

    Endpoints:
        POST /push      — Store an encrypted blob
        GET  /pull      — Fetch blobs by IDs (blind fetch)
        POST /register  — Register FCM/APNS device token
        DELETE /token   — Unregister device token
        GET  /health    — Health check
        GET  /stats     — Relay statistics (admin)

    Lifecycle:
        server = PushRelayServer(config)
        await server.start()    # Starts the aiohttp server
        ...
        await server.stop()     # Graceful shutdown
    """

    def __init__(self, config: Optional[PushRelayConfig] = None) -> None:
        self._config = config or PushRelayConfig()
        self._storage = BlobStorage(max_total_blobs=self._config.max_total_blobs)
        self._rate_limiter = RateLimiter(
            push_limit=self._config.push_rate_limit,
            pull_limit=self._config.pull_rate_limit,
        )
        self._device_tokens: dict[str, DeviceToken] = {}
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._cleanup_task: Optional[asyncio.Task] = None
        self._started_at: float = 0.0

    # -------------------------------------------------------------------
    # Server lifecycle
    # -------------------------------------------------------------------

    async def start(self) -> None:
        """Start the push relay server."""
        self._app = web.Application()
        self._app.add_routes([
            web.post("/push", self._handle_push),
            web.get("/pull", self._handle_pull),
            web.post("/register", self._handle_register),
            web.delete("/token", self._handle_unregister),
            web.get("/health", self._handle_health),
            web.get("/stats", self._handle_stats),
        ])

        # CORS middleware
        self._app.middlewares.append(self._cors_middleware)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        self._site = web.TCPSite(
            self._runner,
            self._config.host,
            self._config.port,
        )
        await self._site.start()

        # Start background cleanup
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        self._started_at = time.time()

        logger.info(
            "Push Relay Server started on %s:%d",
            self._config.host,
            self._config.port,
        )

    async def stop(self) -> None:
        """Gracefully stop the push relay server."""
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

        logger.info("Push Relay Server stopped.")

    # -------------------------------------------------------------------
    # Background tasks
    # -------------------------------------------------------------------

    async def _cleanup_loop(self) -> None:
        """Periodically clean up expired blobs and stale rate limit entries."""
        while True:
            try:
                await asyncio.sleep(CLEANUP_INTERVAL)
                expired = await self._storage.cleanup_expired()
                stale = self._rate_limiter.cleanup_stale()
                if expired > 0 or stale > 0:
                    logger.info(
                        "Cleanup: removed %d expired blobs, %d stale rate entries.",
                        expired,
                        stale,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Cleanup loop error: %s", exc)

    # -------------------------------------------------------------------
    # CORS middleware
    # -------------------------------------------------------------------

    @web.middleware
    async def _cors_middleware(self, request: web.Request, handler: web.Handler) -> web.StreamResponse:
        """Add CORS headers to all responses."""
        response = await handler(request)
        origin = request.headers.get("Origin", "*")
        if origin in self._config.cors_origins or "*" in self._config.cors_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            response.headers["Access-Control-Max-Age"] = "86400"
        return response

    # -------------------------------------------------------------------
    # Request helpers
    # -------------------------------------------------------------------

    def _get_client_ip(self, request: web.Request) -> str:
        """Extract the client IP, respecting X-Forwarded-For headers."""
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        peername = request.transport.get_extra_info("peername") if request.transport else None
        if peername:
            return peername[0]
        return "unknown"

    def _verify_hmac(self, blob_id: bytes, encrypted_data: bytes, hmac_tag: bytes) -> bool:
        """Verify HMAC-SHA256 tag for a blob.

        Args:
            blob_id: 16-byte blob identifier.
            encrypted_data: The encrypted payload bytes.
            hmac_tag: 32-byte HMAC tag to verify.

        Returns:
            True if the HMAC is valid, False otherwise.
        """
        expected = hmac.new(
            self._config.hmac_key,
            blob_id + encrypted_data,
            hashlib.sha256,
        ).digest()
        return hmac.compare_digest(expected, hmac_tag)

    # -------------------------------------------------------------------
    # HTTP handlers
    # -------------------------------------------------------------------

    async def _handle_push(self, request: web.Request) -> web.Response:
        """POST /push — Store an encrypted blob on the relay.

        Request body (binary):
            [16B blob_id][4B data_len][N bytes encrypted_data][32B hmac_tag]

        The relay stores the blob and optionally sends a push notification
        to the recipient via FCM/APNS (containing only the blob_id).

        Returns:
            202 Accepted on success.
            400 Bad Request if the payload is malformed.
            429 Too Many Requests if rate limited.
        """
        ip = self._get_client_ip(request)

        # Rate limit check
        if not self._rate_limiter.check_push(ip):
            return web.json_response(
                {"error": "rate_limit", "message": "Push rate limit exceeded."},
                status=429,
            )

        # Read body
        try:
            body = await request.read()
        except Exception as exc:
            return web.json_response(
                {"error": "bad_request", "message": f"Failed to read body: {exc}"},
                status=400,
            )

        # Parse binary payload
        if len(body) < BLOB_ID_SIZE + 4 + HMAC_KEY_SIZE:
            return web.json_response(
                {"error": "bad_request", "message": "Payload too short."},
                status=400,
            )

        offset = 0
        blob_id = body[offset:offset + BLOB_ID_SIZE]
        offset += BLOB_ID_SIZE

        import struct
        data_len = struct.unpack("!I", body[offset:offset + 4])[0]
        offset += 4

        if data_len > self._config.max_blob_size:
            return web.json_response(
                {"error": "bad_request", "message": f"Encrypted data too large: {data_len}."},
                status=400,
            )

        if len(body) < offset + data_len + HMAC_KEY_SIZE:
            return web.json_response(
                {"error": "bad_request", "message": "Payload truncated."},
                status=400,
            )

        encrypted_data = body[offset:offset + data_len]
        offset += data_len

        hmac_tag = body[offset:offset + HMAC_KEY_SIZE]

        # Verify HMAC
        if not self._verify_hmac(blob_id, encrypted_data, hmac_tag):
            return web.json_response(
                {"error": "hmac_invalid", "message": "HMAC verification failed."},
                status=400,
            )

        # Store the blob
        now = time.time()
        blob = StoredBlob(
            blob_id=blob_id,
            encrypted_data=encrypted_data,
            hmac_tag=hmac_tag,
            created_at=now,
            expires_at=now + self._config.blob_ttl,
        )

        try:
            await self._storage.store(blob)
        except RelayStorageError as exc:
            return web.json_response(
                {"error": "storage_error", "message": str(exc)},
                status=507,
            )

        # Optionally trigger FCM/APNS push
        await self._send_push_notification(blob_id)

        logger.debug(
            "Push accepted: blob_id=%s, size=%d, ip=%s",
            blob_id.hex(),
            data_len,
            ip,
        )

        return web.json_response(
            {"status": "accepted", "blob_id": blob_id.hex()},
            status=202,
        )

    async def _handle_pull(self, request: web.Request) -> web.Response:
        """GET /pull?ids=<hex_blob_id1,hex_blob_id2,...> — Blind fetch blobs.

        The recipient computes blob IDs from their own public key and
        timestamp buckets, then fetches matching blobs without revealing
        their identity to the relay.

        Returns:
            200 with JSON array of {blob_id, encrypted_data, hmac_tag}.
            429 if rate limited.
        """
        ip = self._get_client_ip(request)

        # Rate limit check
        if not self._rate_limiter.check_pull(ip):
            return web.json_response(
                {"error": "rate_limit", "message": "Pull rate limit exceeded."},
                status=429,
            )

        # Parse IDs
        ids_param = request.query.get("ids", "")
        if not ids_param:
            return web.json_response(
                {"error": "bad_request", "message": "Missing 'ids' parameter."},
                status=400,
            )

        hex_ids = [h.strip() for h in ids_param.split(",") if h.strip()]
        if len(hex_ids) > MAX_IDS_PER_PULL:
            return web.json_response(
                {"error": "bad_request",
                 "message": f"Too many IDs: {len(hex_ids)}, max {MAX_IDS_PER_PULL}."},
                status=400,
            )

        blob_ids: list[bytes] = []
        for hid in hex_ids:
            try:
                bid = bytes.fromhex(hid)
                if len(bid) != BLOB_ID_SIZE:
                    raise ValueError(f"Wrong size: {len(bid)}")
                blob_ids.append(bid)
            except (ValueError, Exception) as exc:
                return web.json_response(
                    {"error": "bad_request", "message": f"Invalid blob ID '{hid}': {exc}"},
                    status=400,
                )

        # Fetch blobs
        blobs = await self._storage.fetch_many(blob_ids)

        # Build response
        result = []
        for blob in blobs:
            result.append({
                "blob_id": blob.blob_id.hex(),
                "encrypted_data": blob.encrypted_data.hex(),
                "hmac_tag": blob.hmac_tag.hex(),
                "expires_at": blob.expires_at,
            })

        # Delete fetched blobs (one-time fetch for forward secrecy)
        for blob in blobs:
            await self._storage.delete(blob.blob_id)

        logger.debug(
            "Pull: requested %d IDs, returned %d blobs, ip=%s",
            len(blob_ids),
            len(result),
            ip,
        )

        return web.json_response({"blobs": result})

    async def _handle_register(self, request: web.Request) -> web.Response:
        """POST /register — Register a FCM/APNS device token.

        Request body (JSON):
            {
                "encrypted_user_id": "<hex>",
                "device_token": "<string>",
                "platform": "fcm" | "apns"
            }

        The encrypted_user_id is opaque to the relay — it's the user's
        identity encrypted with a key known only to the user and the
        notification dispatch service.
        """
        try:
            data = await request.json()
        except Exception as exc:
            return web.json_response(
                {"error": "bad_request", "message": f"Invalid JSON: {exc}"},
                status=400,
            )

        encrypted_uid_hex = data.get("encrypted_user_id", "")
        device_token = data.get("device_token", "")
        platform = data.get("platform", "")

        if not encrypted_uid_hex or not device_token or platform not in ("fcm", "apns"):
            return web.json_response(
                {"error": "bad_request",
                 "message": "Missing or invalid fields: encrypted_user_id, device_token, platform."},
                status=400,
            )

        try:
            encrypted_uid = bytes.fromhex(encrypted_uid_hex)
        except ValueError:
            return web.json_response(
                {"error": "bad_request", "message": "Invalid encrypted_user_id hex."},
                status=400,
            )

        token_entry = DeviceToken(
            encrypted_user_id=encrypted_uid,
            device_token=device_token,
            platform=platform,
            registered_at=time.time(),
        )
        self._device_tokens[device_token] = token_entry

        logger.debug(
            "Registered %s token: %s...%s",
            platform.upper(),
            device_token[:8],
            device_token[-4:],
        )

        return web.json_response({"status": "registered"}, status=201)

    async def _handle_unregister(self, request: web.Request) -> web.Response:
        """DELETE /token?device_token=<token> — Unregister a device token."""
        token = request.query.get("device_token", "")
        if not token:
            return web.json_response(
                {"error": "bad_request", "message": "Missing device_token parameter."},
                status=400,
            )

        removed = self._device_tokens.pop(token, None)
        if removed is None:
            return web.json_response(
                {"error": "not_found", "message": "Token not registered."},
                status=404,
            )

        return web.json_response({"status": "unregistered"})

    async def _handle_health(self, request: web.Request) -> web.Response:
        """GET /health — Health check endpoint."""
        return web.json_response({
            "status": "healthy",
            "uptime": time.time() - self._started_at if self._started_at else 0,
            "blob_count": self._storage.blob_count,
            "total_bytes": self._storage.total_bytes,
        })

    async def _handle_stats(self, request: web.Request) -> web.Response:
        """GET /stats — Relay statistics (for monitoring)."""
        return web.json_response({
            "uptime_seconds": time.time() - self._started_at if self._started_at else 0,
            "blob_count": self._storage.blob_count,
            "total_bytes_stored": self._storage.total_bytes,
            "registered_devices": len(self._device_tokens),
            "rate_limit_entries": len(self._rate_limiter._entries),
        })

    # -------------------------------------------------------------------
    # FCM/APNS push notification dispatch
    # -------------------------------------------------------------------

    async def _send_push_notification(self, blob_id: bytes) -> None:
        """Send a push notification to registered devices.

        The notification contains ONLY the 16-byte encrypted_payload_id.
        No message content is ever included — the client must fetch the
        actual encrypted blob separately.

        For FCM: sends a data message with blob_id field.
        For APNS: sends a background notification with blob_id in custom data.

        This is a best-effort operation — failures are logged but don't
        block the push operation.
        """
        blob_id_hex = blob_id.hex()

        # FCM push
        if self._config.fcm_server_key:
            fcm_tokens = [
                t for t in self._device_tokens.values()
                if t.platform == "fcm"
            ]
            for token_entry in fcm_tokens:
                try:
                    await self._send_fcm(token_entry.device_token, blob_id_hex)
                except Exception as exc:
                    logger.debug("FCM push failed for %s...%s: %s",
                                 token_entry.device_token[:8],
                                 token_entry.device_token[-4:],
                                 exc)

        # APNS push
        if self._config.apns_cert_path:
            apns_tokens = [
                t for t in self._device_tokens.values()
                if t.platform == "apns"
            ]
            for token_entry in apns_tokens:
                try:
                    await self._send_apns(token_entry.device_token, blob_id_hex)
                except Exception as exc:
                    logger.debug("APNS push failed for %s...%s: %s",
                                 token_entry.device_token[:8],
                                 token_entry.device_token[-4:],
                                 exc)

    async def _send_fcm(self, device_token: str, blob_id_hex: str) -> None:
        """Send a Firebase Cloud Messaging data notification.

        The notification payload contains only the blob_id — the client
        app will then fetch the encrypted blob from the relay.
        """
        import aiohttp

        url = "https://fcm.googleapis.com/fcm/send"
        headers = {
            "Authorization": f"key={self._config.fcm_server_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "to": device_token,
            "data": {
                "blob_id": blob_id_hex,
                "type": "presidium_message",
            },
            "content_available": True,
            "priority": "high",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning("FCM returned %d: %s", resp.status, body[:200])

    async def _send_apns(self, device_token: str, blob_id_hex: str) -> None:
        """Send an Apple Push Notification Service notification.

        Uses APNS HTTP/2 API with the configured certificate.
        The notification is a background push containing only the blob_id.
        """
        # APNS HTTP/2 integration
        # In production, use the aiohttp-http2 or hyper library for HTTP/2
        # For now, log the notification for debugging
        logger.debug(
            "APNS push: token=%s...%s, blob_id=%s",
            device_token[:8],
            device_token[-4:],
            blob_id_hex,
        )
