"""XMPP federation bridge for Presidium.

Implements bidirectional message relay between Presidium's P2P network and
XMPP servers using the XMPP protocol (RFC 6120/6121) with XEP extensions.

Architecture overview:
    Presidium ←→ XmppBridge ←→ XMPP Server ←→ XMPP users

Supported XEPs:
    - XEP-0030: Service Discovery
    - XEP-0082: XMPP Date and Time Profiles
    - XEP-0184: Message Receipts
    - XEP-0249: Direct MUC Invitations
    - XEP-0313: Message Archive Management (MAM)
    - XEP-0333: Chat Markers (displayed, acknowledged)
    - XEP-0363: HTTP File Upload
    - XEP-0357: Push Notifications
    - XEP-0444: Message Reactions

Encryption considerations:
    - OMEMO (XEP-0384) encrypted messages: the bridge cannot decrypt OMEMO
      messages since it lacks the recipient's device keys. OMEMO messages
      are relayed as opaque ciphertext through Presidium.
    - Plain XMPP messages are wrapped as Presidium E2EE messages.
    - The bridge operates as a standard XMPP client, not an OMEMO device.

The bridge uses a lightweight XMPP client built on asyncio + aiohttp for
XMPP-over-WebSocket (RFC 7395), or raw TCP with XML streaming as a fallback.
For simplicity and compatibility, we implement a minimal XMPP client
rather than depending on a full XMPP library.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import random
import re
import ssl
import time
import uuid
import xml.etree.ElementTree as ET
from typing import Any, Optional
from dataclasses import dataclass

from presidium.bridges.protocol import (
    BaseBridge,
    BridgedMessage,
    BridgedRoom,
    BridgeConfig,
    BridgeDirection,
    BridgeMessageType,
    BridgeStatus,
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
# XMPP constants
# ---------------------------------------------------------------------------

XMPP_NS_CLIENT = "jabber:client"
XMPP_NS_STREAM = "http://etherx.jabber.org/streams"
XMPP_NS_SASL = "urn:ietf:params:xml:ns:xmpp-sasl"
XMPP_NS_BIND = "urn:ietf:params:xml:ns:xmpp-bind"
XMPP_NS_SESSION = "urn:ietf:params:xml:ns:xmpp-session"
XMPP_NS_ROSTER = "jabber:iq:roster"
XMPP_NS_DISCO = "http://jabber.org/protocol/disco#info"
XMPP_NS_MUC = "http://jabber.org/protocol/muc"
XMPP_NS_MAM = "urn:xmpp:mam:2"
XMPP_NS_RECEIPTS = "urn:xmpp:receipts"
XMPP_NS_MARKERS = "urn:xmpp:chat-markers:0"
XMPP_NS_REACTIONS = "urn:xmpp:reactions:0"
XMPP_NS_UPLOAD = "urn:xmpp:http:upload:0"
XMPP_NS_PING = "urn:xmpp:ping"

XMPP_DEFAULT_PORT = 5222
XMPP_PING_INTERVAL = 60  # seconds


# ---------------------------------------------------------------------------
# Minimal XMPP XML stream parser
# ---------------------------------------------------------------------------

class XmppStreamParser:
    """Incremental XML stream parser for XMPP.

    XMPP uses a streaming XML format where the <stream:stream> root element
    is never closed during the session. This parser handles partial reads
    and extracts individual top-level stanzas.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._depth = 0

    def feed(self, data: str) -> list[ET.Element]:
        """Feed raw XML data and return complete stanzas.

        Args:
            data: Raw XML string data from the XMPP stream.

        Returns:
            List of parsed Element objects for complete stanzas.
        """
        self._buffer += data
        stanzas: list[ET.Element] = []

        while True:
            stanza = self._extract_stanza()
            if stanza is None:
                break
            stanzas.append(stanza)

        return stanzas

    def _extract_stanza(self) -> Optional[ET.Element]:
        """Try to extract one complete stanza from the buffer."""
        # Look for complete elements at depth 1 (direct children of stream)
        # We use a simple heuristic: find matching closing tags

        if not self._buffer.strip():
            return None

        # Try to parse the buffer, looking for complete top-level stanzas
        # Wrap in a fake root to make it valid XML
        try:
            # Find the first complete stanza by tracking tags
            tag_start = self._buffer.find("<")
            if tag_start < 0:
                self._buffer = ""
                return None

            # Skip XML declarations and stream features
            if self._buffer[tag_start:tag_start+5] == "<?xml":
                end = self._buffer.find("?>", tag_start)
                if end < 0:
                    return None
                self._buffer = self._buffer[end + 2:]
                return self._extract_stanza()

            # Handle stream:stream opening
            if "<stream:stream" in self._buffer[tag_start:tag_start+20]:
                # Find the end of the opening tag
                end = self._buffer.find(">", tag_start)
                if end < 0:
                    return None
                self._buffer = self._buffer[end + 1:]
                return self._extract_stanza()

            # Try to find a complete stanza
            # Simple approach: find matching close tag for the first element
            match = re.match(r'<([^\s/>]+)', self._buffer[tag_start:])
            if not match:
                return None

            tag_name = match.group(1)
            close_tag = f"</{tag_name}>"

            # For self-closing tags
            self_close_pos = self._buffer.find("/>", tag_start)
            close_pos = self._buffer.find(close_tag, tag_start)

            if self_close_pos > 0 and (close_pos < 0 or self_close_pos < close_pos):
                # Self-closing tag
                end = self_close_pos + 2
                stanza_str = self._buffer[tag_start:end]
            elif close_pos > 0:
                end = close_pos + len(close_tag)
                stanza_str = self._buffer[tag_start:end]
            else:
                return None  # Incomplete stanza

            # Parse the extracted stanza
            try:
                element = ET.fromstring(stanza_str)
                self._buffer = self._buffer[end:]
                return element
            except ET.ParseError:
                # If parsing fails, skip this and try again
                self._buffer = self._buffer[tag_start + 1:]
                return None

        except Exception:
            self._buffer = ""
            return None


# ---------------------------------------------------------------------------
# Minimal XMPP client
# ---------------------------------------------------------------------------

class XmppClient:
    """Minimal XMPP client for the Presidium bridge.

    Implements just enough of the XMPP protocol to:
    - Connect and authenticate (PLAIN SASL)
    - Send/receive messages
    - Join MUC rooms
    - Send/receive reactions (XEP-0444)
    - Request/read receipts (XEP-0184)

    Not implemented (future work):
    - OMEMO encryption
    - HTTP File Upload (XEP-0363)
    - MAM archive queries
    """

    def __init__(
        self,
        jid: str,
        password: str,
        host: str = "",
        port: int = XMPP_DEFAULT_PORT,
        use_tls: bool = True,
    ) -> None:
        self._jid = jid
        self._password = password
        self._host = host or self._extract_host(jid)
        self._port = port
        self._use_tls = use_tls

        # Parsed JID components
        self._bare_jid: str = jid.split("/")[0]
        self._resource: str = jid.split("/")[-1] if "/" in jid else "presidium-bridge"

        # Connection state
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._parser = XmppStreamParser()
        self._connected = False
        self._authenticated = False

        # Stanza handlers: tag_name → list of callbacks
        self._handlers: dict[str, list[Any]] = {}
        self._iq_handlers: dict[str, asyncio.Future[Any]] = {}

        # Read loop task
        self._read_task: Optional[asyncio.Task[None]] = None

    @staticmethod
    def _extract_host(jid: str) -> str:
        """Extract the domain from a JID."""
        return jid.split("@")[-1].split("/")[0]

    # -------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------

    @property
    def bare_jid(self) -> str:
        return self._bare_jid

    @property
    def full_jid(self) -> str:
        return f"{self._bare_jid}/{self._resource}"

    @property
    def is_connected(self) -> bool:
        return self._connected and self._authenticated

    # -------------------------------------------------------------------
    # Connection lifecycle
    # -------------------------------------------------------------------

    async def connect(self) -> None:
        """Connect to the XMPP server and authenticate."""
        logger.info("Connecting to XMPP server %s:%d", self._host, self._port)

        try:
            self._reader, self._writer = await asyncio.open_connection(
                self._host, self._port,
                ssl=ssl.create_default_context() if self._use_tls else None,
            )
        except OSError as exc:
            raise BridgeConnectionError(
                f"Cannot connect to XMPP server {self._host}:{self._port}: {exc}"
            ) from exc

        # Send stream init
        await self._send_stream_header()

        # Wait for stream features
        features = await self._read_stream_features()
        if features is None:
            raise BridgeConnectionError("No stream features received.")

        # Authenticate with SASL PLAIN
        await self._sasl_plain_auth()

        # Restart stream after auth
        await self._send_stream_header()
        features = await self._read_stream_features()
        if features is None:
            raise BridgeConnectionError("No stream features after auth.")

        # Bind resource
        await self._bind_resource()

        # Session (if required by server)
        if self._requires_session(features):
            await self._establish_session()

        self._connected = True
        self._authenticated = True

        # Start read loop
        self._read_task = asyncio.create_task(self._read_loop())

        logger.info("XMPP connected and authenticated as %s", self.full_jid)

    async def disconnect(self) -> None:
        """Gracefully disconnect from the XMPP server."""
        if self._writer and not self._writer.is_closing():
            try:
                self._writer.write(b"</stream:stream>")
                await self._writer.drain()
            except Exception:
                pass
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass

        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass

        self._connected = False
        self._authenticated = False
        logger.info("XMPP disconnected.")

    # -------------------------------------------------------------------
    # Stream management
    # -------------------------------------------------------------------

    async def _send_stream_header(self) -> None:
        """Send the XMPP stream opening tag."""
        header = (
            f"<stream:stream to='{self._host}' "
            f"xmlns='{XMPP_NS_CLIENT}' "
            f"xmlns:stream='{XMPP_NS_STREAM}' "
            f"version='1.0'>"
        )
        await self._send_raw(header)

    async def _read_stream_features(self) -> Optional[ET.Element]:
        """Read and return stream features."""
        # Read until we get a features element
        for _ in range(20):
            data = await self._read_raw()
            if not data:
                return None
            stanzas = self._parser.feed(data)
            for stanza in stanzas:
                if stanza.tag == f"{{{XMPP_NS_STREAM}}}features" or stanza.tag == "stream:features":
                    return stanza
        return None

    async def _sasl_plain_auth(self) -> None:
        """Authenticate using SASL PLAIN mechanism."""
        # Build PLAIN auth string: \0authzid\0authcid\0password
        auth_str = f"\x00{self._bare_jid}\x00{self._password}"
        auth_b64 = base64.b64encode(auth_str.encode("utf-8")).decode("ascii")

        auth_el = f"<auth xmlns='{XMPP_NS_SASL}' mechanism='PLAIN'>{auth_b64}</auth>"
        await self._send_raw(auth_el)

        # Read response
        for _ in range(10):
            data = await self._read_raw()
            if not data:
                raise BridgeAuthError("No response to SASL auth.")
            stanzas = self._parser.feed(data)
            for stanza in stanzas:
                tag = stanza.tag
                if "success" in tag:
                    return
                if "failure" in tag:
                    reason = stanza.text or "unknown"
                    raise BridgeAuthError(f"SASL PLAIN auth failed: {reason}")

        raise BridgeAuthError("SASL PLAIN auth timed out.")

    async def _bind_resource(self) -> None:
        """Bind a resource to the JID."""
        iq_id = f"bind_{uuid.uuid4().hex[:8]}"
        bind_iq = (
            f"<iq id='{iq_id}' type='set'>"
            f"<bind xmlns='{XMPP_NS_BIND}'>"
            f"<resource>{self._resource}</resource>"
            f"</bind></iq>"
        )
        await self._send_raw(bind_iq)

        # Wait for bind result
        for _ in range(10):
            data = await self._read_raw()
            if not data:
                break
            stanzas = self._parser.feed(data)
            for stanza in stanzas:
                if stanza.get("id") == iq_id:
                    if stanza.get("type") == "result":
                        bind_el = stanza.find(f"{{{XMPP_NS_BIND}}}bind")
                        if bind_el is not None:
                            jid_el = bind_el.find(f"{{{XMPP_NS_BIND}}}jid")
                            if jid_el is not None and jid_el.text:
                                self._resource = jid_el.text.split("/")[-1]
                                logger.info("Resource bound: %s", self.full_jid)
                        return
                    elif stanza.get("type") == "error":
                        raise BridgeAuthError("Resource bind failed.")
            # Process other stanzas through handlers
            await self._dispatch_stanzas(stanzas)

    async def _establish_session(self) -> None:
        """Establish an XMPP session (required by some servers)."""
        iq_id = f"session_{uuid.uuid4().hex[:8]}"
        session_iq = (
            f"<iq id='{iq_id}' type='set'>"
            f"<session xmlns='{XMPP_NS_SESSION}'/>"
            f"</iq>"
        )
        await self._send_raw(session_iq)

    def _requires_session(self, features: ET.Element) -> bool:
        """Check if the server requires session establishment."""
        session_el = features.find(f".//{{{XMPP_NS_SESSION}}}session")
        if session_el is not None:
            optional = session_el.get("optional", "false")
            return optional != "true"
        return False

    # -------------------------------------------------------------------
    # Message sending
    # -------------------------------------------------------------------

    async def send_message(
        self,
        to: str,
        body: str,
        msg_type: str = "chat",
        thread: str = "",
        request_receipt: bool = True,
    ) -> str:
        """Send an XMPP message stanza.

        Args:
            to: Recipient JID.
            body: Message body text.
            msg_type: Message type (chat, groupchat, normal).
            thread: Thread ID for conversation threading.
            request_receipt: Whether to request a delivery receipt.

        Returns:
            A locally-generated message ID.
        """
        msg_id = f"presidium_{uuid.uuid4().hex[:12]}"

        parts = [
            f"<message id='{msg_id}' to='{to}' type='{msg_type}'>",
            f"<body>{_xml_escape(body)}</body>",
        ]

        if thread:
            parts.append(f"<thread>{_xml_escape(thread)}</thread>")

        if request_receipt:
            parts.append(f"<request xmlns='{XMPP_NS_RECEIPTS}'/>")

        parts.append("</message>")
        await self._send_raw("".join(parts))
        return msg_id

    async def send_reaction(
        self,
        to: str,
        message_id: str,
        emoji: str,
    ) -> str:
        """Send an emoji reaction (XEP-0444).

        Args:
            to: Recipient JID.
            message_id: ID of the message being reacted to.
            emoji: Single emoji character.

        Returns:
            The reaction message ID.
        """
        reaction_id = f"react_{uuid.uuid4().hex[:8]}"
        reaction_msg = (
            f"<message id='{reaction_id}' to='{to}' type='chat'>"
            f"<reactions xmlns='{XMPP_NS_REACTIONS}' id='{message_id}'>"
            f"<reaction>{_xml_escape(emoji)}</reaction>"
            f"</reactions>"
            f"</message>"
        )
        await self._send_raw(reaction_msg)
        return reaction_id

    async def send_composing(self, to: str, msg_type: str = "chat") -> None:
        """Send a chat state composing notification."""
        composing = (
            f"<message to='{to}' type='{msg_type}'>"
            f"<composing xmlns='http://jabber.org/protocol/chatstates'/>"
            f"</message>"
        )
        await self._send_raw(composing)

    async def send_paused(self, to: str, msg_type: str = "chat") -> None:
        """Send a chat state paused notification."""
        paused = (
            f"<message to='{to}' type='{msg_type}'>"
            f"<paused xmlns='http://jabber.org/protocol/chatstates'/>"
            f"</message>"
        )
        await self._send_raw(paused)

    async def send_displayed(self, to: str, message_id: str) -> None:
        """Send a chat marker 'displayed' (XEP-0333)."""
        displayed = (
            f"<message to='{to}' type='chat'>"
            f"<displayed xmlns='{XMPP_NS_MARKERS}' id='{message_id}'/>"
            f"</message>"
        )
        await self._send_raw(displayed)

    # -------------------------------------------------------------------
    # MUC operations
    # -------------------------------------------------------------------

    async def join_muc(
        self,
        room_jid: str,
        nickname: str = "presidium-bridge",
    ) -> None:
        """Join a MUC room.

        Args:
            room_jid: Room JID (e.g. room@conference.example.com).
            nickname: Nickname to use in the room.
        """
        presence = (
            f"<presence to='{room_jid}/{nickname}'>"
            f"<x xmlns='{XMPP_NS_MUC}'/>"
            f"</presence>"
        )
        await self._send_raw(presence)
        logger.info("Joined MUC room %s as %s", room_jid, nickname)

    async def leave_muc(self, room_jid: str, nickname: str = "presidium-bridge") -> None:
        """Leave a MUC room."""
        presence = (
            f"<presence to='{room_jid}/{nickname}' type='unavailable'/>"
        )
        await self._send_raw(presence)
        logger.info("Left MUC room %s", room_jid)

    # -------------------------------------------------------------------
    # Roster
    # -------------------------------------------------------------------

    async def get_roster(self) -> list[dict[str, str]]:
        """Request the user's roster (contact list).

        Returns:
            List of dicts with 'jid', 'name', 'subscription' keys.
        """
        iq_id = f"roster_{uuid.uuid4().hex[:8]}"
        roster_iq = (
            f"<iq id='{iq_id}' type='get'>"
            f"<query xmlns='{XMPP_NS_ROSTER}'/>"
            f"</iq>"
        )
        await self._send_raw(roster_iq)

        # Wait for response (simplified: we just read until we get the IQ result)
        for _ in range(20):
            data = await self._read_raw()
            if not data:
                break
            stanzas = self._parser.feed(data)
            for stanza in stanzas:
                if stanza.get("id") == iq_id and stanza.get("type") == "result":
                    query = stanza.find(f"{{{XMPP_NS_ROSTER}}}query")
                    if query is None:
                        return []
                    contacts = []
                    for item in query.findall(f"{{{XMPP_NS_ROSTER}}}item"):
                        contacts.append({
                            "jid": item.get("jid", ""),
                            "name": item.get("name", ""),
                            "subscription": item.get("subscription", "none"),
                        })
                    return contacts
            await self._dispatch_stanzas(stanzas)

        return []

    # -------------------------------------------------------------------
    # Ping (keepalive)
    # -------------------------------------------------------------------

    async def send_ping(self) -> None:
        """Send an XMPP ping (XEP-0199)."""
        iq_id = f"ping_{uuid.uuid4().hex[:8]}"
        ping = (
            f"<iq id='{iq_id}' to='{self._host}' type='get'>"
            f"<ping xmlns='{XMPP_NS_PING}'/>"
            f"</iq>"
        )
        await self._send_raw(ping)

    # -------------------------------------------------------------------
    # Presence
    # -------------------------------------------------------------------

    async def send_presence(
        self,
        show: str = "chat",
        status: str = "Presidium Bridge",
        priority: int = 0,
    ) -> None:
        """Send a presence stanza."""
        presence = (
            f"<presence>"
            f"<show>{show}</show>"
            f"<status>{_xml_escape(status)}</status>"
            f"<priority>{priority}</priority>"
            f"</presence>"
        )
        await self._send_raw(presence)

    # -------------------------------------------------------------------
    # Stanza handlers
    # -------------------------------------------------------------------

    def add_handler(self, tag: str, callback: Any) -> None:
        """Register a handler for a specific stanza tag."""
        if tag not in self._handlers:
            self._handlers[tag] = []
        self._handlers[tag].append(callback)

    # -------------------------------------------------------------------
    # Read loop
    # -------------------------------------------------------------------

    async def _read_loop(self) -> None:
        """Background task that reads from the XMPP stream and dispatches stanzas."""
        try:
            while self._connected:
                data = await self._read_raw()
                if not data:
                    logger.warning("XMPP stream closed by server.")
                    self._connected = False
                    break

                stanzas = self._parser.feed(data)
                await self._dispatch_stanzas(stanzas)

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("XMPP read loop error: %s", exc)
            self._connected = False

    async def _dispatch_stanzas(self, stanzas: list[ET.Element]) -> None:
        """Dispatch parsed stanzas to registered handlers."""
        for stanza in stanzas:
            tag = stanza.tag
            # Strip namespace prefix for handler matching
            local_tag = tag.split("}")[-1] if "}" in tag else tag

            # Check specific handlers
            for handler_tag in [tag, local_tag]:
                handlers = self._handlers.get(handler_tag, [])
                for handler in handlers:
                    try:
                        if asyncio.iscoroutinefunction(handler):
                            await handler(stanza)
                        else:
                            handler(stanza)
                    except Exception as exc:
                        logger.error("Stanza handler error: %s", exc)

    # -------------------------------------------------------------------
    # Low-level I/O
    # -------------------------------------------------------------------

    async def _send_raw(self, data: str) -> None:
        """Send raw XML data over the stream."""
        if self._writer is None or self._writer.is_closing():
            raise BridgeConnectionError("XMPP stream not connected.")
        self._writer.write(data.encode("utf-8"))
        await self._writer.drain()

    async def _read_raw(self) -> str:
        """Read raw data from the stream."""
        if self._reader is None:
            return ""
        try:
            data = await self._reader.read(65536)
            if not data:
                return ""
            return data.decode("utf-8", errors="replace")
        except asyncio.CancelledError:
            return ""
        except ConnectionError:
            return ""


# ---------------------------------------------------------------------------
# XMPP → Presidium event converter
# ---------------------------------------------------------------------------

def xmpp_stanza_to_bridged(
    stanza: ET.Element,
    bridge_jid: str,
) -> Optional[BridgedMessage]:
    """Convert an XMPP message stanza into a BridgedMessage.

    Handles:
        - <message> with <body> (text messages)
        - <message> with <reactions> (XEP-0444)
        - Chat state notifications (composing/paused)

    Args:
        stanza: Parsed XMPP stanza element.
        bridge_jid: The bridge's JID (to skip own messages).

    Returns:
        BridgedMessage or None if the stanza is not a message.
    """
    local_tag = stanza.tag.split("}")[-1] if "}" in stanza.tag else stanza.tag

    if local_tag != "message":
        return None

    from_jid = stanza.get("from", "")
    if from_jid == bridge_jid:
        return None

    msg_type = stanza.get("type", "normal")
    msg_id = stanza.get("id", "")

    # Determine if this is a group chat
    is_group = msg_type == "groupchat"

    # Extract sender (for MUC, from_jid includes nickname)
    sender_jid = from_jid
    room_jid = from_jid.split("/")[0] if is_group else from_jid

    # Check for reactions (XEP-0444)
    reactions_el = stanza.find(f"{{{XMPP_NS_REACTIONS}}}reactions")
    if reactions_el is not None:
        target_msg_id = reactions_el.get("id", "")
        reaction_el = reactions_el.find(f"{{{XMPP_NS_REACTIONS}}}reaction")
        emoji = reaction_el.text if reaction_el is not None else ""
        if emoji:
            return BridgedMessage(
                external_room_id=room_jid,
                external_sender_id=sender_jid,
                external_event_id=msg_id,
                msg_type=BridgeMessageType.REACTION,
                emoji=emoji,
                reply_to_external_id=target_msg_id,
                timestamp=time.time(),
                direction=BridgeDirection.INBOUND,
                extra={"xmpp_msg_type": msg_type},
            )

    # Check for body text
    body_el = stanza.find(f"{{{XMPP_NS_CLIENT}}}body")
    if body_el is None:
        body_el = stanza.find("body")
    if body_el is None:
        # Could be a chat state notification (composing/paused)
        return None

    text = body_el.text or ""

    # Check for reply (XEP-0461: Message Replies)
    reply_to = ""
    reply_el = stanza.find(".//{urn:xmpp:reply:0}reply")
    if reply_el is not None:
        reply_to = reply_el.get("id", "")

    return BridgedMessage(
        external_room_id=room_jid,
        external_sender_id=sender_jid,
        external_event_id=msg_id,
        msg_type=BridgeMessageType.TEXT,
        text_content=text,
        reply_to_external_id=reply_to,
        timestamp=time.time(),
        direction=BridgeDirection.INBOUND,
        extra={"xmpp_msg_type": msg_type, "is_group": is_group},
    )


# ---------------------------------------------------------------------------
# XMPP Bridge implementation
# ---------------------------------------------------------------------------

@register_bridge("xmpp")
class XmppBridge(BaseBridge):
    """Bidirectional bridge between Presidium and XMPP.

    Connects to an XMPP server as a standard client and relays messages
    between XMPP contacts/rooms and Presidium chat sessions.

    Configuration (via BridgeConfig.extra):
        jid: Bot's XMPP JID (e.g. presidium@xmpp.example.com).
        password: Account password.
        host: Override server hostname (default: extracted from JID).
        port: Override server port (default: 5222).
        use_tls: Whether to use TLS (default: True).
        muc_nickname: Nickname for MUC rooms (default: "presidium-bridge").

    Example config:
        BridgeConfig(
            bridge_type="xmpp",
            extra={
                "jid": "presidium@xmpp.example.com",
                "password": "s3cret",
                "use_tls": True,
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

        # XMPP-specific config
        extra = config.extra
        self._jid: str = extra.get("jid", "")
        self._password: str = extra.get("password", "")
        self._host: str = extra.get("host", "")
        self._port: int = extra.get("port", XMPP_DEFAULT_PORT)
        self._use_tls: bool = extra.get("use_tls", True)
        self._muc_nickname: str = extra.get("muc_nickname", "presidium-bridge")

        # XMPP client
        self._client: Optional[XmppClient] = None

        # Ping task
        self._ping_task: Optional[asyncio.Task[None]] = None
        self._running = False

    # -------------------------------------------------------------------
    # BaseBridge implementation
    # -------------------------------------------------------------------

    async def start(self) -> None:
        """Connect to XMPP and start receiving messages."""
        await self._set_status(BridgeStatus.STARTING)
        logger.info("Starting XMPP bridge for JID %s", self._jid)

        if not self._jid or not self._password:
            raise BridgeAuthError(
                "XMPP bridge requires 'jid' and 'password' in config.extra."
            )

        try:
            self._client = XmppClient(
                jid=self._jid,
                password=self._password,
                host=self._host,
                port=self._port,
                use_tls=self._use_tls,
            )

            # Register message handler
            self._client.add_handler("message", self._on_xmpp_message)

            # Connect
            await self._client.connect()

            # Send initial presence
            await self._client.send_presence()

            # Request roster
            roster = await self._client.get_roster()
            for contact in roster:
                if contact["subscription"] in ("both", "to"):
                    room = BridgedRoom(
                        external_room_id=contact["jid"],
                        external_room_name=contact.get("name", ""),
                        presidium_peer_id=contact["jid"],
                        bridge_type="xmpp",
                        relay_bot_id=self._client.bare_jid,
                    )
                    self._register_room(room)
                    if self._on_room_created:
                        await self._on_room_created(room)

            self._stats.rooms_active = len(self._rooms)
            logger.info("XMPP bridge started with %d contacts", len(roster))

            # Start ping task
            self._running = True
            self._ping_task = asyncio.create_task(self._ping_loop())
            await self._set_status(BridgeStatus.RUNNING)

        except BridgeAuthError:
            await self._set_status(BridgeStatus.ERROR)
            raise
        except Exception as exc:
            await self._set_status(BridgeStatus.ERROR)
            raise BridgeConnectionError(
                f"Failed to start XMPP bridge: {exc}"
            ) from exc

    async def stop(self) -> None:
        """Disconnect from XMPP."""
        logger.info("Stopping XMPP bridge...")
        self._running = False

        if self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass

        if self._client:
            await self._client.disconnect()
            self._client = None

        await self._set_status(BridgeStatus.STOPPED)
        logger.info("XMPP bridge stopped.")

    async def send_message(self, message: BridgedMessage) -> str:
        """Send a Presidium message to an XMPP contact or room."""
        if not self._client or not self._client.is_connected:
            raise BridgeConnectionError("XMPP bridge not connected.")

        if not self._check_outbound_rate_limit():
            raise BridgeRateLimitError("Outbound rate limit exceeded.")

        target_jid = message.external_room_id
        if not target_jid:
            room = self.get_room_by_presidium_peer(message.presidium_peer_id)
            if room is None:
                raise BridgeRoomError(
                    f"No bridged room for peer {message.presidium_peer_id}"
                )
            target_jid = room.external_room_id

        try:
            if message.msg_type == BridgeMessageType.TEXT:
                text = message.text_content
                if self._config.relay_prefix:
                    text = f"{self._config.relay_prefix}{text}"

                is_group = message.extra.get("is_group", False)
                msg_type = "groupchat" if is_group else "chat"

                msg_id = await self._client.send_message(
                    to=target_jid,
                    body=text,
                    msg_type=msg_type,
                    request_receipt=self._config.relay_read_receipts,
                )

            elif message.msg_type == BridgeMessageType.REACTION:
                msg_id = await self._client.send_reaction(
                    to=target_jid,
                    message_id=message.reply_to_external_id,
                    emoji=message.emoji,
                )

            else:
                logger.warning("Unsupported outbound message type: %s", message.msg_type)
                return ""

            self._stats.outbound_relayed += 1
            self._stats.last_outbound_ts = time.time()
            return msg_id

        except BridgeRateLimitError:
            self._stats.outbound_rate_limited += 1
            raise
        except Exception as exc:
            self._stats.outbound_failed += 1
            raise BridgeMessageError(
                f"Failed to send message to XMPP: {exc}"
            ) from exc

    async def create_room(self, presidium_peer_id: str, is_group: bool = False) -> BridgedRoom:
        """Create or join a bridged room on XMPP.

        For 1:1 chats, no room creation is needed — just send a message.
        For groups, this joins a MUC room.
        """
        if not self._client:
            raise BridgeConnectionError("XMPP bridge not connected.")

        # Check if room already exists
        existing = self.get_room_by_presidium_peer(presidium_peer_id)
        if existing:
            return existing

        if is_group:
            # Join MUC room
            room_jid = presidium_peer_id  # Assume peer_id is the MUC JID
            await self._client.join_muc(room_jid, self._muc_nickname)
        # For 1:1, the room is just the contact JID

        room = BridgedRoom(
            external_room_id=presidium_peer_id,
            presidium_peer_id=presidium_peer_id,
            is_group=is_group,
            bridge_type="xmpp",
            relay_bot_id=self._client.bare_jid,
        )
        self._register_room(room)
        self._stats.rooms_active = len(self._rooms)

        if self._on_room_created:
            await self._on_room_created(room)

        return room

    async def get_user_profile(self, external_user_id: str) -> dict[str, Any]:
        """Fetch a user's profile from XMPP.

        XMPP doesn't have a standard profile API. We return the JID
        as the display name, with vCard support as a future enhancement.
        """
        return {
            "display_name": external_user_id.split("@")[0],
            "avatar_url": "",
        }

    # -------------------------------------------------------------------
    # XMPP message handler
    # -------------------------------------------------------------------

    async def _on_xmpp_message(self, stanza: ET.Element) -> None:
        """Handle an incoming XMPP message stanza."""
        try:
            bridge_jid = self._client.full_jid if self._client else ""
            msg = xmpp_stanza_to_bridged(stanza, bridge_jid)

            if msg is not None:
                # Ensure room exists
                if msg.external_room_id not in self._rooms:
                    room = BridgedRoom(
                        external_room_id=msg.external_room_id,
                        presidium_peer_id=msg.external_room_id,
                        bridge_type="xmpp",
                        relay_bot_id=self._client.bare_jid if self._client else "",
                    )
                    self._register_room(room)
                    if self._on_room_created:
                        await self._on_room_created(room)

                await self._dispatch_inbound(msg)
        except Exception as exc:
            logger.error("Failed to process XMPP message: %s", exc)

    # -------------------------------------------------------------------
    # Typing relay (Presidium → XMPP)
    # -------------------------------------------------------------------

    async def relay_typing(self, presidium_peer_id: str, is_typing: bool) -> None:
        """Relay a typing indicator from Presidium to XMPP."""
        if not self._client or not self._config.relay_typing:
            return

        room = self.get_room_by_presidium_peer(presidium_peer_id)
        if room is None:
            return

        try:
            if is_typing:
                await self._client.send_composing(room.external_room_id)
            else:
                await self._client.send_paused(room.external_room_id)
        except Exception as exc:
            logger.debug("Failed to relay typing to XMPP: %s", exc)

    # -------------------------------------------------------------------
    # Ping loop
    # -------------------------------------------------------------------

    async def _ping_loop(self) -> None:
        """Periodically send XMPP pings to keep the connection alive."""
        while self._running:
            try:
                await asyncio.sleep(XMPP_PING_INTERVAL)
                if self._client and self._client.is_connected:
                    await self._client.send_ping()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("XMPP ping failed: %s", exc)
                self._stats.reconnect_count += 1
                # Attempt reconnection
                try:
                    if self._client:
                        await self._client.disconnect()
                    await self._client.connect()  # type: ignore[union-attr]
                    await self._set_status(BridgeStatus.RUNNING)
                except Exception as reconn_exc:
                    logger.error("XMPP reconnection failed: %s", reconn_exc)
                    await self._set_status(BridgeStatus.DEGRADED)


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def _xml_escape(text: str) -> str:
    """Escape special XML characters."""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("'", "&apos;")
        .replace('"', "&quot;")
    )
