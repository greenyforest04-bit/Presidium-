"""P2P Network security primitives for Presidium.

Provides:
- Hashcash Proof-of-Work for DHT Sybil resistance
- Subnet diversity validation for eclipse attack prevention
- Bloom filter for replay protection
- Per-IP rate limiter for DoS mitigation
- UDP amplification protection
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import math
import os
import struct
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hashcash Proof-of-Work
# ---------------------------------------------------------------------------

HASHCASH_DEFAULT_DIFFICULTY = 4  # Number of leading zero bits
HASHCASH_MIN_DIFFICULTY = 4
HASHCASH_MAX_DIFFICULTY = 20


def hashcash_mint(
    resource: bytes,
    difficulty: int = HASHCASH_DEFAULT_DIFFICULTY,
    max_iterations: int = 10_000_000,
) -> tuple[bytes, bytes]:
    """Generate a Hashcash Proof-of-Work stamp.

    Finds a nonce such that SHA-256(resource || nonce) has at least
    `difficulty` leading zero bits.

    Args:
        resource: The resource identifier (e.g., node_id || timestamp).
        difficulty: Number of required leading zero bits.
        max_iterations: Maximum number of attempts before giving up.

    Returns:
        Tuple of (nonce, stamp_hash) where stamp_hash proves the work.

    Raises:
        ValueError: If difficulty is out of range.
    """
    if difficulty < HASHCASH_MIN_DIFFICULTY or difficulty > HASHCASH_MAX_DIFFICULTY:
        raise ValueError(
            f"Difficulty must be between {HASHCASH_MIN_DIFFICULTY} "
            f"and {HASHCASH_MAX_DIFFICULTY}, got {difficulty}"
        )

    # Target: hash must be less than 2^(64-difficulty) when interpreted as
    # a big-endian integer from the first 8 bytes.
    # This means the top `difficulty` bits must all be zero.
    target = 1 << (64 - difficulty)
    nonce = 0

    while nonce < max_iterations:
        nonce_bytes = nonce.to_bytes(8, "big")
        digest = hashlib.sha256(resource + nonce_bytes).digest()
        # Check first 8 bytes (64 bits) for leading zeros
        value = int.from_bytes(digest[:8], "big")
        if value < target:
            return nonce_bytes, digest
        nonce += 1

    raise ValueError(
        f"Failed to find Hashcash solution after {max_iterations} iterations "
        f"(difficulty={difficulty})"
    )


def hashcash_verify(
    resource: bytes,
    nonce: bytes,
    difficulty: int = HASHCASH_DEFAULT_DIFFICULTY,
) -> bool:
    """Verify a Hashcash Proof-of-Work stamp.

    Args:
        resource: The resource identifier.
        nonce: The nonce produced by hashcash_mint.
        difficulty: Number of required leading zero bits.

    Returns:
        True if the proof is valid, False otherwise.
    """
    if difficulty < HASHCASH_MIN_DIFFICULTY or difficulty > HASHCASH_MAX_DIFFICULTY:
        return False

    if len(nonce) != 8:
        return False

    target = 1 << (64 - difficulty)
    digest = hashlib.sha256(resource + nonce).digest()
    value = int.from_bytes(digest[:8], "big")
    return value < target


def compute_pow_difficulty(peer_count: int) -> int:
    """Compute dynamic PoW difficulty based on network peer count.

    Difficulty scales logarithmically with peer count to prevent
    Sybil attacks in larger networks where the incentive to
    create fake nodes is higher.

    Args:
        peer_count: Current number of known peers.

    Returns:
        Required difficulty (leading zero bits).
    """
    if peer_count < 20:
        return HASHCASH_MIN_DIFFICULTY
    # Scale: difficulty = 4 + floor(log2(peer_count / 20))
    # At 20 peers: 4, at 40: 5, at 80: 6, at 160: 7, etc.
    extra = min(int(math.log2(peer_count / 20)), HASHCASH_MAX_DIFFICULTY - HASHCASH_MIN_DIFFICULTY)
    return HASHCASH_MIN_DIFFICULTY + extra


# ---------------------------------------------------------------------------
# Subnet diversity validation (eclipse attack prevention)
# ---------------------------------------------------------------------------

@dataclass
class SubnetDiversityConfig:
    """Configuration for subnet diversity enforcement."""
    min_subnets: int = 3        # Minimum distinct /24 subnets per lookup
    subnet_prefix_len: int = 24  # /24 subnet prefix length
    min_peers_per_subnet: int = 1  # Minimum peers from each required subnet


def get_subnet(ip_address: str, prefix_len: int = 24) -> str:
    """Get the subnet identifier for an IP address.

    Args:
        ip_address: IP address string (e.g., "192.168.1.5").
        prefix_len: Subnet prefix length (default /24).

    Returns:
        Subnet string (e.g., "192.168.1.0/24").
    """
    try:
        network = ipaddress.ip_network(f"{ip_address}/{prefix_len}", strict=False)
        return str(network)
    except ValueError:
        # Invalid IP — return the raw address as fallback
        return ip_address


def validate_lookup_diversity(
    peers: list[tuple[str, int]],  # (ip, port) pairs
    config: Optional[SubnetDiversityConfig] = None,
) -> bool:
    """Validate that a set of lookup results includes sufficient subnet diversity.

    This prevents eclipse attacks where an attacker fills routing tables
    with nodes from a single subnet, allowing them to control all responses.

    Args:
        peers: List of (ip, port) tuples from a lookup.
        config: Diversity configuration. Uses defaults if None.

    Returns:
        True if the lookup results have sufficient diversity.
    """
    if config is None:
        config = SubnetDiversityConfig()

    subnets: set[str] = set()
    for ip, _port in peers:
        subnet = get_subnet(ip, config.subnet_prefix_len)
        subnets.add(subnet)

    if len(subnets) < config.min_subnets:
        logger.warning(
            "Eclipse attack risk: lookup returned peers from only %d /%d subnet(s), "
            "minimum %d required. Subnets: %s",
            len(subnets), config.subnet_prefix_len, config.min_subnets, subnets,
        )
        return False

    return True


# ---------------------------------------------------------------------------
# Bloom filter for replay protection
# ---------------------------------------------------------------------------

class ReplayBloomFilter:
    """Time-bounded Bloom filter for replay attack prevention.

    Uses a counting Bloom filter with periodic resets to provide
    replay protection with a configurable TTL (time-to-live).
    Nonces are added and checked in O(1) time with bounded false
    positive rate.
    """

    def __init__(
        self,
        expected_items: int = 1_000_000,
        false_positive_rate: float = 0.001,
        ttl_seconds: float = 3600.0,  # 1 hour
    ) -> None:
        """Initialize the Bloom filter.

        Args:
            expected_items: Maximum number of items expected in one TTL window.
            false_positive_rate: Target false positive rate (0.001 = 0.1%).
            ttl_seconds: Time before the filter resets (default 1 hour).
        """
        self._ttl = ttl_seconds
        self._last_reset = time.monotonic()

        # Calculate optimal filter size
        # m = -(n * ln(p)) / (ln(2)^2)
        # k = (m/n) * ln(2)
        n = expected_items
        p = false_positive_rate
        self._size = max(1024, int(-(n * math.log(p)) / (math.log(2) ** 2)))
        self._num_hashes = max(1, int((self._size / n) * math.log(2)))

        # Use a bit array (bytearray for simplicity)
        self._bits = bytearray((self._size + 7) // 8)
        self._count = 0

    def _hash_positions(self, item: bytes) -> list[int]:
        """Compute hash positions for an item using double hashing."""
        h1 = int(hashlib.sha256(item).hexdigest(), 16)
        h2 = int(hashlib.sha256(item + b"\x01").hexdigest(), 16)
        return [(h1 + i * h2) % self._size for i in range(self._num_hashes)]

    def _check_reset(self) -> None:
        """Reset the filter if TTL has expired."""
        now = time.monotonic()
        if now - self._last_reset >= self._ttl:
            self._bits = bytearray((self._size + 7) // 8)
            self._count = 0
            self._last_reset = now

    def add(self, item: bytes) -> None:
        """Add an item to the Bloom filter."""
        self._check_reset()
        for pos in self._hash_positions(item):
            byte_idx = pos // 8
            bit_idx = pos % 8
            self._bits[byte_idx] |= (1 << bit_idx)
        self._count += 1

    def check(self, item: bytes) -> bool:
        """Check if an item might be in the filter (replay detected).

        Returns:
            True if the item might have been seen before (potential replay).
            False if the item is definitely new.
        """
        self._check_reset()
        for pos in self._hash_positions(item):
            byte_idx = pos // 8
            bit_idx = pos % 8
            if not (self._bits[byte_idx] & (1 << bit_idx)):
                return False
        return True

    def check_and_add(self, item: bytes) -> bool:
        """Check if an item is a replay, and add it if new.

        Returns:
            True if the item was already seen (replay detected).
            False if the item is new (and was added to the filter).
        """
        is_replay = self.check(item)
        if not is_replay:
            self.add(item)
        return is_replay

    @property
    def count(self) -> int:
        """Number of items added in current TTL window."""
        return self._count


# ---------------------------------------------------------------------------
# Per-IP rate limiter
# ---------------------------------------------------------------------------

@dataclass
class RateLimitEntry:
    """Tracks rate limit state for a single IP address."""
    connection_count: int = 0
    last_connection_time: float = 0.0
    window_start: float = 0.0
    request_count: int = 0


class IPRateLimiter:
    """Per-IP rate limiter to prevent DoS attacks.

    Tracks connection attempts and request rates per IP address,
    enforcing configurable limits to prevent abuse.
    """

    def __init__(
        self,
        max_connections_per_minute: int = 30,
        max_requests_per_minute: int = 120,
        window_seconds: float = 60.0,
    ) -> None:
        """Initialize the rate limiter.

        Args:
            max_connections_per_minute: Max new connections per IP per minute.
            max_requests_per_minute: Max requests per IP per minute.
            window_seconds: Sliding window duration in seconds.
        """
        self._max_conn = max_connections_per_minute
        self._max_req = max_requests_per_minute
        self._window = window_seconds
        self._entries: dict[str, RateLimitEntry] = defaultdict(RateLimitEntry)

    def check_connection(self, ip_address: str) -> bool:
        """Check if a new connection from this IP is allowed.

        Returns:
            True if the connection is allowed, False if rate limited.
        """
        entry = self._entries[ip_address]
        now = time.monotonic()

        # Reset window if expired
        if now - entry.window_start >= self._window:
            entry.window_start = now
            entry.connection_count = 0
            entry.request_count = 0

        if entry.connection_count >= self._max_conn:
            logger.warning(
                "Rate limit: IP %s exceeded %d connections per %.0fs",
                ip_address, self._max_conn, self._window,
            )
            return False

        entry.connection_count += 1
        entry.last_connection_time = now
        return True

    def check_request(self, ip_address: str) -> bool:
        """Check if a request from this IP is allowed.

        Returns:
            True if the request is allowed, False if rate limited.
        """
        entry = self._entries[ip_address]
        now = time.monotonic()

        # Reset window if expired
        if now - entry.window_start >= self._window:
            entry.window_start = now
            entry.connection_count = 0
            entry.request_count = 0

        if entry.request_count >= self._max_req:
            return False

        entry.request_count += 1
        return True

    def cleanup(self, max_age: float = 300.0) -> None:
        """Remove stale entries older than max_age seconds."""
        now = time.monotonic()
        stale = [
            ip for ip, entry in self._entries.items()
            if now - entry.last_connection_time > max_age
        ]
        for ip in stale:
            del self._entries[ip]


# ---------------------------------------------------------------------------
# UDP amplification protection
# ---------------------------------------------------------------------------

class AmplificationProtector:
    """Protects against UDP amplification attacks.

    Enforces the rule that response size must not exceed 2× the
    request size, preventing attackers from using the node as an
    amplification vector for DDoS attacks.
    """

    MAX_AMPLIFICATION_FACTOR = 2.0  # Response must be ≤ 2× request size
    MAX_RESPONSE_SIZE = 1400  # bytes — fits in single UDP datagram

    @classmethod
    def check_response_size(
        cls,
        request_size: int,
        response_size: int,
    ) -> bool:
        """Check if a response violates amplification limits.

        Args:
            request_size: Size of the incoming request in bytes.
            response_size: Size of the proposed response in bytes.

        Returns:
            True if the response is within limits, False if it violates.
        """
        if response_size > cls.MAX_RESPONSE_SIZE:
            logger.warning(
                "Amplification protection: response %d bytes exceeds max %d",
                response_size, cls.MAX_RESPONSE_SIZE,
            )
            return False

        max_allowed = int(request_size * cls.MAX_AMPLIFICATION_FACTOR)
        if response_size > max_allowed:
            logger.warning(
                "Amplification protection: response %d bytes > %.1f× request %d bytes",
                response_size, cls.MAX_AMPLIFICATION_FACTOR, request_size,
            )
            return False

        return True
