"""Contact verification via Safety Numbers for Presidium.

Implements a safety number system (similar to Signal) that allows
users to verify they are communicating with the correct person by
comparing a short numeric code out-of-band.

How it works:
    1. Each contact pair has a "Safety Number" derived from both
       parties' Ed25519 public keys
    2. The Safety Number is a 60-digit decimal number, displayed as
       groups of 5 digits for easy comparison
    3. Both parties compute the same Safety Number independently
    4. Users compare Safety Numbers via a different channel (in-person,
       phone call, etc.)
    5. If the numbers match, the contact is verified

Safety Number computation:
    safety_number = truncate(
        SHA-256(
            SHA-256(public_key_A) || SHA-256(public_key_B)
        ),
        30 bytes
    ).to_decimal()

    Display: 01234 56789 01234 56789 01234 56789 01234 56789 01234 56789 01234 56789

This provides 120 bits of security against collision attacks,
which is sufficient for human verification.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Verification status
# ---------------------------------------------------------------------------

class VerificationLevel(Enum):
    """Level of verification for a contact."""
    UNVERIFIED = "unverified"          # No verification performed
    PARTIALLY_VERIFIED = "partial"     # One party verified (bridge contact)
    VERIFIED = "verified"             # Safety number confirmed out-of-band
    VERIFIED_WITH_CHANGE = "changed"   # Safety number changed since last verification


# ---------------------------------------------------------------------------
# Safety number
# ---------------------------------------------------------------------------

@dataclass
class SafetyNumber:
    """A computed safety number for a contact pair.

    The safety number is derived deterministically from both parties'
    public keys, so both parties compute the same number independently.
    """
    user_id_a: str = ""
    public_key_a: bytes = b""
    user_id_b: str = ""
    public_key_b: bytes = b""
    number: str = ""          # 60-digit decimal string
    fingerprint: str = ""     # Formatted for display (groups of 5)
    level: VerificationLevel = VerificationLevel.UNVERIFIED
    verified_at: float = 0.0  # Timestamp of last verification

    @classmethod
    def compute(
        cls,
        user_id_a: str,
        public_key_a: bytes,
        user_id_b: str,
        public_key_b: bytes,
    ) -> SafetyNumber:
        """Compute a Safety Number for a pair of users.

        The computation is deterministic and order-independent
        (lesser user_id comes first to ensure both parties get
        the same result).

        Args:
            user_id_a: First user's ID (32 hex chars).
            public_key_a: First user's Ed25519 public key (32 bytes).
            user_id_b: Second user's ID (32 hex chars).
            public_key_b: Second user's Ed25519 public key (32 bytes).

        Returns:
            A SafetyNumber instance.
        """
        # Canonical ordering: lower user_id first
        if user_id_a > user_id_b:
            user_id_a, user_id_b = user_id_b, user_id_a
            public_key_a, public_key_b = public_key_b, public_key_a

        # Compute fingerprints
        fp_a = hashlib.sha256(public_key_a).digest()
        fp_b = hashlib.sha256(public_key_b).digest()

        # Concatenate and hash
        combined = hashlib.sha256(fp_a + fp_b).digest()

        # Take first 30 bytes and convert to decimal
        truncated = combined[:30]
        number_int = int.from_bytes(truncated, byteorder="big")

        # Format as 60-digit string (pad with leading zeros)
        number_str = f"{number_int:060d}"

        # Format for display: groups of 5 digits
        groups = [number_str[i:i+5] for i in range(0, 60, 5)]
        fingerprint = "  ".join(groups)

        return cls(
            user_id_a=user_id_a,
            public_key_a=public_key_a,
            user_id_b=user_id_b,
            public_key_b=public_key_b,
            number=number_str,
            fingerprint=fingerprint,
        )

    def matches(self, other: SafetyNumber) -> bool:
        """Check if two safety numbers match."""
        return self.number == other.number

    def format_display(self) -> str:
        """Format the safety number for user display."""
        return self.fingerprint

    def format_compact(self) -> str:
        """Format as a compact 12-character code (first and last groups).

        This is useful for quick verbal comparison:
        "Alpha-Charlie-01234-56789"
        """
        groups = [self.number[i:i+5] for i in range(0, 60, 5)]
        return f"{groups[0]}-{groups[1]}-{groups[-2]}-{groups[-1]}"


# ---------------------------------------------------------------------------
# Contact verification manager
# ---------------------------------------------------------------------------

class ContactVerification:
    """Manages contact verification state and safety number comparison.

    Tracks which contacts have been verified, detects safety number
    changes (e.g., after a key rotation), and provides UI-ready
    verification status for each contact.
    """

    def __init__(self) -> None:
        # Verified contacts: peer_user_id → SafetyNumber
        self._verified: dict[str, SafetyNumber] = {}

        # Previous safety numbers (to detect changes)
        self._previous: dict[str, str] = {}

    # -------------------------------------------------------------------
    # Verification operations
    # -------------------------------------------------------------------

    def compute_safety_number(
        self,
        local_user_id: str,
        local_public_key: bytes,
        peer_user_id: str,
        peer_public_key: bytes,
    ) -> SafetyNumber:
        """Compute the safety number for a contact.

        Args:
            local_user_id: Our user ID.
            local_public_key: Our Ed25519 public key.
            peer_user_id: The contact's user ID.
            peer_public_key: The contact's Ed25519 public key.

        Returns:
            The computed SafetyNumber.
        """
        return SafetyNumber.compute(
            user_id_a=local_user_id,
            public_key_a=local_public_key,
            user_id_b=peer_user_id,
            public_key_b=peer_public_key,
        )

    def verify_contact(
        self,
        peer_user_id: str,
        safety_number: SafetyNumber,
    ) -> VerificationLevel:
        """Mark a contact as verified with a safety number.

        Checks if the safety number has changed since the last
        verification and returns the appropriate verification level.

        Args:
            peer_user_id: The contact's user ID.
            safety_number: The verified safety number.

        Returns:
            The resulting verification level.
        """
        previous = self._verified.get(peer_user_id)

        if previous and previous.number != safety_number.number:
            # Safety number changed — key rotation or potential MITM
            self._previous[peer_user_id] = previous.number
            import time
            safety_number.level = VerificationLevel.VERIFIED_WITH_CHANGE
            safety_number.verified_at = time.time()
            self._verified[peer_user_id] = safety_number
            logger.warning(
                "Safety number CHANGED for contact %s! "
                "Previous: %s... New: %s...",
                peer_user_id[:8],
                previous.number[:10],
                safety_number.number[:10],
            )
            return VerificationLevel.VERIFIED_WITH_CHANGE

        import time
        safety_number.level = VerificationLevel.VERIFIED
        safety_number.verified_at = time.time()
        self._verified[peer_user_id] = safety_number

        logger.info("Contact %s verified (safety number: %s...)",
                     peer_user_id[:8], safety_number.number[:10])
        return VerificationLevel.VERIFIED

    def unverify_contact(self, peer_user_id: str) -> None:
        """Remove verification for a contact."""
        self._verified.pop(peer_user_id, None)

    # -------------------------------------------------------------------
    # Query methods
    # -------------------------------------------------------------------

    def get_verification_level(self, peer_user_id: str) -> VerificationLevel:
        """Get the current verification level for a contact."""
        sn = self._verified.get(peer_user_id)
        if sn is None:
            return VerificationLevel.UNVERIFIED
        return sn.level

    def get_safety_number(self, peer_user_id: str) -> Optional[SafetyNumber]:
        """Get the stored safety number for a contact."""
        return self._verified.get(peer_user_id)

    def has_safety_number_changed(self, peer_user_id: str) -> bool:
        """Check if the safety number has changed since last verification."""
        return peer_user_id in self._previous

    def get_previous_safety_number(self, peer_user_id: str) -> Optional[str]:
        """Get the previous safety number string (before change)."""
        return self._previous.get(peer_user_id)

    def get_all_verified(self) -> dict[str, SafetyNumber]:
        """Get all verified contacts."""
        return dict(self._verified)

    # -------------------------------------------------------------------
    # Bulk operations
    # -------------------------------------------------------------------

    def export_verification_state(self) -> dict[str, dict[str, str]]:
        """Export verification state for backup.

        Returns:
            Dict mapping peer_user_id to verification info.
        """
        result: dict[str, dict[str, str]] = {}
        for peer_id, sn in self._verified.items():
            result[peer_id] = {
                "safety_number": sn.number,
                "level": sn.level.value,
                "verified_at": str(sn.verified_at),
            }
        return result

    def import_verification_state(self, data: dict[str, dict[str, str]]) -> None:
        """Import verification state from a backup."""
        for peer_id, info in data.items():
            sn = SafetyNumber(
                user_id_b=peer_id,
                number=info.get("safety_number", ""),
                level=VerificationLevel(info.get("level", "unverified")),
                verified_at=float(info.get("verified_at", "0")),
            )
            self._verified[peer_id] = sn
