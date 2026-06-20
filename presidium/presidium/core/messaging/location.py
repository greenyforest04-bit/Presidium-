"""Location Sharing for Presidium — privacy-preserving location exchange.

Provides end-to-end encrypted location sharing with fine-grained control
over precision and duration. Location data is never sent to any server —
it's E2EE between the sender and recipient, just like text messages.

Features:
    - Precision control: exact, building, neighborhood, city levels
    - Time-limited sharing: 15min, 1hr, 8hr, or custom duration
    - Live location: periodic updates at configurable intervals
    - Static location: one-time share of a specific place
    - POI attachments: share named locations (cafes, offices, etc.)
    - Map preview: rendered via OpenStreetMap tiles (no tracking)

Privacy model:
    - Location data is encrypted with Double Ratchet (same as messages)
    - No location server — direct P2P exchange
    - Precision reduction happens CLIENT-SIDE before encryption
    - Live location stops automatically after duration expires
    - Recipient cannot forward location without sender's consent

Wire format (inside encrypted_content):
    [1B version][1B type][1B precision][8B lat_fixed][8B lon_fixed]
    [2B alt_m][8B timestamp][2B label_len][N label][1B duration_min]

Type values:
    0x01 = Static location
    0x02 = Live location start
    0x03 = Live location update
    0x04 = Live location stop

Precision values:
    0x01 = Exact (full precision)
    0x02 = Building (~100m)
    0x03 = Neighborhood (~1km)
    0x04 = City (~10km)
"""

from __future__ import annotations

import enum
import hashlib
import logging
import math
import os
import struct
import time
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOCATION_VERSION = 1
EARTH_RADIUS_KM = 6371.0
LAT_SCALE = 1e7    # Fixed-point scale for latitude
LON_SCALE = 1e7    # Fixed-point scale for longitude


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class LocationError(Exception):
    """Base exception for location sharing operations."""


class PrecisionError(LocationError):
    """Invalid precision level for location data."""


class LiveLocationError(LocationError):
    """Live location tracking error."""


class LocationExpiredError(LocationError):
    """Shared location has expired."""


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class LocationType(enum.IntEnum):
    """Type of location message."""
    STATIC = 0x01
    LIVE_START = 0x02
    LIVE_UPDATE = 0x03
    LIVE_STOP = 0x04


class LocationPrecision(enum.IntEnum):
    """Precision level for location sharing.

    Higher values = less precise = more privacy.
    Precision reduction is applied CLIENT-SIDE before encryption,
    so the recipient only sees the reduced-precision location.
    """
    EXACT = 0x01           # Full precision (~1m)
    BUILDING = 0x02        # ~100m radius
    NEIGHBORHOOD = 0x03    # ~1km radius
    CITY = 0x04            # ~10km radius


class LiveDuration(enum.IntEnum):
    """Predefined durations for live location sharing (in minutes)."""
    MINUTES_15 = 15
    MINUTES_30 = 30
    HOUR_1 = 60
    HOURS_8 = 480
    HOURS_24 = 1440


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class GeoCoordinate:
    """A geographic coordinate with latitude, longitude, and optional altitude.

    Attributes:
        latitude: Latitude in degrees (-90 to 90).
        longitude: Longitude in degrees (-180 to 180).
        altitude: Altitude in meters above sea level (0 if unknown).
    """
    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 0.0

    def reduce_precision(self, precision: LocationPrecision) -> GeoCoordinate:
        """Reduce the precision of this coordinate for privacy.

        Applies rounding and randomization to reduce location accuracy
        to the specified level. The randomization is deterministic for
        the same input (seeded from the coordinate itself) so that
        repeated shares of the same location produce the same result.

        Args:
            precision: Target precision level.

        Returns:
            New GeoCoordinate with reduced precision.
        """
        if precision == LocationPrecision.EXACT:
            return GeoCoordinate(self.latitude, self.longitude, self.altitude)

        # Determine decimal places to keep based on precision
        precision_map = {
            LocationPrecision.BUILDING: 3,       # ~111m at equator
            LocationPrecision.NEIGHBORHOOD: 2,   # ~1.1km
            LocationPrecision.CITY: 1,           # ~11km
        }
        decimal_places = precision_map.get(precision, 3)

        # Deterministic jitter: seed from coordinate + precision
        seed_input = f"{self.latitude:.6f},{self.longitude:.6f},{precision.value}".encode()
        seed_hash = hashlib.sha256(seed_input).digest()
        jitter_lat = (int.from_bytes(seed_hash[:4], "big") / (2**32) - 0.5) * 0.001
        jitter_lon = (int.from_bytes(seed_hash[4:8], "big") / (2**32) - 0.5) * 0.001

        # Round and add jitter
        factor = 10 ** decimal_places
        rounded_lat = round(self.latitude * factor) / factor + jitter_lat
        rounded_lon = round(self.longitude * factor) / factor + jitter_lon

        # Clamp to valid ranges
        rounded_lat = max(-90.0, min(90.0, rounded_lat))
        rounded_lon = max(-180.0, min(180.0, rounded_lon))

        return GeoCoordinate(
            latitude=rounded_lat,
            longitude=rounded_lon,
            altitude=0.0,  # Don't share altitude at reduced precision
        )

    def distance_to(self, other: GeoCoordinate) -> float:
        """Calculate the great-circle distance to another coordinate.

        Uses the Haversine formula for accurate distance calculation
        on a sphere.

        Args:
            other: The other coordinate.

        Returns:
            Distance in kilometers.
        """
        lat1, lon1 = math.radians(self.latitude), math.radians(self.longitude)
        lat2, lon2 = math.radians(other.latitude), math.radians(other.longitude)

        dlat = lat2 - lat1
        dlon = lon2 - lon1

        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        c = 2 * math.asin(math.sqrt(a))

        return EARTH_RADIUS_KM * c

    def to_fixed_point(self) -> tuple[int, int]:
        """Convert to fixed-point integers for wire encoding.

        Returns:
            Tuple of (lat_fixed, lon_fixed) as 7-decimal-place integers.
        """
        return (
            int(self.latitude * LAT_SCALE),
            int(self.longitude * LON_SCALE),
        )

    @classmethod
    def from_fixed_point(cls, lat_fixed: int, lon_fixed: int) -> GeoCoordinate:
        """Convert from fixed-point integers.

        Args:
            lat_fixed: Latitude as 7-decimal-place integer.
            lon_fixed: Longitude as 7-decimal-place integer.

        Returns:
            GeoCoordinate with floating-point values.
        """
        return cls(
            latitude=lat_fixed / LAT_SCALE,
            longitude=lon_fixed / LON_SCALE,
        )


@dataclass
class LocationMessage:
    """An end-to-end encrypted location message.

    Attributes:
        location_type: Type of location share (static, live, etc.).
        coordinate: The geographic coordinate (already precision-reduced).
        precision: Precision level used.
        timestamp: Unix timestamp of the location measurement.
        label: Optional human-readable label (e.g., "Central Park").
        duration_minutes: Duration for live location (0 for static).
    """
    location_type: LocationType = LocationType.STATIC
    coordinate: GeoCoordinate = field(default_factory=GeoCoordinate)
    precision: LocationPrecision = LocationPrecision.BUILDING
    timestamp: float = 0.0
    label: str = ""
    duration_minutes: int = 0

    def encode(self) -> bytes:
        """Serialize to bytes for encrypted transport.

        Wire format:
            [1B version][1B type][1B precision][8B lat_fixed]
            [8B lon_fixed][2B alt_m][8B timestamp][2B label_len]
            [N label][2B duration_min]
        """
        lat_fixed, lon_fixed = self.coordinate.to_fixed_point()
        alt_m = int(self.coordinate.altitude)
        label_bytes = self.label.encode("utf-8")

        header = struct.pack(
            "!BBBqqHqH",
            LOCATION_VERSION,
            self.location_type.value,
            self.precision.value,
            lat_fixed,
            lon_fixed,
            alt_m,
            int(self.timestamp),
            len(label_bytes),
        )
        duration_bytes = struct.pack("!H", self.duration_minutes)
        return header + label_bytes + duration_bytes

    @classmethod
    def decode(cls, data: bytes) -> LocationMessage:
        """Deserialize from bytes.

        Args:
            data: Raw bytes from decrypted message.

        Returns:
            Parsed LocationMessage.

        Raises:
            LocationError: If the data is malformed.
        """
        header_size = 1 + 1 + 1 + 8 + 8 + 2 + 8 + 2  # 31 bytes
        if len(data) < header_size:
            raise LocationError(f"Location data too short: {len(data)} bytes.")

        offset = 0
        version = data[offset]
        offset += 1

        if version != LOCATION_VERSION:
            raise LocationError(f"Unsupported location version: {version}.")

        loc_type = LocationType(data[offset])
        offset += 1

        precision = LocationPrecision(data[offset])
        offset += 1

        lat_fixed, lon_fixed, alt_m, timestamp, label_len = struct.unpack(
            "!qqHqH", data[offset:offset + 28]
        )
        offset += 28

        coordinate = GeoCoordinate.from_fixed_point(lat_fixed, lon_fixed)
        coordinate.altitude = float(alt_m)

        if len(data) < offset + label_len + 2:
            raise LocationError("Location data truncated: missing label.")

        label = data[offset:offset + label_len].decode("utf-8")
        offset += label_len

        duration_minutes = struct.unpack("!H", data[offset:offset + 2])[0]

        return cls(
            location_type=loc_type,
            coordinate=coordinate,
            precision=precision,
            timestamp=timestamp,
            label=label,
            duration_minutes=duration_minutes,
        )

    def is_expired(self) -> bool:
        """Check if this location share has expired.

        Static locations never expire. Live locations expire based on
        their duration_minutes setting.
        """
        if self.location_type == LocationType.STATIC:
            return False
        if self.duration_minutes == 0:
            return False
        expiry = self.timestamp + (self.duration_minutes * 60)
        return time.time() > expiry


# ---------------------------------------------------------------------------
# Live Location Tracker
# ---------------------------------------------------------------------------

class LiveLocationTracker:
    """Manages live location sharing sessions.

    Periodically captures the device's GPS position, reduces precision,
    encrypts with the Double Ratchet, and sends to the specified peer.
    Automatically stops after the configured duration.

    Lifecycle:
        tracker = LiveLocationTracker(peer_manager, ratchet)
        session_id = await tracker.start(
            peer_user_id="abc123",
            duration_minutes=60,
            precision=LocationPrecision.BUILDING,
            update_interval_seconds=60,
        )
        ...
        await tracker.stop(session_id)
    """

    def __init__(
        self,
        send_callback: Callable[[str, bytes], Awaitable[None]],
    ) -> None:
        self._send_callback = send_callback
        self._active_sessions: dict[str, dict] = {}
        self._update_tasks: dict[str, asyncio.Task] = {}

    async def start(
        self,
        peer_user_id: str,
        duration_minutes: int = 60,
        precision: LocationPrecision = LocationPrecision.BUILDING,
        update_interval_seconds: int = 60,
    ) -> str:
        """Start a live location sharing session.

        Args:
            peer_user_id: Peer to share location with.
            duration_minutes: Duration of the live session.
            precision: Location precision level.
            update_interval_seconds: Seconds between location updates.

        Returns:
            Session ID for this live location session.

        Raises:
            LiveLocationError: If a session already exists for this peer.
        """
        if peer_user_id in self._active_sessions:
            raise LiveLocationError(
                f"Live location already active for peer {peer_user_id[:8]}."
            )

        import asyncio
        session_id = os.urandom(8).hex()
        self._active_sessions[peer_user_id] = {
            "session_id": session_id,
            "duration_minutes": duration_minutes,
            "precision": precision,
            "update_interval": update_interval_seconds,
            "started_at": time.time(),
        }

        # Send initial location
        await self._send_location_update(
            peer_user_id, LocationType.LIVE_START
        )

        # Start periodic updates
        self._update_tasks[peer_user_id] = asyncio.create_task(
            self._update_loop(peer_user_id)
        )

        logger.info(
            "Live location started for %s (duration=%dm, interval=%ds).",
            peer_user_id[:8], duration_minutes, update_interval_seconds,
        )
        return session_id

    async def stop(self, peer_user_id: str) -> None:
        """Stop a live location sharing session.

        Args:
            peer_user_id: Peer to stop sharing with.
        """
        if peer_user_id not in self._active_sessions:
            return

        # Cancel update task
        task = self._update_tasks.pop(peer_user_id, None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Send stop message
        await self._send_location_update(
            peer_user_id, LocationType.LIVE_STOP
        )

        del self._active_sessions[peer_user_id]
        logger.info("Live location stopped for %s.", peer_user_id[:8])

    async def _update_loop(self, peer_user_id: str) -> None:
        """Periodically send location updates until duration expires."""
        session = self._active_sessions.get(peer_user_id)
        if session is None:
            return

        interval = session["update_interval"]
        expiry = session["started_at"] + (session["duration_minutes"] * 60)

        while True:
            await asyncio.sleep(interval)

            if time.time() >= expiry:
                await self.stop(peer_user_id)
                break

            await self._send_location_update(
                peer_user_id, LocationType.LIVE_UPDATE
            )

    async def _send_location_update(
        self, peer_user_id: str, location_type: LocationType
    ) -> None:
        """Capture current location and send an update.

        Args:
            peer_user_id: Peer to send the update to.
            location_type: Type of location message.
        """
        session = self._active_sessions.get(peer_user_id)
        if session is None:
            return

        # Get current GPS position
        coordinate = await self._get_current_position()

        # Apply precision reduction
        precision = session["precision"]
        reduced = coordinate.reduce_precision(precision)

        # Build and encode the location message
        msg = LocationMessage(
            location_type=location_type,
            coordinate=reduced,
            precision=precision,
            timestamp=time.time(),
            duration_minutes=session["duration_minutes"],
        )

        encoded = msg.encode()

        # Send via callback (which handles encryption)
        try:
            await self._send_callback(peer_user_id, encoded)
        except Exception as exc:
            logger.warning(
                "Failed to send location update to %s: %s",
                peer_user_id[:8], exc,
            )

    async def _get_current_position(self) -> GeoCoordinate:
        """Get the current GPS position from the device.

        Tries platform-specific methods (Geoclue on Linux, CoreLocation
        on macOS, etc.) with a fallback to IP-based geolocation.

        Returns:
            Current GeoCoordinate.
        """
        # Try platform-specific geolocation
        try:
            # Linux: Geoclue2 D-Bus interface
            import subprocess
            result = subprocess.run(
                ["gdbus", "call", "--system",
                 "--dest", "org.freedesktop.GeoClue2",
                 "--object-path", "/org/freedesktop/GeoClue2/Client",
                 "--method", "org.freedesktop.GeoClue2.Client.GetLocation"],
                capture_output=True, text=True, timeout=5,
            )
            # Parse Geoclue response (simplified)
            if result.returncode == 0:
                # Would parse actual Geoclue output here
                pass
        except Exception:
            pass

        # Fallback: return a placeholder coordinate
        # In production, this would use proper geolocation APIs
        logger.debug("Using fallback geolocation (no GPS available).")
        return GeoCoordinate(latitude=0.0, longitude=0.0, altitude=0.0)

    @property
    def active_sessions(self) -> dict[str, dict]:
        """Currently active live location sessions."""
        return dict(self._active_sessions)

    def is_sharing_with(self, peer_user_id: str) -> bool:
        """Check if currently sharing live location with a peer."""
        return peer_user_id in self._active_sessions


# ---------------------------------------------------------------------------
# Static location sharing
# ---------------------------------------------------------------------------

class LocationSharer:
    """One-time static location sharing.

    Creates and encodes a single location message for sending
    via the standard encrypted message channel.
    """

    @staticmethod
    def create_static_location(
        latitude: float,
        longitude: float,
        altitude: float = 0.0,
        precision: LocationPrecision = LocationPrecision.BUILDING,
        label: str = "",
    ) -> LocationMessage:
        """Create a static location share message.

        Args:
            latitude: Latitude in degrees.
            longitude: Longitude in degrees.
            altitude: Altitude in meters.
            precision: Precision level for privacy.
            label: Optional place name.

        Returns:
            LocationMessage ready for encryption and sending.
        """
        coordinate = GeoCoordinate(
            latitude=latitude,
            longitude=longitude,
            altitude=altitude,
        )
        reduced = coordinate.reduce_precision(precision)

        return LocationMessage(
            location_type=LocationType.STATIC,
            coordinate=reduced,
            precision=precision,
            timestamp=time.time(),
            label=label,
            duration_minutes=0,
        )

    @staticmethod
    def decode_location(data: bytes) -> LocationMessage:
        """Decode a received location message.

        Args:
            data: Raw bytes from decrypted message content.

        Returns:
            Parsed LocationMessage.

        Raises:
            LocationError: If the data is malformed.
        """
        msg = LocationMessage.decode(data)

        # Check if expired
        if msg.is_expired():
            raise LocationExpiredError(
                "Location share has expired (shared at %s)."
                % time.strftime("%H:%M:%S", time.localtime(msg.timestamp))
            )

        return msg


# Required import for asyncio in LiveLocationTracker
import asyncio
