"""Crypto Agility for Presidium — versioned crypto suite negotiation.

Implements a versioned crypto suite system that allows Presidium to
evolve its cryptographic primitives without breaking compatibility.
During handshake, peers negotiate the highest common suite they both
support.

Suite definitions:
    Suite v1 (legacy): X25519 + Ed25519 + AES-256-GCM
        - Classical security, compatible with all versions
        - Default for backward compatibility

    Suite v2 (post-quantum): X25519/ML-KEM-768 + Ed25519/LMS + AES-256-GCM
        - Hybrid classical + post-quantum key exchange
        - LMS for long-term signatures
        - Recommended for new conversations

    Suite v3 (future): X25519/ML-KEM-1024 + SLH-DSA + AES-256-GCM
        - Higher PQ security level
        - Stateless hash-based signatures (SLH-DSA / SPHINCS+)
        - Not yet implemented (placeholder)

Negotiation protocol:
    1. Initiator sends HELLO with list of supported suites (highest first)
    2. Responder selects highest common suite
    3. Responder sends HELLO_ACK with selected suite
    4. Both parties use the selected suite for the session

Migration path:
    - Existing v1 sessions continue until explicitly upgraded
    - New sessions default to v2 if both peers support it
    - Users can force v1 in settings for compatibility
    - Automatic v1→v2 upgrade: when both peers are online and
      support v2, a KEY_UPGRADE message re-keys the session

Storage:
    Each contact's supported suite version is cached locally.
    When a peer updates their client, the cache is refreshed on
    next handshake.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from presidium.core.crypto import (
    AES256GCM,
    CryptoError,
    Ed25519KeyPair,
    X25519KeyPair,
)
from presidium.core.crypto.pq_crypto import (
    HybridCiphertext,
    HybridKeyExchange,
    HybridPublicKey,
    MLKEM768,
    OQSNotAvailableError,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Crypto suite definitions
# ---------------------------------------------------------------------------

class CryptoSuite(enum.IntEnum):
    """Versioned crypto suite identifiers."""
    V1_CLASSICAL = 1    # X25519 + Ed25519 + AES-256-GCM
    V2_HYBRID_PQ = 2   # X25519/ML-KEM-768 + Ed25519/LMS + AES-256-GCM
    V3_FUTURE = 3       # X25519/ML-KEM-1024 + SLH-DSA + AES-256-GCM (reserved)


# ---------------------------------------------------------------------------
# Suite capabilities
# ---------------------------------------------------------------------------

@dataclass
class SuiteCapabilities:
    """Describes what a crypto suite supports."""
    suite: CryptoSuite = CryptoSuite.V1_CLASSICAL
    kem_algorithm: str = "X25519"          # Key encapsulation
    sig_algorithm: str = "Ed25519"          # Signature
    aead_algorithm: str = "AES-256-GCM"     # Authenticated encryption
    is_post_quantum: bool = False
    pq_kem_algorithm: str = ""              # Post-quantum KEM (if hybrid)
    pq_sig_algorithm: str = ""              # Post-quantum signature (if hybrid)
    security_level_bits: int = 128          # Classical security level
    pq_security_level_bits: int = 0         # Post-quantum security level


# Pre-defined capabilities for each suite
SUITE_CAPABILITIES: dict[CryptoSuite, SuiteCapabilities] = {
    CryptoSuite.V1_CLASSICAL: SuiteCapabilities(
        suite=CryptoSuite.V1_CLASSICAL,
        kem_algorithm="X25519",
        sig_algorithm="Ed25519",
        aead_algorithm="AES-256-GCM",
        is_post_quantum=False,
        security_level_bits=128,
    ),
    CryptoSuite.V2_HYBRID_PQ: SuiteCapabilities(
        suite=CryptoSuite.V2_HYBRID_PQ,
        kem_algorithm="X25519/ML-KEM-768",
        sig_algorithm="Ed25519/LMS",
        aead_algorithm="AES-256-GCM",
        is_post_quantum=True,
        pq_kem_algorithm="ML-KEM-768",
        pq_sig_algorithm="LMS",
        security_level_bits=128,
        pq_security_level_bits=192,
    ),
    CryptoSuite.V3_FUTURE: SuiteCapabilities(
        suite=CryptoSuite.V3_FUTURE,
        kem_algorithm="X25519/ML-KEM-1024",
        sig_algorithm="SLH-DSA",
        aead_algorithm="AES-256-GCM",
        is_post_quantum=True,
        pq_kem_algorithm="ML-KEM-1024",
        pq_sig_algorithm="SLH-DSA",
        security_level_bits=128,
        pq_security_level_bits=256,
    ),
}


# ---------------------------------------------------------------------------
# Suite negotiation result
# ---------------------------------------------------------------------------

@dataclass
class NegotiatedSuite:
    """Result of crypto suite negotiation between two peers."""
    selected_suite: CryptoSuite = CryptoSuite.V1_CLASSICAL
    capabilities: SuiteCapabilities = field(
        default_factory=lambda: SUITE_CAPABILITIES[CryptoSuite.V1_CLASSICAL]
    )
    is_downgrade: bool = False  # True if we had to fall back from our preferred suite
    is_pq_available: bool = False  # True if post-quantum is active
    fallback_reason: str = ""


# ---------------------------------------------------------------------------
# Suite negotiator
# ---------------------------------------------------------------------------

class SuiteNegotiator:
    """Negotiates the crypto suite to use with a peer.

    Strategy:
        1. Both peers advertise their supported suites (highest first)
        2. Select the highest suite that both support
        3. If PQ suite is selected but PQ library unavailable, fall back
        4. Cache the negotiated suite per peer for future sessions

    Usage:
        negotiator = SuiteNegotiator(preferred_suite=CryptoSuite.V2_HYBRID_PQ)
        result = negotiator.negotiate(peer_supported_suites=[1, 2])
        if result.selected_suite == CryptoSuite.V2_HYBRID_PQ:
            # Use hybrid key exchange
    """

    def __init__(
        self,
        preferred_suite: CryptoSuite = CryptoSuite.V2_HYBRID_PQ,
        force_classical: bool = False,
    ) -> None:
        self._preferred = preferred_suite
        self._force_classical = force_classical
        self._peer_suites: dict[str, list[CryptoSuite]] = {}  # peer_id → supported
        self._negotiated: dict[str, NegotiatedSuite] = {}  # peer_id → result

    @property
    def local_supported_suites(self) -> list[CryptoSuite]:
        """Get the list of suites this node supports (highest first)."""
        suites = [CryptoSuite.V1_CLASSICAL]

        if not self._force_classical and MLKEM768.is_available():
            suites.insert(0, CryptoSuite.V2_HYBRID_PQ)

        return suites

    def negotiate(
        self,
        peer_id: str,
        peer_supported: list[int],
    ) -> NegotiatedSuite:
        """Negotiate the best crypto suite with a peer.

        Args:
            peer_id: The peer's user ID.
            peer_supported: List of suite versions the peer supports.

        Returns:
            NegotiatedSuite with the selected suite and metadata.
        """
        # Store peer capabilities
        peer_suites = [CryptoSuite(s) for s in peer_supported]
        self._peer_suites[peer_id] = peer_suites

        local = set(self.local_supported_suites)
        remote = set(peer_suites)

        # Find highest common suite
        common = local & remote
        if not common:
            # No common suite — fall back to v1
            result = NegotiatedSuite(
                selected_suite=CryptoSuite.V1_CLASSICAL,
                capabilities=SUITE_CAPABILITIES[CryptoSuite.V1_CLASSICAL],
                is_downgrade=True,
                fallback_reason="No common suite; falling back to v1 classical.",
            )
        else:
            selected = max(common)  # Highest common suite

            # Check if PQ is actually available
            is_pq = selected >= CryptoSuite.V2_HYBRID_PQ
            is_downgrade = selected < self._preferred

            fallback_reason = ""
            if is_pq and not MLKEM768.is_available():
                # PQ suite selected but library unavailable
                selected = CryptoSuite.V1_CLASSICAL
                is_pq = False
                is_downgrade = True
                fallback_reason = "ML-KEM-768 library not available."

            result = NegotiatedSuite(
                selected_suite=selected,
                capabilities=SUITE_CAPABILITIES.get(selected, SUITE_CAPABILITIES[CryptoSuite.V1_CLASSICAL]),
                is_downgrade=is_downgrade,
                is_pq_available=is_pq,
                fallback_reason=fallback_reason,
            )

        self._negotiated[peer_id] = result

        if result.is_downgrade:
            logger.info(
                "Suite negotiation with %s: %s (downgrade: %s)",
                peer_id[:8],
                result.selected_suite.name,
                result.fallback_reason or "peer doesn't support higher suite",
            )
        else:
            logger.info(
                "Suite negotiation with %s: %s",
                peer_id[:8],
                result.selected_suite.name,
            )

        return result

    def get_negotiated(self, peer_id: str) -> Optional[NegotiatedSuite]:
        """Get the previously negotiated suite for a peer."""
        return self._negotiated.get(peer_id)

    def invalidate(self, peer_id: str) -> None:
        """Invalidate the cached suite for a peer (force re-negotiation)."""
        self._negotiated.pop(peer_id, None)
        self._peer_suites.pop(peer_id, None)


# ---------------------------------------------------------------------------
# Suite-aware key exchange
# ---------------------------------------------------------------------------

class SuiteAwareKeyExchange:
    """Performs key exchange using the negotiated crypto suite.

    Delegates to either classical X25519 or hybrid X25519+ML-KEM-768
    based on the negotiated suite.
    """

    @staticmethod
    async def initiate(
        suite: NegotiatedSuite,
        peer_x25519_pk: bytes,
        peer_ml_kem_pk: bytes = b"",
    ) -> tuple[bytes, bytes]:
        """Initiate a key exchange with a peer.

        Args:
            suite: The negotiated suite.
            peer_x25519_pk: Peer's X25519 public key (32 bytes).
            peer_ml_kem_pk: Peer's ML-KEM-768 public key (if v2+).

        Returns:
            Tuple of (payload_to_send, shared_secret).
        """
        if suite.is_pq_available and suite.selected_suite >= CryptoSuite.V2_HYBRID_PQ:
            # Hybrid: X25519 + ML-KEM-768
            peer_pk = HybridPublicKey(
                x25519_pk=peer_x25519_pk,
                ml_kem_pk=peer_ml_kem_pk,
            )
            ciphertext, shared_secret = HybridKeyExchange.encapsulate(peer_pk)
            return ciphertext.encode(), shared_secret
        else:
            # Classical: X25519 only
            eph_pk, shared_secret = HybridKeyExchange.encapsulate_classical_only(
                peer_x25519_pk
            )
            return eph_pk, shared_secret

    @staticmethod
    async def respond(
        suite: NegotiatedSuite,
        local_x25519_sk: bytes,
        local_ml_kem_sk: bytes,
        payload: bytes,
    ) -> bytes:
        """Respond to a key exchange (compute shared secret).

        Args:
            suite: The negotiated suite.
            local_x25519_sk: Our X25519 private key.
            local_ml_kem_sk: Our ML-KEM-768 secret key (if v2+).
            payload: The initiator's key exchange payload.

        Returns:
            Shared secret (32 bytes).
        """
        if suite.is_pq_available and suite.selected_suite >= CryptoSuite.V2_HYBRID_PQ:
            ciphertext = HybridCiphertext.decode(payload)
            return HybridKeyExchange.decapsulate(
                local_x25519_sk, local_ml_kem_sk, ciphertext,
            )
        else:
            # Classical: payload is X25519 ephemeral public key
            from cryptography.hazmat.primitives.asymmetric.x25519 import (
                X25519PrivateKey,
                X25519PublicKey,
            )
            priv = X25519PrivateKey.from_private_bytes(local_x25519_sk)
            pub = X25519PublicKey.from_public_bytes(payload[:32])
            classical = priv.exchange(pub)
            import hashlib
            return hashlib.sha256(
                classical + b"presidium-classical-v1"
            ).digest()
