"""Voice call signaling for Presidium using WebRTC (aiortc).

Provides:
- WebRTC voice call offer/answer exchange
- ICE candidate signaling via P2P transport
- Call state management (ringing, connected, ended)
- Hangup handling

Note: The actual WebRTC media handling is done by aiortc.
This module handles the signaling layer that runs over the
Presidium P2P transport.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from presidium.core.network.peer import PeerManager
from presidium.core.network.protocol import (
    MessageType,
    VoiceOfferPayload,
    VoiceAnswerPayload,
    VoiceIceCandidatePayload,
    VoiceHangupPayload,
)
from presidium.core.network.transport import UDPTransport

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class VoiceError(Exception):
    """Base voice call error."""


class CallNotFoundError(VoiceError):
    """Call session not found."""


class CallRejectedError(VoiceError):
    """Call was rejected by the peer."""


# ---------------------------------------------------------------------------
# Call states
# ---------------------------------------------------------------------------

class CallState:
    RINGING = "ringing"
    CONNECTING = "connecting"
    ACTIVE = "active"
    ENDED = "ended"
    ERROR = "error"


@dataclass
class VoiceCall:
    """Represents a voice call session."""
    session_id: bytes  # 16 bytes
    peer_user_id: str
    state: str = CallState.RINGING
    is_initiator: bool = False
    started_at: float = field(default_factory=time.monotonic)
    ended_at: Optional[float] = None
    duration: float = 0.0

    # aiortc objects (set during active call)
    _pc: Optional[object] = None  # RTCPeerConnection

    @property
    def is_active(self) -> bool:
        return self.state == CallState.ACTIVE

    def end(self, reason: str = "") -> None:
        """End the call."""
        self.state = CallState.ENDED
        self.ended_at = time.monotonic()
        self.duration = self.ended_at - self.started_at
        logger.info("Call %s ended. Duration: %.1fs. Reason: %s",
                     self.session_id.hex()[:8], self.duration, reason or "normal")


# ---------------------------------------------------------------------------
# Voice Manager
# ---------------------------------------------------------------------------

class VoiceManager:
    """Manages WebRTC voice call signaling over the P2P transport.

    The signaling flow:
    1. Initiator sends VOICE_OFFER (SDP offer)
    2. Responder sends VOICE_ANSWER (SDP answer)
    3. Both exchange VOICE_ICE_CANDIDATE messages
    4. WebRTC connection is established
    5. Either side can send VOICE_HANGUP to end

    The actual WebRTC peer connection and media tracks are managed
    by aiortc. This module handles the signaling transport.
    """

    def __init__(
        self,
        peer_manager: PeerManager,
        transport: UDPTransport,
    ) -> None:
        self._peer_manager = peer_manager
        self._transport = transport

        # Active calls: session_id → VoiceCall
        self._calls: dict[bytes, VoiceCall] = {}

        # Callbacks
        self._on_incoming_call: Optional[Callable[[str, bytes], Awaitable[bool]]] = None
        self._on_call_state_changed: Optional[Callable[[bytes, str], Awaitable[None]]] = None

        # Register handlers
        self._transport.register_handler(MessageType.VOICE_OFFER, self._handle_voice_offer)
        self._transport.register_handler(MessageType.VOICE_ANSWER, self._handle_voice_answer)
        self._transport.register_handler(MessageType.VOICE_ICE_CANDIDATE, self._handle_ice_candidate)
        self._transport.register_handler(MessageType.VOICE_HANGUP, self._handle_hangup)

    # -------------------------------------------------------------------
    # Callback setters
    # -------------------------------------------------------------------

    def set_on_incoming_call(
        self, callback: Callable[[str, bytes], Awaitable[bool]]
    ) -> None:
        """Set callback for incoming calls: (peer_uid, session_id) → accept?"""
        self._on_incoming_call = callback

    def set_on_call_state_changed(
        self, callback: Callable[[bytes, str], Awaitable[None]]
    ) -> None:
        self._on_call_state_changed = callback

    # -------------------------------------------------------------------
    # Initiate a call
    # -------------------------------------------------------------------

    async def call(self, peer_user_id: str) -> bytes:
        """Initiate a voice call with a peer.

        Args:
            peer_user_id: The peer to call.

        Returns:
            The session_id for the call.

        Raises:
            VoiceError: If the call cannot be initiated.
        """
        peer = self._peer_manager.get_peer(peer_user_id)
        if peer is None or peer.addr is None:
            raise VoiceError(f"Peer {peer_user_id} not found or not connected.")

        session_id = os.urandom(16)

        # SIMPLIFIED: In production, create aiortc RTCPeerConnection,
        # add audio tracks, create offer SDP, and send it.
        # For now, send a minimal offer.
        sdp_offer = "v=0\r\no=- 0 0 IN IP4 0.0.0.0\r\ns=-\r\nt=0 0\r\n"

        call = VoiceCall(
            session_id=session_id,
            peer_user_id=peer_user_id,
            state=CallState.RINGING,
            is_initiator=True,
        )
        self._calls[session_id] = call

        offer = VoiceOfferPayload(
            session_id=session_id,
            sdp=sdp_offer,
            media_types=["audio"],
        )
        await self._transport.send_reliable(MessageType.VOICE_OFFER, offer, peer.addr)

        logger.info("Voice call initiated to %s (session %s)", peer_user_id[:8], session_id.hex()[:8])
        return session_id

    # -------------------------------------------------------------------
    # Hangup
    # -------------------------------------------------------------------

    async def hangup(self, session_id: bytes, reason: str = "") -> None:
        """Hang up an active call."""
        call = self._calls.get(session_id)
        if call is None:
            raise CallNotFoundError(f"No call with session {session_id.hex()}")

        peer = self._peer_manager.get_peer(call.peer_user_id)
        if peer and peer.addr:
            hangup = VoiceHangupPayload(
                session_id=session_id,
                reason=reason,
            )
            await self._transport.send(MessageType.VOICE_HANGUP, hangup, peer.addr)

        call.end(reason)

        # Close WebRTC peer connection if active
        if call._pc is not None:
            try:
                await call._pc.close()  # type: ignore
            except Exception:
                pass

        if self._on_call_state_changed:
            await self._on_call_state_changed(session_id, CallState.ENDED)

    # -------------------------------------------------------------------
    # Incoming handlers
    # -------------------------------------------------------------------

    async def _handle_voice_offer(
        self, msg_type: int, timestamp: float, payload: VoiceOfferPayload, addr: tuple[str, int]
    ) -> None:
        """Handle incoming voice call offer."""
        # Find peer
        peer_uid = ""
        for peer in self._peer_manager.get_all_peers():
            if peer.addr == addr:
                peer_uid = peer.user_id
                break

        if not peer_uid:
            logger.warning("Voice offer from unknown peer at %s", addr)
            return

        logger.info("Incoming voice call from %s", peer_uid[:8])

        # Ask user whether to accept
        accept = False
        if self._on_incoming_call:
            try:
                accept = await self._on_incoming_call(peer_uid, payload.session_id)
            except Exception:
                accept = False

        if not accept:
            # Reject by sending hangup
            hangup = VoiceHangupPayload(
                session_id=payload.session_id,
                reason="rejected",
            )
            await self._transport.send(MessageType.VOICE_HANGUP, hangup, addr)
            return

        # Create call session
        call = VoiceCall(
            session_id=payload.session_id,
            peer_user_id=peer_uid,
            state=CallState.CONNECTING,
            is_initiator=False,
        )
        self._calls[payload.session_id] = call

        # SIMPLIFIED: In production, create RTCPeerConnection, set remote description,
        # create answer SDP, and send it back.
        sdp_answer = "v=0\r\no=- 0 0 IN IP4 0.0.0.0\r\ns=-\r\nt=0 0\r\n"

        answer = VoiceAnswerPayload(
            session_id=payload.session_id,
            sdp=sdp_answer,
        )
        await self._transport.send(MessageType.VOICE_ANSWER, answer, addr)

        call.state = CallState.ACTIVE
        if self._on_call_state_changed:
            await self._on_call_state_changed(payload.session_id, CallState.ACTIVE)

    async def _handle_voice_answer(
        self, msg_type: int, timestamp: float, payload: VoiceAnswerPayload, addr: tuple[str, int]
    ) -> None:
        """Handle voice call answer (SDP answer)."""
        call = self._calls.get(payload.session_id)
        if call is None:
            logger.warning("Voice answer for unknown session %s", payload.session_id.hex())
            return

        # SIMPLIFIED: In production, set remote description on RTCPeerConnection.
        call.state = CallState.ACTIVE
        if self._on_call_state_changed:
            await self._on_call_state_changed(payload.session_id, CallState.ACTIVE)

        logger.info("Voice call %s is now active", payload.session_id.hex()[:8])

    async def _handle_ice_candidate(
        self, msg_type: int, timestamp: float, payload: VoiceIceCandidatePayload, addr: tuple[str, int]
    ) -> None:
        """Handle ICE candidate exchange."""
        call = self._calls.get(payload.session_id)
        if call is None:
            return

        # SIMPLIFIED: In production, add ICE candidate to RTCPeerConnection.
        logger.debug("ICE candidate for session %s: %s", payload.session_id.hex()[:8], payload.candidate)

    async def _handle_hangup(
        self, msg_type: int, timestamp: float, payload: VoiceHangupPayload, addr: tuple[str, int]
    ) -> None:
        """Handle incoming hangup."""
        call = self._calls.get(payload.session_id)
        if call is None:
            return

        call.end(payload.reason)

        # Close WebRTC connection
        if call._pc is not None:
            try:
                await call._pc.close()  # type: ignore
            except Exception:
                pass

        if self._on_call_state_changed:
            await self._on_call_state_changed(payload.session_id, CallState.ENDED)

        logger.info("Voice call ended by peer %s. Reason: %s",
                     call.peer_user_id[:8], payload.reason or "normal")

    # -------------------------------------------------------------------
    # Getters
    # -------------------------------------------------------------------

    def get_call(self, session_id: bytes) -> Optional[VoiceCall]:
        """Get a call by session ID."""
        return self._calls.get(session_id)

    def get_active_calls(self) -> list[VoiceCall]:
        """Get all active calls."""
        return [c for c in self._calls.values() if c.is_active]

    def get_call_with_peer(self, peer_user_id: str) -> Optional[VoiceCall]:
        """Get the active call with a specific peer."""
        for call in self._calls.values():
            if call.peer_user_id == peer_user_id and call.is_active:
                return call
        return None
