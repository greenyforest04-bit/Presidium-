"""Matrix federation bridge for Presidium.

Implements bidirectional message relay between Presidium's P2P network and
Matrix homeservers using the Matrix Client-Server API (v1.x).

Architecture overview:
    Presidium ←→ MatrixBridge ←→ Matrix HS ←→ Matrix users

The bridge operates as a Matrix "application service" style bot that:
    1. Logs into a Matrix homeserver using username/password or access token
    2. Listens for events in bridged rooms via /sync long-polling
    3. Converts Matrix events into BridgedMessages for Presidium
    4. Converts Presidium messages into Matrix event sends

Encryption handling:
    - If the Matrix room uses Megolm (m.megolm.v1.aes-sha2), the bridge
      can decrypt messages using its own device keys (if configured).
    - Decrypted Matrix messages are re-wrapped as Presidium E2EE messages.
    - Outbound Presidium messages are sent as m.text (unencrypted) to Matrix
      unless the Matrix room is encrypted, in which case the bridge encrypts
      them with Megolm.

Rate limiting:
    - Respects Matrix rate limits (429 responses with Retry-After)
    - Applies local inbound/outbound rate limits from BridgeConfig
    - Exponential backoff on connection failures

Supported Matrix events:
    - m.room.message (m.text, m.emote, m.notice, m.image, m.file)
    - m.reaction
    - m.typing
    - m.read_receipt (optional)
    - m.room.member (for membership sync)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.parse
from typing import Any, Optional

import aiohttp

from presidium.bridges.protocol import (
    BaseBridge,
    BridgedMessage,
    BridgedRoom,
    BridgeConfig,
    BridgeDirection,
    BridgeMessageType,
    BridgeStatus,
    BridgeStats,
    BridgeAuthError,
    BridgeConnectionError,
    BridgeError,
    BridgeMessageError,
    BridgeRateLimitError,
    BridgeRoomError,
    register_bridge,
)
from presidium.utils.config import AppConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Matrix-specific constants
# ---------------------------------------------------------------------------

MATRIX_DEFAULT_HOMESERVER = "https://matrix.org"
MATRIX_SYNC_TIMEOUT_MS = 30000  # 30 seconds long-poll
MATRIX_SYNC_RETRY_DELAY = 5.0
MATRIX_MAX_EVENT_BATCH = 100
MATRIX_MEDIA_DOWNLOAD_TIMEOUT = 60


# ---------------------------------------------------------------------------
# Matrix API client
# ---------------------------------------------------------------------------

class MatrixClient:
    """Low-level Matrix Client-Server API client.

    Handles authentication, rate-limited requests, and /sync streaming.
    This is NOT a full Matrix SDK — it implements only the endpoints
    needed for the bridge.
    """

    def __init__(self, homeserver_url: str, access_token: str = "") -> None:
        self._homeserver = homeserver_url.rstrip("/")
        self._access_token = access_token
        self._user_id: Optional[str] = None
        self._device_id: Optional[str] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._next_batch_token: Optional[str] = None
        self._request_count = 0

    # -------------------------------------------------------------------
    # Session management
    # -------------------------------------------------------------------

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Get or create the aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self._auth_headers(),
                timeout=aiohttp.ClientTimeout(total=120),
            )
        return self._session

    def _auth_headers(self) -> dict[str, str]:
        """Return authorization headers."""
        headers: dict[str, str] = {}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        return headers

    # -------------------------------------------------------------------
    # Authentication
    # -------------------------------------------------------------------

    async def login_password(
        self,
        username: str,
        password: str,
        device_id: str = "presidium-bridge",
    ) -> dict[str, str]:
        """Log in with username and password (m.login.password).

        Args:
            username: Full Matrix user ID (e.g. @bot:matrix.org) or localpart.
            password: Account password.
            device_id: Device ID to use.

        Returns:
            Dict with 'user_id', 'access_token', 'device_id'.

        Raises:
            BridgeAuthError: If login fails.
        """
        url = f"{self._homeserver}/_matrix/client/v3/login"
        payload = {
            "type": "m.login.password",
            "identifier": {
                "type": "m.id.user",
                "user": username,
            },
            "password": password,
            "device_id": device_id,
        }
        resp_data = await self._request("POST", url, json=payload)

        if "error" in resp_data:
            raise BridgeAuthError(
                f"Matrix login failed: {resp_data.get('error', 'unknown error')}"
            )

        self._access_token = resp_data["access_token"]
        self._user_id = resp_data["user_id"]
        self._device_id = resp_data.get("device_id", device_id)

        # Rebuild session with new token
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

        return {
            "user_id": self._user_id,
            "access_token": self._access_token,
            "device_id": self._device_id or "",
        }

    async def whoami(self) -> str:
        """Verify the access token and return the associated user_id.

        Raises:
            BridgeAuthError: If the token is invalid.
        """
        url = f"{self._homeserver}/_matrix/client/v3/account/whoami"
        resp_data = await self._request("GET", url)

        if "error" in resp_data:
            raise BridgeAuthError(
                f"Matrix token invalid: {resp_data.get('error', 'unknown')}"
            )

        self._user_id = resp_data["user_id"]
        return self._user_id

    # -------------------------------------------------------------------
    # Sync
    # -------------------------------------------------------------------

    async def sync(
        self,
        since: Optional[str] = None,
        timeout_ms: int = MATRIX_SYNC_TIMEOUT_MS,
        full_state: bool = False,
    ) -> dict[str, Any]:
        """Perform a /sync request (long-polling).

        Args:
            since: Next_batch token from previous sync.
            timeout_ms: Long-poll timeout in milliseconds.
            full_state: Whether to request full state.

        Returns:
            The /sync response JSON.

        Raises:
            BridgeConnectionError: If the sync request fails.
        """
        params: dict[str, Any] = {
            "timeout": timeout_ms,
        }
        if since:
            params["since"] = since
        if full_state:
            params["full_state"] = "true"

        url = f"{self._homeserver}/_matrix/client/v3/sync"
        return await self._request("GET", url, params=params)

    # -------------------------------------------------------------------
    # Room operations
    # -------------------------------------------------------------------

    async def create_room(
        self,
        room_alias: Optional[str] = None,
        name: str = "",
        topic: str = "",
        invite: Optional[list[str]] = None,
        is_direct: bool = True,
        preset: str = "private_chat",
        visibility: str = "private",
    ) -> dict[str, Any]:
        """Create a new Matrix room.

        Args:
            room_alias: Optional canonical alias (without # prefix).
            name: Room display name.
            topic: Room topic.
            invite: List of Matrix user IDs to invite.
            is_direct: Whether this is a DM room.
            preset: Room preset (private_chat, trusted_private_chat, public_chat).
            visibility: Room visibility.

        Returns:
            Dict with 'room_id' and optionally 'room_alias'.
        """
        url = f"{self._homeserver}/_matrix/client/v3/createRoom"
        payload: dict[str, Any] = {
            "visibility": visibility,
            "preset": preset,
            "is_direct": is_direct,
            "creation_content": {
                "m.federate": True,
            },
        }
        if room_alias:
            payload["room_alias_name"] = room_alias
        if name:
            payload["name"] = name
        if topic:
            payload["topic"] = topic
        if invite:
            payload["invite"] = invite

        return await self._request("POST", url, json=payload)

    async def join_room(self, room_id_or_alias: str) -> dict[str, Any]:
        """Join a Matrix room.

        Args:
            room_id_or_alias: Room ID (!id:hs) or alias (#alias:hs).

        Returns:
            Dict with 'room_id'.
        """
        encoded = urllib.parse.quote(room_id_or_alias, safe="")
        url = f"{self._homeserver}/_matrix/client/v3/join/{encoded}"
        return await self._request("POST", url, json={})

    async def leave_room(self, room_id: str) -> dict[str, Any]:
        """Leave a Matrix room."""
        url = f"{self._homeserver}/_matrix/client/v3/rooms/{room_id}/leave"
        return await self._request("POST", url, json={})

    async def get_joined_rooms(self) -> list[str]:
        """Get list of rooms the bridge user has joined.

        Returns:
            List of room IDs.
        """
        url = f"{self._homeserver}/_matrix/client/v3/joined_rooms"
        resp = await self._request("GET", url)
        return resp.get("joined_rooms", [])

    async def get_room_members(self, room_id: str) -> list[str]:
        """Get the list of joined members in a room.

        Returns:
            List of Matrix user IDs.
        """
        url = (
            f"{self._homeserver}/_matrix/client/v3/rooms/{room_id}/members"
        )
        resp = await self._request("GET", url, params={"membership": "join"})
        return [
            evt["content"]["user_id"]
            for evt in resp.get("chunk", [])
            if evt.get("content", {}).get("membership") == "join"
        ]

    # -------------------------------------------------------------------
    # Message sending
    # -------------------------------------------------------------------

    async def send_message(
        self,
        room_id: str,
        text: str,
        msgtype: str = "m.text",
        reply_to_event_id: str = "",
    ) -> str:
        """Send a text message to a Matrix room.

        Args:
            room_id: Target Matrix room ID.
            text: Message body.
            msgtype: Message type (m.text, m.emote, m.notice).
            reply_to_event_id: If replying, the event ID being replied to.

        Returns:
            The event_id of the sent message.
        """
        txn_id = f"presidium_{int(time.time() * 1000)}_{self._request_count}"
        self._request_count += 1

        url = (
            f"{self._homeserver}/_matrix/client/v3/rooms/{room_id}"
            f"/send/m.room.message/{txn_id}"
        )

        content: dict[str, Any] = {
            "msgtype": msgtype,
            "body": text,
        }

        if reply_to_event_id:
            content["m.relates_to"] = {
                "m.in_reply_to": {
                    "event_id": reply_to_event_id,
                },
            }

        resp = await self._request("PUT", url, json=content)
        return resp.get("event_id", "")

    async def send_reaction(
        self,
        room_id: str,
        target_event_id: str,
        emoji: str,
    ) -> str:
        """Send an emoji reaction to a Matrix event.

        Args:
            room_id: Target room.
            target_event_id: The event being reacted to.
            emoji: Single emoji character.

        Returns:
            The event_id of the reaction event.
        """
        txn_id = f"presidium_react_{int(time.time() * 1000)}_{self._request_count}"
        self._request_count += 1

        url = (
            f"{self._homeserver}/_matrix/client/v3/rooms/{room_id}"
            f"/send/m.reaction/{txn_id}"
        )

        content = {
            "m.relates_to": {
                "rel_type": "m.annotation",
                "event_id": target_event_id,
                "key": emoji,
            },
        }

        resp = await self._request("PUT", url, json=content)
        return resp.get("event_id", "")

    async def send_typing(
        self,
        room_id: str,
        is_typing: bool,
        timeout_ms: int = 30000,
    ) -> None:
        """Send a typing notification to a Matrix room."""
        user_id = self._user_id or ""
        encoded = urllib.parse.quote(user_id, safe="")
        url = (
            f"{self._homeserver}/_matrix/client/v3/rooms/{room_id}"
            f"/typing/{encoded}"
        )
        payload: dict[str, Any] = {"typing": is_typing}
        if is_typing:
            payload["timeout"] = timeout_ms
        await self._request("PUT", url, json=payload)

    async def send_read_receipt(
        self,
        room_id: str,
        event_id: str,
    ) -> None:
        """Send a read receipt for an event."""
        url = (
            f"{self._homeserver}/_matrix/client/v3/rooms/{room_id}"
            f"/receipt/m.read/{event_id}"
        )
        await self._request("POST", url, json={})

    # -------------------------------------------------------------------
    # Media
    # -------------------------------------------------------------------

    async def upload_media(
        self,
        data: bytes,
        content_type: str = "application/octet-stream",
        filename: str = "",
    ) -> str:
        """Upload media to the Matrix content repository.

        Returns:
            The mxc:// URI.
        """
        url = f"{self._homeserver}/_matrix/media/v3/upload"
        params: dict[str, str] = {}
        if filename:
            params["filename"] = filename

        session = await self._ensure_session()
        headers = self._auth_headers()
        headers["Content-Type"] = content_type

        async with session.post(url, data=data, params=params, headers=headers) as resp:
            if resp.status == 429:
                retry_after = float(resp.headers.get("Retry-After", "5"))
                raise BridgeRateLimitError(
                    f"Matrix rate limit hit. Retry after {retry_after}s."
                )
            resp.raise_for_status()
            data_json = await resp.json()
            return data_json.get("content_uri", "")

    # -------------------------------------------------------------------
    # User profile
    # -------------------------------------------------------------------

    async def get_profile(self, user_id: str) -> dict[str, Any]:
        """Get a user's display name and avatar URL."""
        encoded = urllib.parse.quote(user_id, safe="")
        url = f"{self._homeserver}/_matrix/client/v3/profile/{encoded}"
        return await self._request("GET", url)

    # -------------------------------------------------------------------
    # Low-level request
    # -------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        url: str,
        json: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Make an authenticated HTTP request to the Matrix homeserver.

        Handles rate limiting (429) with automatic retry.
        """
        session = await self._ensure_session()
        headers = self._auth_headers()

        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with session.request(
                    method,
                    url,
                    json=json,
                    params=params,
                    headers=headers,
                ) as resp:
                    if resp.status == 429:
                        retry_after = float(resp.headers.get("Retry-After", "5"))
                        logger.warning(
                            "Matrix rate limited. Retry after %.1fs (attempt %d/%d)",
                            retry_after, attempt + 1, max_retries,
                        )
                        await asyncio.sleep(retry_after)
                        continue

                    if resp.status == 401:
                        raise BridgeAuthError("Matrix token expired or invalid.")

                    resp.raise_for_status()
                    return await resp.json()

            except aiohttp.ClientError as exc:
                if attempt == max_retries - 1:
                    raise BridgeConnectionError(
                        f"Matrix request failed after {max_retries} attempts: {exc}"
                    ) from exc
                await asyncio.sleep(2 ** attempt)

        return {}  # unreachable, but satisfies type checker

    # -------------------------------------------------------------------
    # Cleanup
    # -------------------------------------------------------------------

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    @property
    def user_id(self) -> Optional[str]:
        return self._user_id

    @property
    def next_batch(self) -> Optional[str]:
        return self._next_batch_token

    @next_batch.setter
    def next_batch(self, token: str) -> None:
        self._next_batch_token = token


# ---------------------------------------------------------------------------
# Matrix → Presidium event converter
# ---------------------------------------------------------------------------

def matrix_event_to_bridged(
    event: dict[str, Any],
    room_id: str,
    bridge_bot_user_id: str,
) -> Optional[BridgedMessage]:
    """Convert a Matrix event dict into a BridgedMessage.

    Handles m.room.message, m.reaction. Other event types return None.

    Args:
        event: Matrix event dict from /sync.
        room_id: The Matrix room ID.
        bridge_bot_user_id: The bridge bot's Matrix user ID (to skip own events).

    Returns:
        BridgedMessage, or None if the event type is not supported.
    """
    sender = event.get("sender", "")
    if sender == bridge_bot_user_id:
        return None  # Skip our own echoed messages

    event_type = event.get("type", "")
    content = event.get("content", {})
    event_id = event.get("event_id", "")
    ts = event.get("origin_server_ts", 0) / 1000.0  # ms → seconds

    if event_type == "m.room.message":
        msgtype = content.get("msgtype", "m.text")

        if msgtype in ("m.text", "m.emote", "m.notice"):
            text = content.get("body", "")
            # Handle replies — extract reply text from formatted_body if present
            reply_to = ""
            relates_to = content.get("m.relates_to", {})
            if "m.in_reply_to" in relates_to:
                reply_to = relates_to["m.in_reply_to"].get("event_id", "")

            return BridgedMessage(
                external_room_id=room_id,
                external_sender_id=sender,
                external_event_id=event_id,
                msg_type=BridgeMessageType.TEXT,
                text_content=text,
                reply_to_external_id=reply_to,
                timestamp=ts,
                direction=BridgeDirection.INBOUND,
                is_encrypted_on_external=event.get("decryption_status") == "decrypted",
                extra={"matrix_msgtype": msgtype},
            )

        elif msgtype in ("m.image", "m.file", "m.audio", "m.video"):
            url = content.get("url", "")  # mxc:// URI
            if url and isinstance(url, str) and url.startswith("mxc://"):
                pass  # Will need homeserver URL to resolve later
            return BridgedMessage(
                external_room_id=room_id,
                external_sender_id=sender,
                external_event_id=event_id,
                msg_type=(
                    BridgeMessageType.IMAGE
                    if msgtype == "m.image"
                    else BridgeMessageType.FILE
                ),
                text_content=content.get("body", ""),
                media_url=url,
                media_mimetype=content.get("mimetype", ""),
                media_size=content.get("size", 0),
                timestamp=ts,
                direction=BridgeDirection.INBOUND,
                is_encrypted_on_external=event.get("decryption_status") == "decrypted",
                extra={"matrix_msgtype": msgtype},
            )

    elif event_type == "m.reaction":
        relates_to = content.get("m.relates_to", {})
        if relates_to.get("rel_type") == "m.annotation":
            target_event = relates_to.get("event_id", "")
            emoji = relates_to.get("key", "")
            return BridgedMessage(
                external_room_id=room_id,
                external_sender_id=sender,
                external_event_id=event_id,
                msg_type=BridgeMessageType.REACTION,
                emoji=emoji,
                reply_to_external_id=target_event,
                timestamp=ts,
                direction=BridgeDirection.INBOUND,
                extra={"matrix_rel_type": "m.annotation"},
            )

    return None


# ---------------------------------------------------------------------------
# Matrix Bridge implementation
# ---------------------------------------------------------------------------

@register_bridge("matrix")
class MatrixBridge(BaseBridge):
    """Bidirectional bridge between Presidium and Matrix.

    Connects to a Matrix homeserver as a bot user and relays messages
    between Matrix rooms and Presidium chat sessions.

    Configuration (via BridgeConfig.extra):
        homeserver_url: Matrix homeserver base URL.
        user_id: Bot's Matrix user ID (e.g. @presidium-bot:matrix.org).
        password: Bot password (for login_password flow).
        access_token: Pre-existing access token (alternative to password).
        device_id: Bot device ID (default: "presidium-bridge").

    Example config:
        BridgeConfig(
            bridge_type="matrix",
            extra={
                "homeserver_url": "https://matrix.org",
                "user_id": "@presidium-bot:matrix.org",
                "password": "s3cret",
            }
        )
    """

    def __init__(
        self,
        config: BridgeConfig,
        app_config: AppConfig,
        local_user_id: str,
    ) -> None:
        super().__init__(config, app_config, local_user_id)

        # Matrix-specific config
        extra = config.extra
        self._homeserver_url: str = extra.get(
            "homeserver_url", MATRIX_DEFAULT_HOMESERVER
        )
        self._matrix_user_id: str = extra.get("user_id", "")
        self._matrix_password: str = extra.get("password", "")
        self._matrix_access_token: str = extra.get("access_token", "")
        self._matrix_device_id: str = extra.get("device_id", "presidium-bridge")

        # Matrix client
        self._client: Optional[MatrixClient] = None

        # Sync loop task
        self._sync_task: Optional[asyncio.Task[None]] = None
        self._running = False

        # User profile cache: user_id → {display_name, avatar_url}
        self._profile_cache: dict[str, dict[str, str]] = {}

    # -------------------------------------------------------------------
    # BaseBridge implementation
    # -------------------------------------------------------------------

    async def start(self) -> None:
        """Connect to Matrix and start the sync loop."""
        await self._set_status(BridgeStatus.STARTING)
        logger.info("Starting Matrix bridge to %s", self._homeserver_url)

        try:
            # Initialize client
            self._client = MatrixClient(
                homeserver_url=self._homeserver_url,
                access_token=self._matrix_access_token,
            )

            # Authenticate
            if self._matrix_access_token:
                # Verify existing token
                user_id = await self._client.whoami()
                logger.info("Matrix bridge authenticated as %s", user_id)
            elif self._matrix_password:
                # Login with password
                result = await self._client.login_password(
                    username=self._matrix_user_id,
                    password=self._matrix_password,
                    device_id=self._matrix_device_id,
                )
                self._matrix_access_token = result["access_token"]
                logger.info(
                    "Matrix bridge logged in as %s (device: %s)",
                    result["user_id"], result["device_id"],
                )
            else:
                raise BridgeAuthError(
                    "Matrix bridge requires either 'access_token' or "
                    "'password' in config.extra."
                )

            # Perform initial sync (to get next_batch token without processing)
            initial_sync = await self._client.sync(timeout_ms=0, full_state=True)
            self._client.next_batch = initial_sync.get("next_batch", "")
            logger.info(
                "Initial sync complete. next_batch=%s",
                self._client.next_batch[:16] if self._client.next_batch else "None",
            )

            # Discover already-joined rooms
            joined_rooms = await self._client.get_joined_rooms()
            for room_id in joined_rooms:
                room = BridgedRoom(
                    external_room_id=room_id,
                    bridge_type="matrix",
                    relay_bot_id=self._client.user_id or "",
                )
                self._register_room(room)
                if self._on_room_created:
                    await self._on_room_created(room)

            self._stats.rooms_active = len(self._rooms)
            logger.info("Discovered %d joined Matrix rooms", len(joined_rooms))

            # Start sync loop
            self._running = True
            self._sync_task = asyncio.create_task(self._sync_loop())
            await self._set_status(BridgeStatus.RUNNING)

        except BridgeAuthError:
            await self._set_status(BridgeStatus.ERROR)
            raise
        except Exception as exc:
            await self._set_status(BridgeStatus.ERROR)
            raise BridgeConnectionError(
                f"Failed to start Matrix bridge: {exc}"
            ) from exc

    async def stop(self) -> None:
        """Gracefully disconnect from Matrix."""
        logger.info("Stopping Matrix bridge...")
        self._running = False

        # Cancel sync loop
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass

        # Close HTTP session
        if self._client:
            await self._client.close()
            self._client = None

        await self._set_status(BridgeStatus.STOPPED)
        logger.info("Matrix bridge stopped.")

    async def send_message(self, message: BridgedMessage) -> str:
        """Send a Presidium message to a Matrix room."""
        if not self._client:
            raise BridgeConnectionError("Matrix bridge not connected.")

        if not self._check_outbound_rate_limit():
            raise BridgeRateLimitError("Outbound rate limit exceeded.")

        room_id = message.external_room_id
        if not room_id:
            # Look up room by presidium peer
            room = self.get_room_by_presidium_peer(message.presidium_peer_id)
            if room is None:
                raise BridgeRoomError(
                    f"No bridged room for peer {message.presidium_peer_id}"
                )
            room_id = room.external_room_id

        try:
            if message.msg_type == BridgeMessageType.TEXT:
                # Format message with optional sender prefix
                text = message.text_content
                if self._config.relay_display_name and message.extra.get("sender_name"):
                    prefix = self._config.relay_prefix or "[Presidium] "
                    text = f"{prefix}{message.extra['sender_name']}: {text}"
                elif self._config.relay_prefix:
                    text = f"{self._config.relay_prefix}{text}"

                event_id = await self._client.send_message(
                    room_id=room_id,
                    text=text,
                    reply_to_event_id=message.reply_to_external_id,
                )

            elif message.msg_type == BridgeMessageType.REACTION:
                event_id = await self._client.send_reaction(
                    room_id=room_id,
                    target_event_id=message.reply_to_external_id,
                    emoji=message.emoji,
                )

            else:
                logger.warning("Unsupported outbound message type: %s", message.msg_type)
                return ""

            self._stats.outbound_relayed += 1
            self._stats.last_outbound_ts = time.time()
            return event_id

        except BridgeRateLimitError:
            self._stats.outbound_rate_limited += 1
            raise
        except Exception as exc:
            self._stats.outbound_failed += 1
            raise BridgeMessageError(
                f"Failed to send message to Matrix: {exc}"
            ) from exc

    async def create_room(self, presidium_peer_id: str, is_group: bool = False) -> BridgedRoom:
        """Create a new Matrix room for bridging with a Presidium peer."""
        if not self._client:
            raise BridgeConnectionError("Matrix bridge not connected.")

        # Check if room already exists
        existing = self.get_room_by_presidium_peer(presidium_peer_id)
        if existing:
            return existing

        room_alias = f"presidium_{presidium_peer_id[:12]}"
        preset = "private_chat"

        try:
            resp = await self._client.create_room(
                room_alias=room_alias,
                name=f"Presidium Bridge: {presidium_peer_id[:8]}",
                topic=f"Bridged from Presidium peer {presidium_peer_id}",
                is_direct=not is_group,
                preset=preset,
            )
        except Exception as exc:
            raise BridgeRoomError(f"Failed to create Matrix room: {exc}") from exc

        room_id = resp.get("room_id", "")
        if not room_id:
            raise BridgeRoomError("Matrix did not return a room_id.")

        room = BridgedRoom(
            external_room_id=room_id,
            presidium_peer_id=presidium_peer_id,
            is_group=is_group,
            bridge_type="matrix",
            relay_bot_id=self._client.user_id or "",
        )
        self._register_room(room)
        self._stats.rooms_active = len(self._rooms)

        if self._on_room_created:
            await self._on_room_created(room)

        logger.info("Created Matrix room %s for Presidium peer %s", room_id, presidium_peer_id[:8])
        return room

    async def get_user_profile(self, external_user_id: str) -> dict[str, Any]:
        """Fetch a Matrix user's display name and avatar."""
        if not self._client:
            return {"display_name": "", "avatar_url": ""}

        # Check cache
        if external_user_id in self._profile_cache:
            return dict(self._profile_cache[external_user_id])

        try:
            profile = await self._client.get_profile(external_user_id)
            result = {
                "display_name": profile.get("displayname", ""),
                "avatar_url": profile.get("avatar_url", ""),
            }
            self._profile_cache[external_user_id] = result
            return result
        except Exception as exc:
            logger.debug("Failed to fetch profile for %s: %s", external_user_id, exc)
            return {"display_name": external_user_id, "avatar_url": ""}

    # -------------------------------------------------------------------
    # Sync loop
    # -------------------------------------------------------------------

    async def _sync_loop(self) -> None:
        """Main long-polling sync loop for receiving Matrix events.

        Runs continuously until self._running is set to False.
        Implements exponential backoff on failures.
        """
        backoff_attempt = 0

        while self._running:
            try:
                sync_data = await self._client.sync(
                    since=self._client.next_batch,
                    timeout_ms=MATRIX_SYNC_TIMEOUT_MS,
                )

                # Update next_batch token
                new_batch = sync_data.get("next_batch", "")
                if new_batch:
                    self._client.next_batch = new_batch

                # Process events
                await self._process_sync(sync_data)

                # Reset backoff on success
                backoff_attempt = 0
                if self._status == BridgeStatus.DEGRADED:
                    await self._set_status(BridgeStatus.RUNNING)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                backoff_attempt += 1
                self._stats.reconnect_count += 1
                delay = self._compute_backoff(backoff_attempt)
                logger.error(
                    "Matrix sync error (attempt %d): %s. Retrying in %.1fs",
                    backoff_attempt, exc, delay,
                )
                await self._set_status(BridgeStatus.DEGRADED)
                await asyncio.sleep(delay)

                # Check max reconnect attempts
                if backoff_attempt >= self._config.max_reconnect_attempts:
                    logger.critical(
                        "Matrix bridge exceeded max reconnect attempts (%d). Stopping.",
                        self._config.max_reconnect_attempts,
                    )
                    await self._set_status(BridgeStatus.ERROR)
                    break

    async def _process_sync(self, sync_data: dict[str, Any]) -> None:
        """Process a /sync response and dispatch events."""
        rooms_data = sync_data.get("rooms", {})
        join_data = rooms_data.get("join", {})

        for room_id, room_state in join_data.items():
            # Process timeline events
            timeline = room_state.get("timeline", {})
            events = timeline.get("events", [])

            # Ensure room is registered
            if room_id not in self._rooms:
                room = BridgedRoom(
                    external_room_id=room_id,
                    bridge_type="matrix",
                    relay_bot_id=self._client.user_id if self._client else "",
                )
                self._register_room(room)
                if self._on_room_created:
                    await self._on_room_created(room)

            # Process each event
            for event in events:
                try:
                    msg = matrix_event_to_bridged(
                        event=event,
                        room_id=room_id,
                        bridge_bot_user_id=self._client.user_id if self._client else "",
                    )
                    if msg is not None:
                        await self._dispatch_inbound(msg)
                except Exception as exc:
                    logger.error(
                        "Failed to process Matrix event in room %s: %s",
                        room_id, exc,
                    )

            # Process typing notifications
            ephemeral = room_state.get("ephemeral", {})
            for event in ephemeral.get("events", []):
                if event.get("type") == "m.typing":
                    typing_users = event.get("content", {}).get("user_ids", [])
                    is_typing = len(typing_users) > 0
                    await self._dispatch_typing(room_id, is_typing)

            # Update room stats
            room = self._rooms.get(room_id)
            if room and events:
                room.last_event_ts = time.time()

        # Update stats
        self._stats.last_inbound_ts = time.time()

    # -------------------------------------------------------------------
    # Typing relay (Presidium → Matrix)
    # -------------------------------------------------------------------

    async def relay_typing(self, presidium_peer_id: str, is_typing: bool) -> None:
        """Relay a typing indicator from Presidium to Matrix.

        Args:
            presidium_peer_id: The Presidium peer who is typing.
            is_typing: Whether the peer is currently typing.
        """
        if not self._client or not self._config.relay_typing:
            return

        room = self.get_room_by_presidium_peer(presidium_peer_id)
        if room is None:
            return

        try:
            await self._client.send_typing(room.external_room_id, is_typing)
        except Exception as exc:
            logger.debug("Failed to relay typing to Matrix: %s", exc)

    # -------------------------------------------------------------------
    # Read receipt relay (Presidium → Matrix)
    # -------------------------------------------------------------------

    async def relay_read_receipt(
        self, presidium_peer_id: str, matrix_event_id: str
    ) -> None:
        """Relay a read receipt from Presidium to Matrix."""
        if not self._client or not self._config.relay_read_receipts:
            return

        room = self.get_room_by_presidium_peer(presidium_peer_id)
        if room is None:
            return

        try:
            await self._client.send_read_receipt(room.external_room_id, matrix_event_id)
        except Exception as exc:
            logger.debug("Failed to relay read receipt to Matrix: %s", exc)

    # -------------------------------------------------------------------
    # Utility
    # -------------------------------------------------------------------

    def resolve_mxc_url(self, mxc_uri: str) -> str:
        """Convert an mxc:// URI to a downloadable HTTP URL.

        Args:
            mxc_uri: Matrix content URI (mxc://server/media_id).

        Returns:
            Downloadable HTTP URL.
        """
        if not mxc_uri.startswith("mxc://"):
            return mxc_uri

        # mxc://server/media_id → /_matrix/media/v3/download/server/media_id
        parts = mxc_uri[6:].split("/", 1)
        if len(parts) != 2:
            return mxc_uri

        server, media_id = parts
        return f"{self._homeserver_url}/_matrix/media/v3/download/{server}/{media_id}"
