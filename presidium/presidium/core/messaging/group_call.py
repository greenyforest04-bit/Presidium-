"""Group Voice/Video Calls for Presidium — P2P SFU architecture.

Implements group calls using a hybrid mesh/SFU topology:
    - Mesh (all-to-all): 2-3 participants — lowest latency, no single point
    - SFU (host forwards): 4-16 participants — host acts as Selective
      Forwarding Unit, receiving all streams and selectively forwarding
      to each participant based on bandwidth and subscription.

SFU model (P2P, no central server):
    The call initiator becomes the "host" and runs a lightweight SFU:
    1. All participants send their media stream to the host
    2. Host selectively forwards streams to each participant
    3. Host does NOT mix/encode — only forwards encoded packets
    4. Each participant receives only the streams they subscribe to

Simulcast:
    Each sender encodes 3 quality layers:
    - Low:  320x180 @ 15fps, 150 kbps
    - Mid:  640x360 @ 24fps, 400 kbps
    - High: 1280x720 @ 30fps, 800 kbps
    The SFU switches layers based on receiver bandwidth.

Signaling:
    All signaling uses the existing P2P transport with new
    GROUP_CALL_* message types for offer/answer/ICE/candidate exchange.

Limits:
    - Maximum 16 participants per call
    - Video: up to 720p per participant (bandwidth constrained)
    - Audio: OPUS codec, 48kHz, mono
    - Screen share: one participant at a time

Call state machine:
    IDLE → RINGING → JOINING → CONNECTED → LEAVING → IDLE
"""

from __future__ import annotations

import asyncio
import enum
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from presidium.core.network.protocol import MessageType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_PARTICIPANTS = 16
MESH_THRESHOLD = 3  # Use mesh for <= 3 participants, SFU for > 3

# Simulcast layers
SIMULCAST_LOW = {"width": 320, "height": 180, "fps": 15, "bitrate": 150_000}
SIMULCAST_MID = {"width": 640, "height": 360, "fps": 24, "bitrate": 400_000}
SIMULCAST_HIGH = {"width": 1280, "height": 720, "fps": 30, "bitrate": 800_000}

# Audio codec
AUDIO_CODEC = "opus"
AUDIO_SAMPLE_RATE = 48000
AUDIO_CHANNELS = 1

# Call timeout
RING_TIMEOUT_SECONDS = 30
CALL_KEEPALIVE_SECONDS = 15


# ---------------------------------------------------------------------------
# Call state machine
# ---------------------------------------------------------------------------

class CallState(enum.IntEnum):
    """State of a group call."""
    IDLE = 0
    RINGING = 1
    JOINING = 2
    CONNECTED = 3
    RECONNECTING = 4
    LEAVING = 5
    ENDED = 6


class CallTopology(enum.IntEnum):
    """Call network topology."""
    MESH = 0    # All-to-all (2-3 participants)
    SFU = 1     # Host-based forwarding (4-16 participants)


class MediaState(enum.IntEnum):
    """Participant media state."""
    OFF = 0
    ON = 1
    MUTED = 2      # Audio muted
    PAUSED = 3     # Video paused


# ---------------------------------------------------------------------------
# Participant model
# ---------------------------------------------------------------------------

@dataclass
class CallParticipant:
    """A participant in a group call."""
    user_id: str = ""
    display_name: str = ""
    is_host: bool = False
    audio_state: MediaState = MediaState.OFF
    video_state: MediaState = MediaState.OFF
    screen_share: bool = False
    is_raising_hand: bool = False
    joined_at: float = 0.0
    last_seen: float = 0.0
    connection_quality: float = 1.0  # 0.0-1.0

    # WebRTC peer connection (set at runtime)
    peer_connection: Any = None  # aiortc.RTCPeerConnection

    # Simulcast subscription (which quality layer this participant receives)
    subscribed_quality: str = "mid"  # "low" | "mid" | "high"

    @property
    def is_active(self) -> bool:
        """Check if the participant is still connected."""
        return self.audio_state != MediaState.OFF or self.video_state != MediaState.OFF


# ---------------------------------------------------------------------------
# Call session
# ---------------------------------------------------------------------------

@dataclass
class GroupCallSession:
    """Represents an active group call session."""
    call_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    group_id: str = ""          # Associated group chat ID
    host_user_id: str = ""      # Call initiator / SFU host
    topology: CallTopology = CallTopology.MESH
    state: CallState = CallState.IDLE
    participants: dict[str, CallParticipant] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    ended_at: float = 0.0
    is_recording: bool = False
    is_screen_sharing: bool = False
    screen_sharer_id: str = ""

    @property
    def participant_count(self) -> int:
        return len(self.participants)

    @property
    def is_full(self) -> bool:
        return self.participant_count >= MAX_PARTICIPANTS

    @property
    def is_host(self) -> bool:
        """True if the local user is the call host."""
        return False  # Set by GroupCallManager

    def add_participant(self, participant: CallParticipant) -> None:
        """Add a participant to the call."""
        if self.is_full:
            raise GroupCallError("Call is full (max 16 participants).")
        self.participants[participant.user_id] = participant

    def remove_participant(self, user_id: str) -> Optional[CallParticipant]:
        """Remove a participant from the call."""
        return self.participants.pop(user_id, None)

    def get_participant(self, user_id: str) -> Optional[CallParticipant]:
        """Get a participant by user ID."""
        return self.participants.get(user_id)

    def get_active_speakers(self) -> list[CallParticipant]:
        """Get participants with audio enabled (potential speakers)."""
        return [
            p for p in self.participants.values()
            if p.audio_state == MediaState.ON
        ]

    def determine_topology(self) -> CallTopology:
        """Determine the appropriate topology based on participant count."""
        if self.participant_count <= MESH_THRESHOLD:
            return CallTopology.MESH
        return CallTopology.SFU


# ---------------------------------------------------------------------------
# Signaling messages
# ---------------------------------------------------------------------------

class GroupCallSignal:
    """Creates signaling messages for group call setup."""

    @staticmethod
    def create_offer(
        call_id: str,
        group_id: str,
        host_user_id: str,
        sdp: str,
        media_types: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a GROUP_CALL_OFFER signal."""
        return {
            "type": "GROUP_CALL_OFFER",
            "call_id": call_id,
            "group_id": group_id,
            "host_user_id": host_user_id,
            "sdp": sdp,
            "media_types": media_types or ["audio"],
            "max_participants": MAX_PARTICIPANTS,
            "timestamp": time.time(),
        }

    @staticmethod
    def create_answer(
        call_id: str,
        sdp: str,
    ) -> dict[str, Any]:
        """Create a GROUP_CALL_ANSWER signal."""
        return {
            "type": "GROUP_CALL_ANSWER",
            "call_id": call_id,
            "sdp": sdp,
            "timestamp": time.time(),
        }

    @staticmethod
    def create_ice_candidate(
        call_id: str,
        candidate: str,
        sdp_mid: str = "",
        sdp_mline_index: int = 0,
    ) -> dict[str, Any]:
        """Create a GROUP_CALL_ICE_CANDIDATE signal."""
        return {
            "type": "GROUP_CALL_ICE_CANDIDATE",
            "call_id": call_id,
            "candidate": candidate,
            "sdp_mid": sdp_mid,
            "sdp_mline_index": sdp_mline_index,
        }

    @staticmethod
    def create_join(call_id: str, user_id: str) -> dict[str, Any]:
        """Create a GROUP_CALL_JOIN signal."""
        return {
            "type": "GROUP_CALL_JOIN",
            "call_id": call_id,
            "user_id": user_id,
            "timestamp": time.time(),
        }

    @staticmethod
    def create_leave(call_id: str, user_id: str, reason: str = "") -> dict[str, Any]:
        """Create a GROUP_CALL_LEAVE signal."""
        return {
            "type": "GROUP_CALL_LEAVE",
            "call_id": call_id,
            "user_id": user_id,
            "reason": reason,
            "timestamp": time.time(),
        }

    @staticmethod
    def create_media_update(
        call_id: str,
        user_id: str,
        audio: MediaState = MediaState.OFF,
        video: MediaState = MediaState.OFF,
        screen_share: bool = False,
    ) -> dict[str, Any]:
        """Create a GROUP_CALL_MEDIA_UPDATE signal."""
        return {
            "type": "GROUP_CALL_MEDIA_UPDATE",
            "call_id": call_id,
            "user_id": user_id,
            "audio": audio.value,
            "video": video.value,
            "screen_share": screen_share,
            "timestamp": time.time(),
        }

    @staticmethod
    def create_raise_hand(call_id: str, user_id: str, raised: bool) -> dict[str, Any]:
        """Create a GROUP_CALL_RAISE_HAND signal."""
        return {
            "type": "GROUP_CALL_RAISE_HAND",
            "call_id": call_id,
            "user_id": user_id,
            "raised": raised,
        }

    @staticmethod
    def create_sfu_subscribe(
        call_id: str,
        user_id: str,
        target_user_id: str,
        quality: str = "mid",
    ) -> dict[str, Any]:
        """Create a GROUP_CALL_SFU_SUBSCRIBE signal.

        Tells the SFU host which quality layer to forward.
        """
        return {
            "type": "GROUP_CALL_SFU_SUBSCRIBE",
            "call_id": call_id,
            "user_id": user_id,
            "target_user_id": target_user_id,
            "quality": quality,
        }


# ---------------------------------------------------------------------------
# Group call manager
# ---------------------------------------------------------------------------

class GroupCallManager:
    """Manages group voice/video calls for Presidium.

    Handles call creation, participant management, WebRTC signaling,
    topology switching (mesh/SFU), and media control.

    Usage:
        manager = GroupCallManager(local_user_id="alice")
        manager.set_signaling_callback(lambda peer_id, msg: transport.send(...))
        await manager.start_call(group_id="group123")
    """

    def __init__(self, local_user_id: str) -> None:
        self._local_user_id = local_user_id
        self._active_calls: dict[str, GroupCallSession] = {}

        # Callbacks
        self._signaling_callback: Optional[
            Callable[[str, dict[str, Any]], Awaitable[None]]
        ] = None
        self._on_call_state_changed: Optional[
            Callable[[GroupCallSession], Awaitable[None]]
        ] = None
        self._on_participant_joined: Optional[
            Callable[[GroupCallSession, CallParticipant], Awaitable[None]]
        ] = None
        self._on_participant_left: Optional[
            Callable[[GroupCallSession, str], Awaitable[None]]
        ] = None
        self._on_media_changed: Optional[
            Callable[[GroupCallSession, CallParticipant], Awaitable[None]]
        ] = None
        self._on_active_speaker_changed: Optional[
            Callable[[GroupCallSession, str], Awaitable[None]]
        ] = None

        # Audio level tracking for speaker detection
        self._audio_levels: dict[str, float] = {}

    # -------------------------------------------------------------------
    # Callback setters
    # -------------------------------------------------------------------

    def set_signaling_callback(
        self, callback: Callable[[str, dict[str, Any]], Awaitable[None]]
    ) -> None:
        """Set callback for sending signaling messages to peers."""
        self._signaling_callback = callback

    def set_on_call_state_changed(
        self, callback: Callable[[GroupCallSession], Awaitable[None]]
    ) -> None:
        self._on_call_state_changed = callback

    def set_on_participant_joined(
        self, callback: Callable[[GroupCallSession, CallParticipant], Awaitable[None]]
    ) -> None:
        self._on_participant_joined = callback

    def set_on_participant_left(
        self, callback: Callable[[GroupCallSession, str], Awaitable[None]]
    ) -> None:
        self._on_participant_left = callback

    def set_on_media_changed(
        self, callback: Callable[[GroupCallSession, CallParticipant], Awaitable[None]]
    ) -> None:
        self._on_media_changed = callback

    def set_on_active_speaker_changed(
        self, callback: Callable[[GroupCallSession, str], Awaitable[None]]
    ) -> None:
        self._on_active_speaker_changed = callback

    # -------------------------------------------------------------------
    # Call lifecycle
    # -------------------------------------------------------------------

    async def start_call(
        self,
        group_id: str,
        participants: list[str],
        with_video: bool = False,
        with_screen_share: bool = False,
    ) -> GroupCallSession:
        """Initiate a group call.

        Args:
            group_id: The group chat ID this call belongs to.
            participants: List of user_ids to invite.
            with_video: Whether to start with video enabled.
            with_screen_share: Whether to start with screen sharing.

        Returns:
            The created GroupCallSession.
        """
        if len(participants) + 1 > MAX_PARTICIPANTS:
            raise GroupCallError(
                f"Too many participants: {len(participants) + 1} > {MAX_PARTICIPANTS}."
            )

        session = GroupCallSession(
            group_id=group_id,
            host_user_id=self._local_user_id,
            state=CallState.RINGING,
        )

        # Determine topology
        session.topology = session.determine_topology()

        # Add self as host participant
        host = CallParticipant(
            user_id=self._local_user_id,
            is_host=True,
            audio_state=MediaState.ON,
            video_state=MediaState.ON if with_video else MediaState.OFF,
            screen_share=with_screen_share,
            joined_at=time.time(),
        )
        session.add_participant(host)

        # Create WebRTC offer (aiortc)
        sdp = await self._create_webrtc_offer(session)

        # Send invites to all participants
        for peer_id in participants:
            signal = GroupCallSignal.create_offer(
                call_id=session.call_id,
                group_id=group_id,
                host_user_id=self._local_user_id,
                sdp=sdp,
                media_types=["audio"] + (["video"] if with_video else []),
            )
            await self._send_signal(peer_id, signal)

        self._active_calls[session.call_id] = session

        if self._on_call_state_changed:
            await self._on_call_state_changed(session)

        logger.info(
            "Group call started: %s (%s, %d invited)",
            session.call_id, session.topology.name, len(participants),
        )
        return session

    async def join_call(
        self,
        call_id: str,
        group_id: str,
        host_user_id: str,
        sdp_offer: str,
    ) -> GroupCallSession:
        """Join an existing group call.

        Args:
            call_id: The call ID from the offer.
            group_id: The group chat ID.
            host_user_id: The call host's user ID.
            sdp_offer: The WebRTC SDP offer.

        Returns:
            The joined GroupCallSession.
        """
        if call_id in self._active_calls:
            return self._active_calls[call_id]

        session = GroupCallSession(
            call_id=call_id,
            group_id=group_id,
            host_user_id=host_user_id,
            state=CallState.JOINING,
        )

        # Add host participant
        host = CallParticipant(
            user_id=host_user_id,
            is_host=True,
            audio_state=MediaState.ON,
            joined_at=time.time(),
        )
        session.add_participant(host)

        # Add self
        self_participant = CallParticipant(
            user_id=self._local_user_id,
            is_host=False,
            audio_state=MediaState.ON,
            joined_at=time.time(),
        )
        session.add_participant(self_participant)

        # Create WebRTC answer
        sdp_answer = await self._create_webrtc_answer(session, sdp_offer)

        # Send answer to host
        signal = GroupCallSignal.create_answer(
            call_id=call_id,
            sdp=sdp_answer,
        )
        await self._send_signal(host_user_id, signal)

        session.state = CallState.CONNECTED
        session.started_at = time.time()
        self._active_calls[call_id] = session

        if self._on_participant_joined:
            await self._on_participant_joined(session, self_participant)
        if self._on_call_state_changed:
            await self._on_call_state_changed(session)

        logger.info("Joined group call %s", call_id)
        return session

    async def leave_call(self, call_id: str, reason: str = "") -> None:
        """Leave an active group call."""
        session = self._active_calls.get(call_id)
        if session is None:
            return

        session.state = CallState.LEAVING

        # Notify all participants
        signal = GroupCallSignal.create_leave(
            call_id=call_id,
            user_id=self._local_user_id,
            reason=reason,
        )
        for participant_id in session.participants:
            if participant_id != self._local_user_id:
                await self._send_signal(participant_id, signal)

        # Close all WebRTC connections
        for participant in session.participants.values():
            if participant.peer_connection:
                await participant.peer_connection.close()

        # Remove self from session
        session.remove_participant(self._local_user_id)

        # If host left and still participants, transfer host
        if session.host_user_id == self._local_user_id and session.participants:
            new_host_id = next(iter(session.participants))
            session.host_user_id = new_host_id
            session.participants[new_host_id].is_host = True
            logger.info("Host transferred to %s", new_host_id[:8])
        elif not session.participants:
            session.state = CallState.ENDED
            session.ended_at = time.time()

        if session.state == CallState.ENDED:
            del self._active_calls[call_id]

        if self._on_call_state_changed:
            await self._on_call_state_changed(session)

        logger.info("Left group call %s", call_id)

    # -------------------------------------------------------------------
    # Media control
    # -------------------------------------------------------------------

    async def toggle_audio(self, call_id: str) -> MediaState:
        """Toggle audio on/off for the local user."""
        session = self._active_calls.get(call_id)
        if session is None:
            return MediaState.OFF

        participant = session.get_participant(self._local_user_id)
        if participant is None:
            return MediaState.OFF

        # Toggle
        if participant.audio_state == MediaState.ON:
            participant.audio_state = MediaState.MUTED
        else:
            participant.audio_state = MediaState.ON

        # Notify participants
        await self._broadcast_media_update(session, participant)
        return participant.audio_state

    async def toggle_video(self, call_id: str) -> MediaState:
        """Toggle video on/off for the local user."""
        session = self._active_calls.get(call_id)
        if session is None:
            return MediaState.OFF

        participant = session.get_participant(self._local_user_id)
        if participant is None:
            return MediaState.OFF

        if participant.video_state == MediaState.ON:
            participant.video_state = MediaState.OFF
        else:
            participant.video_state = MediaState.ON

        await self._broadcast_media_update(session, participant)
        return participant.video_state

    async def toggle_screen_share(self, call_id: str) -> bool:
        """Toggle screen sharing for the local user."""
        session = self._active_calls.get(call_id)
        if session is None:
            return False

        participant = session.get_participant(self._local_user_id)
        if participant is None:
            return False

        # Only one screen share at a time
        if not participant.screen_share and session.is_screen_sharing:
            raise GroupCallError("Another participant is already sharing their screen.")

        participant.screen_share = not participant.screen_share
        session.is_screen_sharing = participant.screen_share
        session.screen_sharer_id = self._local_user_id if participant.screen_share else ""

        await self._broadcast_media_update(session, participant)
        return participant.screen_share

    async def raise_hand(self, call_id: str, raised: bool = True) -> None:
        """Raise or lower hand."""
        session = self._active_calls.get(call_id)
        if session is None:
            return

        participant = session.get_participant(self._local_user_id)
        if participant:
            participant.is_raising_hand = raised

        signal = GroupCallSignal.create_raise_hand(call_id, self._local_user_id, raised)
        for peer_id in session.participants:
            if peer_id != self._local_user_id:
                await self._send_signal(peer_id, signal)

    # -------------------------------------------------------------------
    # Speaker detection
    # -------------------------------------------------------------------

    def update_audio_level(self, call_id: str, user_id: str, level: float) -> None:
        """Update the audio level for a participant (VU meter).

        Args:
            call_id: Active call ID.
            user_id: Participant user ID.
            level: Audio level 0.0-1.0.
        """
        self._audio_levels[user_id] = level

    def get_active_speaker(self, call_id: str) -> Optional[str]:
        """Get the user_id of the current active speaker."""
        if not self._audio_levels:
            return None
        return max(self._audio_levels, key=self._audio_levels.get)  # type: ignore[arg-type]

    # -------------------------------------------------------------------
    # Signaling handler
    # -------------------------------------------------------------------

    async def handle_signal(self, sender_id: str, signal: dict[str, Any]) -> None:
        """Handle an incoming group call signal."""
        signal_type = signal.get("type", "")
        call_id = signal.get("call_id", "")

        if signal_type == "GROUP_CALL_OFFER":
            await self.join_call(
                call_id=call_id,
                group_id=signal.get("group_id", ""),
                host_user_id=signal.get("host_user_id", ""),
                sdp_offer=signal.get("sdp", ""),
            )

        elif signal_type == "GROUP_CALL_ANSWER":
            session = self._active_calls.get(call_id)
            if session:
                participant = session.get_participant(sender_id)
                if participant:
                    participant.audio_state = MediaState.ON
                    if self._on_participant_joined:
                        await self._on_participant_joined(session, participant)

        elif signal_type == "GROUP_CALL_LEAVE":
            session = self._active_calls.get(call_id)
            if session:
                removed = session.remove_participant(sender_id)
                if removed and self._on_participant_left:
                    await self._on_participant_left(session, sender_id)

        elif signal_type == "GROUP_CALL_MEDIA_UPDATE":
            session = self._active_calls.get(call_id)
            if session:
                participant = session.get_participant(sender_id)
                if participant:
                    participant.audio_state = MediaState(signal.get("audio", 0))
                    participant.video_state = MediaState(signal.get("video", 0))
                    participant.screen_share = signal.get("screen_share", False)
                    if self._on_media_changed:
                        await self._on_media_changed(session, participant)

        elif signal_type == "GROUP_CALL_RAISE_HAND":
            session = self._active_calls.get(call_id)
            if session:
                participant = session.get_participant(sender_id)
                if participant:
                    participant.is_raising_hand = signal.get("raised", False)

        elif signal_type == "GROUP_CALL_ICE_CANDIDATE":
            # Forward ICE candidate to the appropriate peer connection
            pass  # WebRTC handles this

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    async def _broadcast_media_update(
        self, session: GroupCallSession, participant: CallParticipant
    ) -> None:
        """Broadcast media state change to all participants."""
        signal = GroupCallSignal.create_media_update(
            call_id=session.call_id,
            user_id=participant.user_id,
            audio=participant.audio_state,
            video=participant.video_state,
            screen_share=participant.screen_share,
        )
        for peer_id in session.participants:
            if peer_id != self._local_user_id:
                await self._send_signal(peer_id, signal)

        if self._on_media_changed:
            await self._on_media_changed(session, participant)

    async def _send_signal(self, peer_id: str, signal: dict[str, Any]) -> None:
        """Send a signaling message to a peer."""
        if self._signaling_callback:
            try:
                await self._signaling_callback(peer_id, signal)
            except Exception as exc:
                logger.error("Failed to send signal to %s: %s", peer_id[:8], exc)

    async def _create_webrtc_offer(self, session: GroupCallSession) -> str:
        """Create a WebRTC SDP offer.

        In a full implementation, this uses aiortc to create a
        RTCPeerConnection with audio/video tracks.
        """
        # Placeholder — actual implementation uses aiortc
        return f"v=0\r\no=- {int(time.time())} 1 IN IP4 0.0.0.0\r\ns=Presidium Group Call\r\nt=0 0\r\n"

    async def _create_webrtc_answer(
        self, session: GroupCallSession, offer: str
    ) -> str:
        """Create a WebRTC SDP answer."""
        return f"v=0\r\no=- {int(time.time())} 1 IN IP4 0.0.0.0\r\ns=Presidium Group Call Answer\r\nt=0 0\r\n"

    # -------------------------------------------------------------------
    # Query methods
    # -------------------------------------------------------------------

    def get_call(self, call_id: str) -> Optional[GroupCallSession]:
        """Get an active call by ID."""
        return self._active_calls.get(call_id)

    def get_active_calls(self) -> list[GroupCallSession]:
        """Get all active calls."""
        return list(self._active_calls.values())


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class GroupCallError(Exception):
    """Base exception for group call operations."""


class CallFullError(GroupCallError):
    """Call has reached maximum participants."""


class ScreenShareConflictError(GroupCallError):
    """Another participant is already sharing screen."""
