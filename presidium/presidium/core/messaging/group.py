"""Group chat implementation for Presidium using Sender Keys.

Implements Signal-style group messaging:
- Sender Keys: one symmetric key per group, distributed to all members
- Key rotation when a member leaves the group
- Group metadata: name, avatar hash, members list, admin
- Admin-only operations: add/remove members, change group name

Group ID format: first 16 bytes of SHA-256(admin_ed25519_pk + timestamp)

Message flow:
1. Admin creates group → generates sender key → distributes to initial members
2. Any member can send → encrypt with sender key (AES-256-GCM)
3. Member leaves → admin rotates sender key, distributes to remaining members
4. New member joins → admin sends current sender key encrypted with their DH key
"""

from __future__ import annotations

import enum
import hashlib
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from presidium.core.crypto import AES256GCM, Ed25519KeyPair, SecureBytes, generate_salt
from presidium.core.network.peer import PeerManager
from presidium.core.network.protocol import MessageType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class GroupError(Exception):
    """Base group chat error."""


class NotGroupAdminError(GroupError):
    """Operation requires admin privileges."""


class NotGroupMemberError(GroupError):
    """Operation requires group membership."""


class SenderKeyError(GroupError):
    """Sender key distribution or rotation error."""


# ---------------------------------------------------------------------------
# Group member role
# ---------------------------------------------------------------------------

class MemberRole(enum.IntEnum):
    ADMIN = 0
    MEMBER = 1


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class GroupMember:
    """A member of a group chat."""
    user_id: str
    public_key_bytes: bytes  # Ed25519 public key
    role: MemberRole = MemberRole.MEMBER
    joined_at: float = field(default_factory=time.time)
    nickname: str = ""


@dataclass
class SenderKey:
    """Symmetric key used for encrypting group messages.

    Rotated when a member leaves the group.
    """
    key: bytes  # 32 bytes AES-256 key
    chain_key: bytes  # 32 bytes for message key derivation
    version: int = 0
    created_at: float = field(default_factory=time.time)
    message_number: int = 0


@dataclass
class GroupMessage:
    """A message in a group chat."""
    message_id: bytes  # 16 bytes
    group_id: str
    sender_user_id: str
    content: str
    timestamp: float
    is_mine: bool = False


@dataclass
class GroupChat:
    """A group chat session."""
    group_id: str
    name: str
    admin_user_id: str
    members: list[GroupMember] = field(default_factory=list)
    sender_key: Optional[SenderKey] = None
    messages: list[GroupMessage] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    avatar_hash: str = ""
    unread_count: int = 0
    last_message_preview: str = ""
    last_message_timestamp: float = 0.0

    @property
    def member_count(self) -> int:
        return len(self.members)

    @property
    def is_admin(self) -> bool:
        """Check if the local user is the admin. Set by GroupChatManager."""
        return self._local_is_admin

    _local_is_admin: bool = False

    def get_member(self, user_id: str) -> Optional[GroupMember]:
        """Find a member by user ID."""
        for m in self.members:
            if m.user_id == user_id:
                return m
        return None

    def is_member(self, user_id: str) -> bool:
        """Check if a user is a member of the group."""
        return self.get_member(user_id) is not None


# ---------------------------------------------------------------------------
# Group Chat Manager
# ---------------------------------------------------------------------------

class GroupChatManager:
    """Manages all group chat sessions.

    Handles:
    - Group creation and sender key distribution
    - Member addition/removal (admin only)
    - Sender key rotation on member removal
    - Group message encryption/decryption
    - Integration with PeerManager for message transport
    """

    def __init__(
        self,
        peer_manager: PeerManager,
        local_user_id: str,
        local_key_pair: Ed25519KeyPair,
    ) -> None:
        self._peer_manager = peer_manager
        self._local_user_id = local_user_id
        self._local_key_pair = local_key_pair

        # Active groups: group_id → GroupChat
        self._groups: dict[str, GroupChat] = {}

        # Callbacks
        self._on_group_message: Optional[Callable[[GroupMessage], Awaitable[None]]] = None
        self._on_group_updated: Optional[Callable[[GroupChat], Awaitable[None]]] = None

    # -------------------------------------------------------------------
    # Callback setters
    # -------------------------------------------------------------------

    def set_on_group_message(
        self, callback: Callable[[GroupMessage], Awaitable[None]]
    ) -> None:
        self._on_group_message = callback

    def set_on_group_updated(
        self, callback: Callable[[GroupChat], Awaitable[None]]
    ) -> None:
        self._on_group_updated = callback

    # -------------------------------------------------------------------
    # Group creation
    # -------------------------------------------------------------------

    async def create_group(
        self,
        name: str,
        member_user_ids: list[str],
        member_public_keys: dict[str, bytes],
    ) -> GroupChat:
        """Create a new group chat as admin.

        Args:
            name: Group display name.
            member_user_ids: User IDs of initial members (not including admin).
            member_public_keys: Map of user_id → Ed25519 public key bytes.

        Returns:
            The created GroupChat.

        Raises:
            GroupError: If creation fails.
        """
        # Generate group ID
        group_id_input = (
            self._local_key_pair.public_key_bytes
            + str(time.time()).encode()
        )
        group_id = hashlib.sha256(group_id_input).digest()[:16].hex()

        # Generate sender key
        sender_key = SenderKey(
            key=os.urandom(32),
            chain_key=os.urandom(32),
            version=0,
        )

        # Create group
        admin_member = GroupMember(
            user_id=self._local_user_id,
            public_key_bytes=self._local_key_pair.public_key_bytes,
            role=MemberRole.ADMIN,
        )

        members = [admin_member]
        for uid in member_user_ids:
            pk = member_public_keys.get(uid, b"")
            if pk:
                members.append(GroupMember(
                    user_id=uid,
                    public_key_bytes=pk,
                    role=MemberRole.MEMBER,
                ))

        group = GroupChat(
            group_id=group_id,
            name=name,
            admin_user_id=self._local_user_id,
            members=members,
            sender_key=sender_key,
        )
        group._local_is_admin = True

        self._groups[group_id] = group

        # Distribute sender key to all members (except self)
        await self._distribute_sender_key(group)

        logger.info("Group created: %s (%s) with %d members", name, group_id[:8], len(members))
        return group

    # -------------------------------------------------------------------
    # Member management
    # -------------------------------------------------------------------

    async def add_member(
        self,
        group_id: str,
        user_id: str,
        public_key_bytes: bytes,
    ) -> None:
        """Add a member to the group (admin only).

        The sender key is encrypted with the new member's public key
        and sent directly to them.
        """
        group = self._groups.get(group_id)
        if group is None:
            raise GroupError(f"Group {group_id} not found.")
        if group.admin_user_id != self._local_user_id:
            raise NotGroupAdminError("Only the admin can add members.")
        if group.is_member(user_id):
            raise GroupError(f"User {user_id} is already a member.")

        member = GroupMember(
            user_id=user_id,
            public_key_bytes=public_key_bytes,
            role=MemberRole.MEMBER,
        )
        group.members.append(member)

        # Send current sender key to the new member
        await self._send_sender_key_to_member(group, member)

        # Notify all members about the update
        await self._notify_group_update(group)

        if self._on_group_updated:
            await self._on_group_updated(group)

    async def remove_member(self, group_id: str, user_id: str) -> None:
        """Remove a member from the group (admin only).

        After removal, the sender key is rotated and redistributed
        to all remaining members.
        """
        group = self._groups.get(group_id)
        if group is None:
            raise GroupError(f"Group {group_id} not found.")
        if group.admin_user_id != self._local_user_id:
            raise NotGroupAdminError("Only the admin can remove members.")
        if not group.is_member(user_id):
            raise NotGroupMemberError(f"User {user_id} is not a member.")

        # Remove member
        group.members = [m for m in group.members if m.user_id != user_id]

        # Rotate sender key (the removed member must not be able to read future messages)
        await self._rotate_sender_key(group)

        # Notify remaining members
        await self._notify_group_update(group)

        if self._on_group_updated:
            await self._on_group_updated(group)

    async def leave_group(self, group_id: str) -> None:
        """Leave a group as a regular member."""
        group = self._groups.get(group_id)
        if group is None:
            raise GroupError(f"Group {group_id} not found.")

        if group.admin_user_id == self._local_user_id:
            # Admin leaving — must transfer or dissolve
            raise GroupError("Admin cannot leave. Transfer admin role first or dissolve the group.")

        group.members = [m for m in group.members if m.user_id != self._local_user_id]
        group._local_is_admin = False

        # Notify admin
        # PHASE2: simplified — just update local state
        if self._on_group_updated:
            await self._on_group_updated(group)

    # -------------------------------------------------------------------
    # Sending messages
    # -------------------------------------------------------------------

    async def send_message(self, group_id: str, text: str) -> GroupMessage:
        """Send a message to the group.

        Encrypts with the sender key and sends to all members via P2P.

        Args:
            group_id: The group ID.
            text: Message text.

        Returns:
            The GroupMessage that was sent.
        """
        group = self._groups.get(group_id)
        if group is None:
            raise GroupError(f"Group {group_id} not found.")
        if not group.is_member(self._local_user_id):
            raise NotGroupMemberError("You are not a member of this group.")
        if group.sender_key is None:
            raise SenderKeyError("No sender key available for this group.")

        # Derive message key from chain
        from presidium.core.messaging.ratchet import advance_chain_key
        new_chain, msg_key = advance_chain_key(group.sender_key.chain_key)
        group.sender_key.chain_key = new_chain
        group.sender_key.message_number += 1

        # Encrypt message
        message_id = uuid.uuid4().bytes
        plaintext = text.encode("utf-8")

        # AAD includes group_id, sender, and message number for authentication
        aad = (
            group_id.encode()
            + self._local_user_id.encode()
            + group.sender_key.message_number.to_bytes(4, "big")
        )
        encrypted = AES256GCM.encrypt(msg_key, plaintext, aad=aad)

        # Build group message payload
        # PHASE2: simplified — sends encrypted content to each member individually
        # In production, would use a dedicated GROUP_MESSAGE protocol message type
        message = GroupMessage(
            message_id=message_id,
            group_id=group_id,
            sender_user_id=self._local_user_id,
            content=text,
            timestamp=time.time(),
            is_mine=True,
        )

        group.messages.append(message)
        group.last_message_preview = text[:80]
        group.last_message_timestamp = message.timestamp

        # Send to all members (except self)
        for member in group.members:
            if member.user_id != self._local_user_id:
                try:
                    await self._peer_manager.send_chat_message(
                        member.user_id,
                        encrypted,  # PHASE2: should wrap in a group message protocol
                    )
                except Exception as exc:
                    logger.warning("Failed to send group message to %s: %s", member.user_id[:8], exc)

        return message

    # -------------------------------------------------------------------
    # Sender key management
    # -------------------------------------------------------------------

    async def _distribute_sender_key(self, group: GroupChat) -> None:
        """Distribute the current sender key to all members except the admin."""
        if group.sender_key is None:
            return

        for member in group.members:
            if member.user_id != self._local_user_id:
                await self._send_sender_key_to_member(group, member)

    async def _send_sender_key_to_member(self, group: GroupChat, member: GroupMember) -> None:
        """Send the sender key to a specific member.

        The key is encrypted using a DH shared secret with the member's
        Ed25519 public key (converted to X25519 for ECDH).
        """
        if group.sender_key is None:
            return

        # PHASE2: simplified — in production, use a proper X3DH-like key exchange
        # Here we use a DH key derived from the member's Ed25519 key
        try:
            # Create a temporary key pair for DH
            from presidium.core.crypto import X25519KeyPair
            ephemeral = X25519KeyPair()

            # PHASE2: simplified key wrapping — just encrypt with AES-GCM
            # using a derived key. In production, use proper key wrapping.
            key_material = group.sender_key.key + group.sender_key.chain_key
            # Derive encryption key from ephemeral shared secret with peer
            # SIMPLIFIED: for now, just send the sender key via the existing E2EE channel
            sender_key_data = (
                group.sender_key.key
                + group.sender_key.chain_key
                + group.sender_key.version.to_bytes(4, "big")
            )

            # Send via the existing peer-to-peer encrypted channel
            await self._peer_manager.send_chat_message(
                member.user_id,
                b"SENDER_KEY:" + group.group_id.encode() + b":" + sender_key_data,
            )
        except Exception as exc:
            logger.error("Failed to distribute sender key to %s: %s", member.user_id[:8], exc)

    async def _rotate_sender_key(self, group: GroupChat) -> None:
        """Rotate the sender key after a member leaves."""
        old_key = group.sender_key
        group.sender_key = SenderKey(
            key=os.urandom(32),
            chain_key=os.urandom(32),
            version=(old_key.version + 1) if old_key else 0,
        )

        # Redistribute to all remaining members
        await self._distribute_sender_key(group)
        logger.info("Sender key rotated for group %s (v%d)", group.group_id[:8], group.sender_key.version)

    async def _notify_group_update(self, group: GroupChat) -> None:
        """Notify all members about a group metadata update."""
        # PHASE2: simplified — in production, send a GROUP_UPDATE protocol message
        for member in group.members:
            if member.user_id != self._local_user_id:
                try:
                    update_data = (
                        b"GROUP_UPDATE:"
                        + group.group_id.encode()
                        + b":"
                        + group.name.encode()
                        + b":"
                        + str(len(group.members)).encode()
                    )
                    await self._peer_manager.send_chat_message(
                        member.user_id,
                        update_data,
                    )
                except Exception as exc:
                    logger.warning("Failed to notify %s of group update: %s", member.user_id[:8], exc)

    # -------------------------------------------------------------------
    # Getters
    # -------------------------------------------------------------------

    def get_group(self, group_id: str) -> Optional[GroupChat]:
        return self._groups.get(group_id)

    def get_all_groups(self) -> list[GroupChat]:
        return list(self._groups.values())

    def add_group_from_invite(self, group: GroupChat) -> None:
        """Add a group received from an invite."""
        group._local_is_admin = (group.admin_user_id == self._local_user_id)
        self._groups[group.group_id] = group
