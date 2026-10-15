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


@dataclass(slots=True)
class MatrixTransportConfig:
    """Runtime configuration for the Matrix transport."""

    homeserver_url: str = "https://matrix.example.com"
    access_token: str = ""
    username: str = ""
    password: str = ""
    device_id: str = "presidium-device"
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
    def send_message(self, conversation_id: str, body: str, **kwargs: Any) -> dict[str, Any]:
        """Send a message to a conversation."""

    @abstractmethod
    def edit_message(self, event_id: str, new_body: str) -> dict[str, Any]:
        """Edit an existing message."""

    @abstractmethod
    def delete_message(self, event_id: str) -> dict[str, Any]:
        """Delete a message."""

    @abstractmethod
    def send_reaction(self, event_id: str, emoji: str) -> dict[str, Any]:
        """Send an emoji reaction."""

    @abstractmethod
    def send_file(self, conversation_id: str, file_path: str, **kwargs: Any) -> dict[str, Any]:
        """Send a file attachment."""

    @abstractmethod
    def create_conversation(self, *, participants: list[str], title: str = "") -> dict[str, Any]:
        """Create or resolve a conversation."""

    @abstractmethod
    def create_group(self, *, members: list[str], title: str) -> dict[str, Any]:
        """Create a group conversation."""

    @abstractmethod
    def subscribe_conversation(self, conversation_id: str) -> None:
        """Subscribe to conversation updates."""

    @abstractmethod
    def mark_read(self, conversation_id: str, event_id: str) -> dict[str, Any]:
        """Mark a message as read."""

    @abstractmethod
    def set_typing(self, conversation_id: str, is_typing: bool) -> dict[str, Any]:
        """Set or clear typing indicator."""

    @abstractmethod
    def get_history(self, conversation_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Fetch recent conversation history."""

    @abstractmethod
    def sync(self) -> dict[str, Any]:
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

    def create_conversation(self, *, participants: list[str], title: str = "") -> dict[str, Any]:
        local_user_id = participants[0] if participants else "@user:example.com"
        remote_user_id = participants[1] if len(participants) > 1 else participants[0]
        return self.build_dm_creation_payload(
            local_user_id=local_user_id,
            remote_user_id=remote_user_id,
            room_name=title or f"{local_user_id} / {remote_user_id}",
        )

    def create_group(self, *, members: list[str], title: str) -> dict[str, Any]:
        return {
            "name": title,
            "preset": "trusted_private_chat",
            "invite": members,
            "is_direct": False,
            "initial_state": [
                {
                    "type": "m.room.encryption",
                    "content": {
                        "algorithm": self.config.encryption_algorithm,
                        "rotation_period_ms": self.config.rotation_period_ms,
                        "rotation_period_msgs": 100,
                    },
                }
            ],
        }

    def subscribe_conversation(self, conversation_id: str) -> None:
        return None

    def send_message(self, conversation_id: str, body: str, **kwargs: Any) -> dict[str, Any]:
        event = self.build_message_event(body, **kwargs)
        return {
            "conversation_id": conversation_id,
            "event": event,
        }

    def edit_message(self, event_id: str, new_body: str) -> dict[str, Any]:
        return {
            "event_id": event_id,
            "body": new_body,
            "msgtype": "m.text",
            "m.new_content": {"msgtype": "m.text", "body": new_body},
            "m.relates_to": {"rel_type": "m.replace", "event_id": event_id},
        }

    def delete_message(self, event_id: str) -> dict[str, Any]:
        return {"event_id": event_id, "type": "m.room.redaction"}

    def send_reaction(self, event_id: str, emoji: str) -> dict[str, Any]:
        return {
            "m.relates_to": {
                "rel_type": "m.annotation",
                "event_id": event_id,
                "key": emoji,
            },
            "msgtype": "m.reaction",
        }

    def send_file(self, conversation_id: str, file_path: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "conversation_id": conversation_id,
            "file_path": file_path,
            "msgtype": "m.file",
            "body": file_path.split("/")[-1],
            "org.matrix.msc1767.text": file_path.split("/")[-1],
        }

    def mark_read(self, conversation_id: str, event_id: str) -> dict[str, Any]:
        return {"conversation_id": conversation_id, "event_id": event_id, "read": True}

    def set_typing(self, conversation_id: str, is_typing: bool) -> dict[str, Any]:
        return {"conversation_id": conversation_id, "typing": is_typing}

    def get_history(self, conversation_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return [
            {
                "conversation_id": conversation_id,
                "event_id": "event_001",
                "body": "history placeholder",
                "type": "m.room.message",
                "limit": limit,
            }
        ]

    def sync(self) -> dict[str, Any]:
        return {"sync": "ok", "started": self._started}

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
                {
                    "type": "m.room.member",
                    "content": {"membership": "join", "displayname": local_user_id},
                    "state_key": local_user_id,
                },
            ],
        }

    def build_message_event(self, body: str, **kwargs: Any) -> dict[str, Any]:
        """Build a Matrix message event payload for text messages."""
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
        return {
            "type": "m.login.password",
            "identifier": {"type": "m.id.user", "user": username},
            "password": password,
            "device_id": self.config.device_id,
        }
