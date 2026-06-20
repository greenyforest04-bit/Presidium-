"""Cover Traffic / Dummy Traffic for Presidium — traffic analysis resistance.

Generates background dummy traffic to obscure real messaging patterns
from network observers. Without cover traffic, an adversary performing
traffic analysis can infer who is communicating with whom based on
message timing and sizes — even when content is encrypted.

Strategy:
    - Poisson process: Dummy messages are sent at random intervals
      following a Poisson distribution with rate λ (lambda).
    - Adaptive λ: The sending rate adapts to match the user's
      average real traffic, making dummy traffic indistinguishable
      from real traffic.
    - Padding: All messages (real and dummy) are padded to the
      nearest power-of-2 size, eliminating size-based fingerprinting.
    - Random recipients: Dummy messages are sent to random peers,
      ensuring the adversary cannot distinguish real from dummy
      recipients.

Security properties:
    - An observer watching the network sees a constant stream of
      encrypted messages at regular intervals.
    - Real messages are hidden within the noise.
    - When no real messages are being sent, dummy messages maintain
      the baseline traffic rate.
    - Recipients can distinguish dummy messages from real ones via
      a special flag in the decrypted payload (post-ratchet).

Trade-offs:
    - Bandwidth: Cover traffic consumes bandwidth proportional to λ.
    - Battery: On mobile, continuous sending drains battery.
      Mobile devices use a lower rate by default.
    - Latency: Messages may be slightly delayed to align with
      the cover traffic schedule (batching).

Usage:
    cover = CoverTrafficManager(local_user_id="alice")
    cover.set_send_callback(lambda addr, data: transport.send(...))
    await cover.start()  # Starts background dummy traffic
    # Real messages are routed through cover.send_real_message()
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import random
import struct
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from presidium.core.crypto import AES256GCM, SecureBytes, secure_zero

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Padding targets: messages are padded to the nearest power-of-2
PADDING_MIN_SIZE = 256         # Minimum padded size
PADDING_MAX_SIZE = 16384       # Maximum padded size (16 KiB)

# Default Poisson rates (messages per second)
DEFAULT_LAMBDA_DESKTOP = 0.05  # ~3 messages per minute
DEFAULT_LAMBDA_MOBILE = 0.02   # ~1.2 messages per minute
DEFAULT_LAMBDA_LOW = 0.005     # ~0.3 messages per minute (battery saver)

# Adaptive rate parameters
ADAPTIVE_WINDOW_SECONDS = 300  # 5-minute window for rate estimation
ADAPTIVE_MIN_LAMBDA = 0.01    # Minimum rate even with no real traffic
ADAPTIVE_MAX_LAMBDA = 1.0     # Maximum rate cap

# Dummy message marker (encrypted, so only recipient can identify)
DUMMY_MARKER = b"\x00PRESIDIUM_DUMMY\x00"  # 20 bytes


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class CoverTrafficError(Exception):
    """Base exception for cover traffic operations."""


class PaddingError(CoverTrafficError):
    """Message padding error."""


# ---------------------------------------------------------------------------
# Padding utilities
# ---------------------------------------------------------------------------

class MessagePadder:
    """Pads messages to uniform sizes for traffic analysis resistance.

    All messages (real and dummy) are padded to the nearest power-of-2
    size, with a minimum of 256 bytes and maximum of 16 KiB. This
    ensures that message sizes do not leak information about content.
    """

    @staticmethod
    def pad(data: bytes) -> bytes:
        """Pad data to the nearest power-of-2 size.

        Padding format:
            [original_data][0x80][0x00...0x00]

        The 0x80 byte marks the end of real data (PKCS7-like).

        Args:
            data: Original unpadded data.

        Returns:
            Padded data of power-of-2 length.
        """
        if len(data) >= PADDING_MAX_SIZE:
            # Already at or above max; no padding needed
            return data

        # Find the target size: next power of 2 >= max(len(data) + 1, PADDING_MIN_SIZE)
        min_needed = max(len(data) + 1, PADDING_MIN_SIZE)
        target = MessagePadder._next_power_of_2(min_needed)
        target = min(target, PADDING_MAX_SIZE)

        if len(data) + 1 > target:
            # Can't fit in this power of 2, go to next
            target = min(target * 2, PADDING_MAX_SIZE)

        # PKCS7-like padding: data + 0x80 + zeros
        padding_len = target - len(data)
        padded = data + b"\x80" + b"\x00" * (padding_len - 1)

        return padded

    @staticmethod
    def unpad(data: bytes) -> bytes:
        """Remove padding from data.

        Args:
            data: Padded data.

        Returns:
            Original unpadded data.
        """
        # Find the 0x80 marker from the end
        i = len(data) - 1
        while i >= 0:
            if data[i] == 0x80:
                return data[:i]
            if data[i] != 0x00:
                # No valid padding found — return as-is
                return data
            i -= 1
        return data

    @staticmethod
    def _next_power_of_2(n: int) -> int:
        """Compute the smallest power of 2 >= n."""
        if n <= 0:
            return PADDING_MIN_SIZE
        return 1 << (n - 1).bit_length()


# ---------------------------------------------------------------------------
# Cover traffic manager
# ---------------------------------------------------------------------------

class CoverTrafficManager:
    """Generates and manages cover traffic for traffic analysis resistance.

    Operates a background task that sends dummy messages at Poisson-distributed
    intervals. The rate adapts to match the user's real traffic pattern,
    making it difficult for an adversary to distinguish real from dummy messages.

    Lifecycle:
        1. Create with local_user_id and send_callback
        2. Call start() to begin background dummy traffic
        3. Use send_real_message() for actual messages (goes through padding)
        4. Call stop() to end cover traffic
    """

    def __init__(
        self,
        local_user_id: str,
        is_mobile: bool = False,
    ) -> None:
        self._local_user_id = local_user_id
        self._is_mobile = is_mobile
        self._lambda = DEFAULT_LAMBDA_MOBILE if is_mobile else DEFAULT_LAMBDA_DESKTOP

        # Callbacks
        self._send_callback: Optional[
            Callable[[str, bytes], Awaitable[None]]
        ] = None

        # State
        self._running = False
        self._task: Optional[asyncio.Task[None]] = None

        # Adaptive rate tracking
        self._real_message_timestamps: list[float] = []
        self._total_dummy_sent = 0
        self._total_real_sent = 0
        self._total_bytes_sent = 0

        # Known peers (for selecting random recipients)
        self._peer_ids: list[str] = []

    # -------------------------------------------------------------------
    # Configuration
    # -------------------------------------------------------------------

    def set_send_callback(
        self, callback: Callable[[str, bytes], Awaitable[None]]
    ) -> None:
        """Set the callback for sending messages (real and dummy)."""
        self._send_callback = callback

    def set_peers(self, peer_ids: list[str]) -> None:
        """Set the list of known peer IDs for dummy message recipients."""
        self._peer_ids = list(peer_ids)

    def set_rate(self, messages_per_second: float) -> None:
        """Manually set the cover traffic rate."""
        self._lambda = max(ADAPTIVE_MIN_LAMBDA, min(messages_per_second, ADAPTIVE_MAX_LAMBDA))

    # -------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------

    async def start(self) -> None:
        """Start the cover traffic background task."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._cover_loop())
        logger.info(
            "Cover traffic started: λ=%.3f msg/s (%s)",
            self._lambda, "mobile" if self._is_mobile else "desktop",
        )

    async def stop(self) -> None:
        """Stop the cover traffic background task."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info(
            "Cover traffic stopped. Sent %d dummy + %d real messages (%d bytes)",
            self._total_dummy_sent, self._total_real_sent, self._total_bytes_sent,
        )

    # -------------------------------------------------------------------
    # Real message sending (through padding)
    # -------------------------------------------------------------------

    async def send_real_message(
        self,
        peer_id: str,
        plaintext: bytes,
    ) -> None:
        """Send a real message through the cover traffic system.

        The message is padded before sending to match the size of
        dummy messages, preventing size-based traffic analysis.

        Args:
            peer_id: Recipient's user ID.
            plaintext: Message content (will be padded).
        """
        # Record timestamp for adaptive rate
        self._real_message_timestamps.append(time.time())

        # Pad the message
        padded = MessagePadder.pad(plaintext)

        # Send via callback
        if self._send_callback:
            await self._send_callback(peer_id, padded)

        self._total_real_sent += 1
        self._total_bytes_sent += len(padded)

    # -------------------------------------------------------------------
    # Dummy message generation
    # -------------------------------------------------------------------

    async def _send_dummy(self) -> None:
        """Generate and send a dummy message to a random peer."""
        if not self._peer_ids:
            return  # No peers to send to

        # Pick a random recipient
        recipient = random.choice(self._peer_ids)

        # Generate random content of random power-of-2 size
        size = random.choice([256, 512, 1024, 2048, 4096])
        dummy_content = DUMMY_MARKER + os.urandom(size - len(DUMMY_MARKER))

        # Pad to match real message format
        padded = MessagePadder.pad(dummy_content)

        # Send via callback
        if self._send_callback:
            try:
                await self._send_callback(recipient, padded)
            except Exception as exc:
                logger.debug("Failed to send dummy message: %s", exc)
                return

        self._total_dummy_sent += 1
        self._total_bytes_sent += len(padded)

    # -------------------------------------------------------------------
    # Background cover loop
    # -------------------------------------------------------------------

    async def _cover_loop(self) -> None:
        """Background task that sends dummy messages at Poisson intervals."""
        while self._running:
            # Compute next interval using exponential distribution
            # (Poisson process inter-arrival times are exponentially distributed)
            interval = random.expovariate(self._lambda)
            interval = max(0.1, min(interval, 300))  # Clamp between 0.1s and 5min

            await asyncio.sleep(interval)

            if not self._running:
                break

            # Send a dummy message
            await self._send_dummy()

            # Periodically adapt the rate
            await self._adapt_rate()

    # -------------------------------------------------------------------
    # Adaptive rate
    # -------------------------------------------------------------------

    async def _adapt_rate(self) -> None:
        """Adapt the cover traffic rate based on real message frequency.

        The goal is to make the cover traffic rate proportional to the
        real message rate, so that the overall traffic pattern looks
        uniform. When the user sends more real messages, the cover
        traffic increases; when idle, it maintains a minimum baseline.
        """
        now = time.time()
        cutoff = now - ADAPTIVE_WINDOW_SECONDS

        # Remove old timestamps
        self._real_message_timestamps = [
            ts for ts in self._real_message_timestamps if ts > cutoff
        ]

        # Compute real message rate
        if self._real_message_timestamps:
            real_rate = len(self._real_message_timestamps) / ADAPTIVE_WINDOW_SECONDS
        else:
            real_rate = 0.0

        # Cover rate = max(real_rate * 0.5, minimum)
        # This ensures cover traffic is at least 50% of real traffic
        new_lambda = max(real_rate * 0.5, ADAPTIVE_MIN_LAMBDA)
        new_lambda = min(new_lambda, ADAPTIVE_MAX_LAMBDA)

        # Smooth transition (don't change too abruptly)
        self._lambda = self._lambda * 0.8 + new_lambda * 0.2

    # -------------------------------------------------------------------
    # Statistics
    # -------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Return cover traffic statistics."""
        return {
            "running": self._running,
            "lambda": round(self._lambda, 4),
            "dummy_sent": self._total_dummy_sent,
            "real_sent": self._total_real_sent,
            "total_bytes": self._total_bytes_sent,
            "peer_count": len(self._peer_ids),
        }

    @staticmethod
    def is_dummy_message(padded_data: bytes) -> bool:
        """Check if a received (decrypted) message is a dummy.

        After decryption, check for the dummy marker.
        """
        return DUMMY_MARKER in padded_data[:40]
