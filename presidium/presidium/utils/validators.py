"""Validation utilities for Presidium."""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse


class ValidationError(ValueError):
    """Custom validation error for Presidium input validation."""


# User ID = first 16 bytes of SHA-256(public_key) → hex string of 32 chars
USER_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
PUBLIC_KEY_B64_PATTERN = re.compile(r"^[A-Za-z0-9+/]{43}=$")  # Ed25519 key in b64


def validate_user_id(user_id: str) -> str:
    """Validate a Presidium User ID (32 hex chars)."""
    if not USER_ID_PATTERN.match(user_id):
        raise ValidationError(
            f"Invalid User ID: expected 32 hex characters, got {user_id!r}"
        )
    return user_id


def validate_public_key_b64(key_b64: str) -> str:
    """Validate a base64-encoded Ed25519 public key."""
    if not PUBLIC_KEY_B64_PATTERN.match(key_b64):
        raise ValidationError(
            f"Invalid Ed25519 public key (base64): expected 44 chars ending with '=', got {key_b64!r}"
        )
    return key_b64


def validate_host_port(address: str) -> tuple[str, int]:
    """Validate a host:port string and return (host, port) tuple."""
    if ":" not in address:
        raise ValidationError(f"Invalid address format (expected host:port): {address!r}")
    host, port_str = address.rsplit(":", 1)
    if not host:
        raise ValidationError(f"Empty host in address: {address!r}")
    try:
        port = int(port_str)
    except ValueError:
        raise ValidationError(f"Invalid port in address: {address!r}")
    if not (1 <= port <= 65535):
        raise ValidationError(f"Port out of range 1-65535: {port}")
    return host, port


def validate_message_text(text: str, max_length: int = 32768) -> str:
    """Validate message text content."""
    if not text:
        raise ValidationError("Message text cannot be empty.")
    if len(text.encode("utf-8")) > max_length:
        raise ValidationError(
            f"Message too long: {len(text.encode('utf-8'))} bytes, max {max_length}."
        )
    return text


def validate_file_path_for_transfer(path: str) -> str:
    """Validate a file path string for P2P transfer (sanitization)."""
    # Reject obviously malicious paths
    if ".." in path:
        raise ValidationError(f"Path traversal detected in: {path!r}")
    if "\x00" in path:
        raise ValidationError(f"Null byte in path: {path!r}")
    return path


def sanitize_nickname(name: str, max_length: int = 64) -> str:
    """Sanitize a user-provided nickname."""
    stripped = name.strip()
    if not stripped:
        raise ValidationError("Nickname cannot be empty or whitespace.")
    if len(stripped) > max_length:
        stripped = stripped[:max_length]
    # Remove control characters
    cleaned = "".join(ch for ch in stripped if ord(ch) >= 32)
    if not cleaned:
        raise ValidationError("Nickname contains only control characters.")
    return cleaned
