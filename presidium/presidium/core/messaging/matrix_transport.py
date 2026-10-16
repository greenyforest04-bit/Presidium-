"""Matrix transport implementation for Presidium messaging.

This module introduces the first concrete transport abstraction required by the
product architecture: the UI and domain layer interact with a generic
``MessagingTransport`` interface, while Matrix-specific details stay behind the
transport boundary.

The implementation is intentionally narrow and security-focused: it does not
reimplement any crypto primitives. Instead, it builds real Matrix room payloads
and request bodies that enforce encrypted direct conversations with the official
Matrix room encryption flow.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote
import uuid
import aiohttp


@dataclass(slots=True)
class MatrixTransportConfig:
    """Runtime configuration for the Matrix transport."""

    homeserver_url: str = "https://matrix.example.com"
    access_token: str = ""
    username: str = ""
    password: str = ""
    device_id: str = ""
    encryption_algorithm: str = "m.megolm.v1.aes-sha2"
    rotation_period_ms: int = 86_400_000
    default_room_preset: str = "trusted_private_chat"
    rate_limit_per_minute: int = 60
    api_version: str = "v3"
    extra: dict[str, Any] = field(default_factory=dict)


class MessagingTransport(ABC):
    """Abstract transport interface for Presidium messaging services."""

    @abstractmethod
    def start(self) -> None:
        """Initialize the transport."""

    @abstractmethod
    def stop(self) -> None:
        """Stop the transport."""

    @abstractmethod
    async def send_message(self, conversation_id: str, body: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        """Send a message to a conversation."""

    @abstractmethod
    def edit_message(self, event_id: str, new_body: str) -> dict[str, Any]:
        """Edit an existing message."""

    @abstractmethod
    async def delete_message(self, event_id: str, *, conversation_id: str) -> dict[str, Any]:
        """Delete a message."""

    @abstractmethod
    def send_reaction(self, event_id: str, emoji: str) -> dict[str, Any]:
        """Send an emoji reaction."""

    @abstractmethod
    def send_file(self, conversation_id: str, file_path: str, **kwargs: Any) -> dict[str, Any]:
        """Send a file attachment."""

    @abstractmethod
    async def create_conversation(self, *, participants: list[str], title: str = "") -> dict[str, Any]:
        """Create or resolve a conversation."""

    @abstractmethod
    async def create_group(self, *, members: list[str], title: str) -> dict[str, Any]:
        """Create a group conversation."""

    @abstractmethod
    def subscribe_conversation(self, conversation_id: str) -> None:
        """Subscribe to conversation updates."""

    @abstractmethod
    async def mark_read(self, conversation_id: str, event_id: str) -> dict[str, Any]:
        """Mark a message as read."""

    @abstractmethod
    async def set_typing(self, conversation_id: str, is_typing: bool) -> dict[str, Any]:
        """Set or clear typing indicator."""

    @abstractmethod
    async def get_history(self, conversation_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Fetch recent conversation history."""

    @abstractmethod
    async def sync(self) -> dict[str, Any]:
        """Synchronize transport state."""


class MatrixTransport(MessagingTransport):
    """Matrix-backed concrete transport implementation.

    This transport intentionally keeps Matrix-specific HTTP details behind the
    generic ``MessagingTransport`` interface so that the Presidium UI remains
    transport-agnostic.
    """

    def __init__(self, config: MatrixTransportConfig | None = None) -> None:
        self.config = config or MatrixTransportConfig()
        self._started = False

    @property
    def started(self) -> bool:
        return self._started

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    async def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        headers = (
            {"Authorization": f"Bearer {self.config.access_token}"}
            if self.config.access_token
            else {}
        )
        url = f"{self.config.homeserver_url.rstrip('/')}/_matrix/client/{self.config.api_version}/{path}"
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                async with session.request(method, url, json=payload, headers=headers) as response:
                    result = await response.json()
                    if response.status >= 400:
                        raise MatrixRequestError(
                            response.status,
                            result.get("errcode", "M_UNKNOWN")
                            if isinstance(result, dict)
                            else "M_UNKNOWN",
                        )
                    return result
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise MatrixRequestError(503, "M_UNAVAILABLE") from exc

    async def create_conversation(
        self, *, participants: list[str], title: str = ""
    ) -> dict[str, Any]:
        if len(participants) != 2 or len(set(participants)) != 2:
            raise ValueError("A direct conversation requires two distinct participants.")
        payload = self.build_dm_creation_payload(
            local_user_id=participants[0], remote_user_id=participants[1], room_name=title
        )
        return await self.request("POST", "createRoom", payload)

    async def create_group(self, *, members: list[str], title: str) -> dict[str, Any]:
        return await self.request(
            "POST",
            "createRoom",
            {
                "name": title,
                "preset": "private_chat",
                "invite": members,
                "initial_state": [
                    {
                        "type": "m.room.encryption",
                        "state_key": "",
                        "content": {
                            "algorithm": self.config.encryption_algorithm,
                            "rotation_period_ms": self.config.rotation_period_ms,
                            "rotation_period_msgs": 100,
                        },
                    }
                ],
            },
        )

    def subscribe_conversation(self, conversation_id: str) -> None:
        raise NotImplementedError("Use sync() or a client SDK to receive room updates.")

    async def assert_direct_conversation(
        self, room_id: str, local_user: str, recipient: str
    ) -> None:
        # The endpoint returns an array, rather than the usual Matrix object.
        state = await self.request("GET", f"rooms/{quote(room_id, safe='')}/state")
        encryption = next(
            (e for e in state if e["type"] == "m.room.encryption" and e.get("state_key") == ""), {}
        )
        members = {
            e.get("state_key")
            for e in state
            if e["type"] == "m.room.member"
            and e.get("content", {}).get("membership") in ("join", "invite")
        }
        if (
            encryption.get("content", {}).get("algorithm") != self.config.encryption_algorithm
            or members != {local_user, recipient}
            or local_user == recipient
            or any(
                e["type"] == "m.room.member"
                and e.get("state_key") not in {local_user, recipient}
                for e in state
            )
        ):
            raise ValueError("Room must be encrypted and contain only the sender and recipient.")

    async def send_message(
        self, conversation_id: str, body: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        required = ("ciphertext", "sender_key", "session_id", "device_id")
        if (
            not isinstance(body, dict)
            or body.get("algorithm") != self.config.encryption_algorithm
            or any(not isinstance(body.get(k), str) or not body[k] for k in required)
        ):
            raise ValueError("Messages must be encrypted by the client SDK before submission.")
        if set(body) != {"algorithm", *required}:
            raise ValueError("Unexpected fields in encrypted event.")
        identity = await self.request("GET", "account/whoami")
        if body["device_id"] != identity.get("device_id"):
            raise ValueError("Encrypted event device does not match the authenticated session.")
        state = await self.request(
            "GET", f"rooms/{quote(conversation_id, safe='')}/state/m.room.encryption/"
        )
        if state.get("algorithm") != self.config.encryption_algorithm:
            raise ValueError("Room encryption is not enabled.")
        return await self.request(
            "PUT",
            f"rooms/{quote(conversation_id, safe='')}/send/m.room.encrypted/{uuid.uuid4().hex}",
            body,
        )

    def edit_message(self, event_id: str, new_body: str) -> dict[str, Any]:
        raise NotImplementedError(
            "Encrypt edits on the client and submit them with send_message()."
        )

    async def delete_message(self, event_id: str, *, conversation_id: str) -> dict[str, Any]:
        return await self.request(
            "PUT",
            f"rooms/{quote(conversation_id, safe='')}/redact/{quote(event_id, safe='')}/{uuid.uuid4().hex}",
            {},
        )

    def send_reaction(self, event_id: str, emoji: str) -> dict[str, Any]:
        raise NotImplementedError(
            "Encrypt reactions on the client and submit them with send_message()."
        )

    def send_file(self, conversation_id: str, file_path: str, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError("Encrypt and upload attachments with the client SDK.")

    async def mark_read(self, conversation_id: str, event_id: str) -> dict[str, Any]:
        return await self.request(
            "POST",
            f"rooms/{quote(conversation_id, safe='')}/read_markers",
            {"m.fully_read": event_id, "m.read": event_id},
        )

    async def set_typing(self, conversation_id: str, is_typing: bool) -> dict[str, Any]:
        identity = await self.request("GET", "account/whoami")
        return await self.request(
            "PUT",
            f"rooms/{quote(conversation_id, safe='')}/typing/{quote(identity['user_id'], safe='')}",
            {"typing": is_typing, "timeout": 1000},
        )

    async def get_history(self, conversation_id: str, limit: int = 50) -> list[dict[str, Any]]:
        result = await self.request(
            "GET",
            f"rooms/{quote(conversation_id, safe='')}/messages?dir=b&limit={max(1, min(limit, 100))}",
        )
        return result.get("chunk", [])

    async def sync(self) -> dict[str, Any]:
        return await self.request("GET", "sync?timeout=0")

    def build_dm_creation_payload(
        self,
        *,
        local_user_id: str,
        remote_user_id: str,
        room_name: str,
    ) -> dict[str, Any]:
        """Build a Matrix DM room payload that defaults to E2EE.

        This mirrors the security model required for Presidium: private direct
        conversations are encrypted by default and the room contains a room
        encryption event before messages are sent.
        """

        return {
            "name": room_name,
            "preset": self.config.default_room_preset,
            "invite": [remote_user_id],
            "is_direct": True,
            "initial_state": [
                {
                    "type": "m.room.encryption",
                    "content": {
                        "algorithm": self.config.encryption_algorithm,
                        "rotation_period_ms": self.config.rotation_period_ms,
                        "rotation_period_msgs": 100,
                    },
                },
            ],
        }

    def build_message_event(self, body: str, **kwargs: Any) -> dict[str, Any]:
        """Build plaintext content for a client SDK to encrypt; this is not a wire event."""
        event = {
            "msgtype": "m.text",
            "body": body,
            "org.matrix.msc1767.text": body,
        }
        event.update(kwargs)
        return event

    def build_registration_payload(
        self,
        *,
        username: str,
        password: str,
        display_name: str = "",
    ) -> dict[str, Any]:
        """Build Matrix registration payload for real account creation."""
        payload: dict[str, Any] = {
            "username": username,
            "password": password,
            "auth": {"type": "m.login.dummy"},
        }
        if display_name:
            payload["displayname"] = display_name
        return payload

    def build_login_payload(self, *, username: str, password: str) -> dict[str, Any]:
        """Build Matrix login payload for real authentication."""
        payload = {
            "type": "m.login.password",
            "identifier": {"type": "m.id.user", "user": username},
            "password": password,
        }
        if self.config.device_id:
            payload["device_id"] = self.config.device_id
        return payload


class MatrixRequestError(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
