"""Contact Discovery v2 for Presidium — privacy-preserving contact matching.

Implements a contact discovery protocol based on RSA blind signatures so that
the server never sees plaintext contact identifiers (phone numbers, emails,
usernames).  Only blinded hashes leave the client; the server signs them
without learning what they represent, and the client unblinds the signature
locally.  Matching is performed by comparing unblinded signatures against a
pre-registered set on the server — the server knows *that* two users share
a contact but not *which* contact.

Architecture:
    BlindSignatureProtocol
        ├── RSA key generation (2048-bit default)
        ├── blind(message_hash, public_key) → blinded_token
        ├── sign(blinded_token, private_key) → blinded_signature
        └── unblind(blinded_signature, blinding_factor, public_key) → signature

    DiscoveryServer
        ├── Holds registered contact tokens (unblinded signatures)
        ├── Signs blinded tokens without seeing plaintext
        ├── Matches client tokens against registered set
        └── Rate limits: 100 discovery requests / hour / user

    DiscoveryClient
        ├── Hashes contact identifiers (SHA-256 + truncation)
        ├── Blinds each hash for submission
        ├── Receives and unblinds server signatures
        └── Compares unblinded signatures with server's match set

    DiscoveryToken
        ├── blinded_token:  the value sent to the server
        ├── signature:      the server's signature on the blinded token
        └── unblinded_sig:  the unblinded signature (verifiable locally)

    ContactDiscoveryV2
        ├── Integrates client + server in one class for local testing
        ├── discover_contacts() async — full protocol round-trip
        └── register_contacts() async — pre-register own contact tokens

Privacy properties:
    - Server never sees plaintext phone numbers / emails
    - Server cannot link a blinded token to a specific contact
    - Blinding factor is client-side secret, never transmitted
    - Rate limiting prevents brute-force enumeration
    - Tokens are single-use per discovery session (fresh blinding each time)

Wire protocol (conceptual):
    Client → Server:  POST /discover
        { user_id, [blinded_token_1, ..., blinded_token_n] }

    Server → Client:  200 OK
        { signatures: [sig_1, ..., sig_n], matches: [index_1, ...] }
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RSA_KEY_BITS: int = 2048
"""Default RSA key size for blind signatures."""

CONTACT_HASH_BYTES: int = 32
"""SHA-256 output length used for contact hashing."""

RATE_LIMIT_PER_HOUR: int = 100
"""Maximum discovery requests per user per hour."""

RATE_LIMIT_WINDOW: int = 3600
"""Rate limit window in seconds (1 hour)."""

MAX_CONTACTS_PER_REQUEST: int = 512
"""Maximum number of contact hashes in a single discovery request."""

TOKEN_EXPIRY_SECONDS: int = 86400 * 30
"""How long a registered contact token remains valid (30 days)."""


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class DiscoveryV2Error(Exception):
    """Base exception for Discovery v2 operations."""


class BlindSignatureError(DiscoveryV2Error):
    """Cryptographic error in the blind signature protocol."""


class RateLimitExceededError(DiscoveryV2Error):
    """User has exceeded the discovery request rate limit."""


class InvalidTokenError(DiscoveryV2Error):
    """A submitted discovery token is malformed or expired."""


class TooManyContactsError(DiscoveryV2Error):
    """Too many contact hashes submitted in a single request."""


class ServerKeyError(DiscoveryV2Error):
    """Server RSA key is not available or invalid."""


# ---------------------------------------------------------------------------
# Discovery token dataclass
# ---------------------------------------------------------------------------

@dataclass
class DiscoveryToken:
    """A single token in the contact discovery protocol.

    Attributes:
        contact_hash:  SHA-256 hash of the contact identifier (phone, email).
        blinded_token: The RSA-blinded hash sent to the server.
        blinding_factor: Secret random value used for blinding (never sent).
        signature: Server's RSA signature on the blinded token.
        unblinded_sig: Signature after removing the blinding factor.
    """
    contact_hash: bytes = b""
    blinded_token: bytes = b""
    blinding_factor: int = 0
    signature: bytes = b""
    unblinded_sig: bytes = b""

    @property
    def is_complete(self) -> bool:
        """Whether the token has been through the full blind-sign-unblind cycle."""
        return bool(self.unblinded_sig)


# ---------------------------------------------------------------------------
# Blind Signature Protocol
# ---------------------------------------------------------------------------

class BlindSignatureProtocol:
    """RSA blind signature operations for contact discovery.

    Uses textbook RSA with SHA-256 as the hash function.  The protocol
    works as follows:

    1. **Blind**:  The client computes ``blinded = hash * r^e mod n``
       where *r* is a random blinding factor and *(e, n)* is the server's
       public key.

    2. **Sign**:   The server computes ``sig = blinded^d mod n`` where
       *d* is its private exponent.

    3. **Unblind**: The client computes ``unblinded = sig * r^{-1} mod n``,
       which equals ``hash^d mod n`` — a valid RSA signature on the
       original hash.

    All operations use Python's built-in ``pow()`` with three arguments
    for efficient modular exponentiation.
    """

    @staticmethod
    def generate_keypair(bits: int = RSA_KEY_BITS) -> tuple[int, int, int, int, int]:
        """Generate an RSA key pair and return (n, e, d, p, q).

        Args:
            bits: Key size in bits.

        Returns:
            Tuple of (modulus n, public exponent e, private exponent d,
            prime p, prime q).
        """
        # Prefer sympy for fast prime generation, but fall back to
        # cryptography's secure RSA generation if sympy is not available
        try:
            from sympy import randprime

            half = bits // 2
            while True:
                p = randprime(2 ** (half - 1), 2 ** half)
                q = randprime(2 ** (half - 1), 2 ** half)
                if p == q:
                    continue
                n = p * q
                phi = (p - 1) * (q - 1)
                e = 65537
                from math import gcd
                if gcd(e, phi) != 1:
                    continue
                d = pow(e, -1, phi)
                return (n, e, d, p, q)
        except Exception:
            # Use cryptography to generate a proper RSA keypair
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.primitives import serialization

            priv = rsa.generate_private_key(public_exponent=65537, key_size=bits)
            nums = priv.private_numbers()
            p = nums.p
            q = nums.q
            d = nums.d
            e = nums.public_numbers.e
            n = nums.public_numbers.n
            return (n, e, d, p, q)

    @staticmethod
    def _hash_to_int(data: bytes) -> int:
        """Convert raw bytes to an integer suitable for RSA operations.

        Args:
            data: Hash bytes (typically SHA-256 output).

        Returns:
            Integer representation.
        """
        return int.from_bytes(data, byteorder="big")

    @staticmethod
    def blind(contact_hash: bytes, e: int, n: int) -> tuple[bytes, int]:
        """Blind a contact hash with a random factor.

        Args:
            contact_hash: SHA-256 hash of the contact identifier.
            e: Server's public exponent.
            n: Server's RSA modulus.

        Returns:
            Tuple of (blinded_token_bytes, blinding_factor).

        Raises:
            BlindSignatureError: If the hash is too large for the modulus.
        """
        h = BlindSignatureProtocol._hash_to_int(contact_hash)
        if h >= n:
            raise BlindSignatureError("Contact hash exceeds RSA modulus size.")

        # Generate random blinding factor coprime to n
        r = 0
        from math import gcd
        while r <= 1 or gcd(r, n) != 1:
            r = int.from_bytes(os.urandom((n.bit_length() + 7) // 8), byteorder="big") % n

        # blinded = hash * r^e mod n
        blinded = (h * pow(r, e, n)) % n
        blinded_bytes = blinded.to_bytes((n.bit_length() + 7) // 8, byteorder="big")

        return blinded_bytes, r

    @staticmethod
    def sign(blinded_token: bytes, d: int, n: int) -> bytes:
        """Sign a blinded token with the server's private key.

        Args:
            blinded_token: The blinded value received from the client.
            d: Server's private exponent.
            n: Server's RSA modulus.

        Returns:
            Signature bytes on the blinded token.
        """
        m = int.from_bytes(blinded_token, byteorder="big")
        sig = pow(m, d, n)
        return sig.to_bytes((n.bit_length() + 7) // 8, byteorder="big")

    @staticmethod
    def unblind(
        blinded_signature: bytes,
        blinding_factor: int,
        e: int,
        n: int,
    ) -> bytes:
        """Remove the blinding factor to reveal the true signature.

        Args:
            blinded_signature: Signature returned by the server.
            blinding_factor: The *r* value used during blinding.
            e: Server's public exponent.
            n: Server's RSA modulus.

        Returns:
            Unblinded signature bytes.

        Raises:
            BlindSignatureError: If the blinding factor has no modular inverse.
        """
        from math import gcd
        sig_int = int.from_bytes(blinded_signature, byteorder="big")
        r = blinding_factor

        if gcd(r, n) != 1:
            raise BlindSignatureError("Blinding factor is not coprime with modulus.")

        r_inv = pow(r, -1, n)
        unblinded = (sig_int * r_inv) % n
        return unblinded.to_bytes((n.bit_length() + 7) // 8, byteorder="big")

    @staticmethod
    def verify(contact_hash: bytes, signature: bytes, e: int, n: int) -> bool:
        """Verify that a signature is valid for a given contact hash.

        Args:
            contact_hash: Original SHA-256 hash.
            signature: Unblinded signature bytes.
            e: Server's public exponent.
            n: Server's RSA modulus.

        Returns:
            True if the signature is valid.
        """
        h = BlindSignatureProtocol._hash_to_int(contact_hash)
        sig_int = int.from_bytes(signature, byteorder="big")
        recovered = pow(sig_int, e, n)
        return recovered == h


# ---------------------------------------------------------------------------
# Discovery Server
# ---------------------------------------------------------------------------

class DiscoveryServer:
    """Server-side component of the contact discovery protocol.

    Holds a set of registered contact tokens (unblinded signatures of
    contact hashes) and matches incoming client tokens against them.
    The server never sees plaintext contact information — it only signs
    blinded tokens and compares unblinded signatures.

    Rate limiting:
        Each user is limited to ``RATE_LIMIT_PER_HOUR`` discovery
        requests per hour.  The rate-limit state is held in memory.
    """

    def __init__(self, key_bits: int = RSA_KEY_BITS) -> None:
        self._key_bits = key_bits
        self._n: Optional[int] = None
        self._e: Optional[int] = None
        self._d: Optional[int] = None
        self._registered_tokens: dict[bytes, str] = {}  # unblinded_sig → user_id
        self._rate_limits: dict[str, list[float]] = {}  # user_id → [timestamps]
        self._initialized = False

    async def initialize(self) -> None:
        """Generate the RSA key pair (may take a moment for large keys).

        Runs in a thread executor to avoid blocking the event loop.
        """
        loop = asyncio.get_running_loop()
        n, e, d, _p, _q = await loop.run_in_executor(
            None, BlindSignatureProtocol.generate_keypair, self._key_bits
        )
        self._n = n
        self._e = e
        self._d = d
        self._initialized = True
        logger.info("DiscoveryServer initialized with %d-bit RSA key.", self._key_bits)

    @property
    def public_key(self) -> tuple[int, int]:
        """Return the server's public key as (e, n).

        Raises:
            ServerKeyError: If the server has not been initialized.
        """
        if not self._initialized or self._e is None or self._n is None:
            raise ServerKeyError("Server not initialized. Call initialize() first.")
        return (self._e, self._n)

    # ------------------------------------------------------------------
    # Token registration
    # ------------------------------------------------------------------

    def register_token(self, unblinded_signature: bytes, user_id: str) -> None:
        """Register a contact token for a user.

        The unblinded signature serves as a privacy-preserving identifier:
        two users who share the same contact will produce the same
        unblinded signature for that contact's hash.

        Args:
            unblinded_signature: The unblinded RSA signature on a contact hash.
            user_id: The user who owns this token.
        """
        self._registered_tokens[unblinded_signature] = user_id
        logger.debug("Token registered for user %s.", user_id[:8])

    def unregister_user(self, user_id: str) -> int:
        """Remove all tokens belonging to *user_id*.

        Args:
            user_id: User whose tokens should be removed.

        Returns:
            Number of tokens removed.
        """
        to_remove = [sig for sig, uid in self._registered_tokens.items() if uid == user_id]
        for sig in to_remove:
            del self._registered_tokens[sig]
        return len(to_remove)

    @property
    def registered_count(self) -> int:
        """Number of registered contact tokens."""
        return len(self._registered_tokens)

    # ------------------------------------------------------------------
    # Signing
    # ------------------------------------------------------------------

    def sign_tokens(self, blinded_tokens: list[bytes], user_id: str) -> list[bytes]:
        """Sign a batch of blinded tokens for a client.

        Args:
            blinded_tokens: List of blinded token bytes from the client.
            user_id: Identity of the requesting user (for rate limiting).

        Returns:
            List of signature bytes, one per input token.

        Raises:
            RateLimitExceededError: If the user has exceeded the rate limit.
            ServerKeyError: If the server is not initialized.
            TooManyContactsError: If too many tokens are submitted.
        """
        if not self._initialized or self._d is None or self._n is None:
            raise ServerKeyError("Server not initialized.")

        self._check_rate_limit(user_id)

        if len(blinded_tokens) > MAX_CONTACTS_PER_REQUEST:
            raise TooManyContactsError(
                f"Too many contacts ({len(blinded_tokens)}); "
                f"maximum is {MAX_CONTACTS_PER_REQUEST}."
            )

        signatures: list[bytes] = []
        for bt in blinded_tokens:
            sig = BlindSignatureProtocol.sign(bt, self._d, self._n)
            signatures.append(sig)

        self._record_request(user_id)
        logger.debug("Signed %d tokens for user %s.", len(signatures), user_id[:8])
        return signatures

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def find_matches(self, unblinded_signatures: list[bytes]) -> list[tuple[int, str]]:
        """Find which of the client's tokens match registered contacts.

        Args:
            unblinded_signatures: List of unblinded signatures from the client.

        Returns:
            List of (index, matched_user_id) tuples for each match found.
        """
        matches: list[tuple[int, str]] = []
        for idx, sig in enumerate(unblinded_signatures):
            if sig in self._registered_tokens:
                matches.append((idx, self._registered_tokens[sig]))

        logger.debug("Found %d matches out of %d tokens.", len(matches), len(unblinded_signatures))
        return matches

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def _check_rate_limit(self, user_id: str) -> None:
        """Raise if the user has exceeded the hourly rate limit.

        Args:
            user_id: User to check.

        Raises:
            RateLimitExceededError: If limit is exceeded.
        """
        now = time.time()
        timestamps = self._rate_limits.get(user_id, [])
        # Prune old timestamps
        timestamps = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW]
        if len(timestamps) >= RATE_LIMIT_PER_HOUR:
            raise RateLimitExceededError(
                f"User {user_id[:8]} exceeded {RATE_LIMIT_PER_HOUR} "
                f"discovery requests per hour."
            )
        self._rate_limits[user_id] = timestamps

    def _record_request(self, user_id: str) -> None:
        """Record a discovery request timestamp for rate limiting.

        Args:
            user_id: User making the request.
        """
        now = time.time()
        if user_id not in self._rate_limits:
            self._rate_limits[user_id] = []
        self._rate_limits[user_id].append(now)


# ---------------------------------------------------------------------------
# Discovery Client
# ---------------------------------------------------------------------------

class DiscoveryClient:
    """Client-side component of the contact discovery protocol.

    Hashes contact identifiers, blinds them, submits to the server,
    unblinds the returned signatures, and asks the server to match
    against its registered set.
    """

    def __init__(self, user_id: str) -> None:
        self._user_id = user_id
        self._server_e: Optional[int] = None
        self._server_n: Optional[int] = None

    def set_server_public_key(self, e: int, n: int) -> None:
        """Configure the server's public key for blinding operations.

        Args:
            e: Server's public exponent.
            n: Server's RSA modulus.
        """
        self._server_e = e
        self._server_n = n

    @staticmethod
    def hash_contact(contact: str) -> bytes:
        """Deterministically hash a contact identifier.

        Uses SHA-256 with a Presidium-specific domain separator to
        prevent cross-protocol attacks.

        Args:
            contact: Phone number, email, or username string.

        Returns:
            32-byte SHA-256 hash.
        """
        domain_separator = b"presidium-discovery-v2\x00"
        data = domain_separator + contact.encode("utf-8")
        return hashlib.sha256(data).digest()

    def create_tokens(self, contacts: list[str]) -> list[DiscoveryToken]:
        """Create blinded discovery tokens for a list of contacts.

        Args:
            contacts: List of contact identifier strings.

        Returns:
            List of DiscoveryToken with blinded_token and blinding_factor set.

        Raises:
            BlindSignatureError: If the server public key is not set.
        """
        if self._server_e is None or self._server_n is None:
            raise BlindSignatureError("Server public key not configured.")

        tokens: list[DiscoveryToken] = []
        for contact in contacts:
            contact_hash = self.hash_contact(contact)
            blinded_bytes, r = BlindSignatureProtocol.blind(
                contact_hash, self._server_e, self._server_n
            )
            tokens.append(DiscoveryToken(
                contact_hash=contact_hash,
                blinded_token=blinded_bytes,
                blinding_factor=r,
            ))

        logger.debug("Created %d blinded tokens for user %s.", len(tokens), self._user_id[:8])
        return tokens

    def unblind_tokens(self, tokens: list[DiscoveryToken], signatures: list[bytes]) -> list[DiscoveryToken]:
        """Unblind server signatures and complete the token lifecycle.

        Args:
            tokens: Tokens previously created by :meth:`create_tokens`.
            signatures: Corresponding signature bytes from the server.

        Returns:
            The same tokens with *signature* and *unblinded_sig* fields set.

        Raises:
            BlindSignatureError: If lengths mismatch or key is missing.
        """
        if self._server_e is None or self._server_n is None:
            raise BlindSignatureError("Server public key not configured.")

        if len(tokens) != len(signatures):
            raise BlindSignatureError(
                f"Token/signature count mismatch: {len(tokens)} vs {len(signatures)}."
            )

        for token, sig in zip(tokens, signatures):
            token.signature = sig
            token.unblinded_sig = BlindSignatureProtocol.unblind(
                sig, token.blinding_factor, self._server_e, self._server_n
            )

        return tokens

    @property
    def user_id(self) -> str:
        """The client's user identifier."""
        return self._user_id


# ---------------------------------------------------------------------------
# Contact Discovery V2 — main integration class
# ---------------------------------------------------------------------------

class ContactDiscoveryV2:
    """Full contact discovery system integrating client and server.

    For local / in-process testing both client and server are co-located.
    In production the server runs remotely and the client communicates
    over an encrypted channel.

    Lifecycle::

        cd = ContactDiscoveryV2(user_id="alice")
        await cd.initialize()

        # Register your own contacts on the server
        await cd.register_contacts(["+15551234567", "bob@example.com"])

        # Discover which of YOUR contacts are also Presidium users
        matches = await cd.discover_contacts(["+15551234567", "+15559876543"])

        await cd.shutdown()
    """

    def __init__(self, user_id: str, key_bits: int = RSA_KEY_BITS) -> None:
        self._user_id = user_id
        self._server = DiscoveryServer(key_bits)
        self._client = DiscoveryClient(user_id)
        self._initialized = False

    async def initialize(self) -> None:
        """Generate RSA keys and configure the client with the server's public key.

        Must be called before any discovery or registration.
        """
        await self._server.initialize()
        e, n = self._server.public_key
        self._client.set_server_public_key(e, n)
        self._initialized = True
        logger.info("ContactDiscoveryV2 initialized for user %s.", self._user_id[:8])

    async def register_contacts(self, contacts: list[str]) -> int:
        """Register this user's contacts on the discovery server.

        Hashes each contact, blinds it, gets the server to sign the
        blinded token, unblinds, and stores the result as a registered
        token on the server.

        Args:
            contacts: List of contact identifier strings.

        Returns:
            Number of contacts successfully registered.

        Raises:
            DiscoveryV2Error: If not initialized.
        """
        if not self._initialized:
            raise DiscoveryV2Error("Not initialized. Call initialize() first.")

        tokens = self._client.create_tokens(contacts)
        blinded = [t.blinded_token for t in tokens]
        signatures = self._server.sign_tokens(blinded, self._user_id)
        completed = self._client.unblind_tokens(tokens, signatures)

        for token in completed:
            self._server.register_token(token.unblinded_sig, self._user_id)

        logger.info("Registered %d contacts for user %s.", len(completed), self._user_id[:8])
        return len(completed)

    async def discover_contacts(self, contacts: list[str]) -> list[str]:
        """Discover which contacts are also registered Presidium users.

        Full protocol round-trip:
            1. Hash and blind each contact
            2. Server signs the blinded tokens
            3. Client unblinds the signatures
            4. Server matches unblinded signatures against registered set

        Args:
            contacts: List of contact identifier strings to check.

        Returns:
            List of matched contact strings that are Presidium users.

        Raises:
            DiscoveryV2Error: If not initialized.
            RateLimitExceededError: If rate limit is exceeded.
        """
        if not self._initialized:
            raise DiscoveryV2Error("Not initialized. Call initialize() first.")

        # Step 1: Create blinded tokens
        tokens = self._client.create_tokens(contacts)

        # Step 2: Server signs
        blinded = [t.blinded_token for t in tokens]
        signatures = self._server.sign_tokens(blinded, self._user_id)

        # Step 3: Client unblinds
        completed = self._client.unblind_tokens(tokens, signatures)

        # Step 4: Server matches
        unblinded_sigs = [t.unblinded_sig for t in completed]
        match_indices = self._server.find_matches(unblinded_sigs)

        # Map indices back to original contact strings
        matched_contacts = [contacts[idx] for idx, _uid in match_indices]

        logger.info(
            "Discovery for %s: %d matches out of %d contacts.",
            self._user_id[:8], len(matched_contacts), len(contacts),
        )
        return matched_contacts

    async def discover_tokens(self, contacts: list[str]) -> list[DiscoveryToken]:
        """Run the discovery protocol and return the completed tokens.

        Unlike :meth:`discover_contacts`, this returns the full token
        objects (including cryptographic material) for inspection or
        further processing.

        Args:
            contacts: Contact identifiers to discover.

        Returns:
            List of completed DiscoveryToken objects.
        """
        if not self._initialized:
            raise DiscoveryV2Error("Not initialized. Call initialize() first.")

        tokens = self._client.create_tokens(contacts)
        blinded = [t.blinded_token for t in tokens]
        signatures = self._server.sign_tokens(blinded, self._user_id)
        completed = self._client.unblind_tokens(tokens, signatures)
        return completed

    async def shutdown(self) -> None:
        """Clean up server state (rate-limit tables, keys)."""
        self._server._registered_tokens.clear()
        self._server._rate_limits.clear()
        self._initialized = False
        logger.info("ContactDiscoveryV2 shut down for user %s.", self._user_id[:8])

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def server(self) -> DiscoveryServer:
        """Access the embedded discovery server."""
        return self._server

    @property
    def client(self) -> DiscoveryClient:
        """Access the embedded discovery client."""
        return self._client

    @property
    def registered_count(self) -> int:
        """Number of tokens registered on the server."""
        return self._server.registered_count
