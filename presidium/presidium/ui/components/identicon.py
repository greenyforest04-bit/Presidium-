"""Identicon avatar generator for Presidium.

Generates unique, deterministic identicon avatars from Ed25519 public keys
using Pillow. Each avatar is a 5x5 symmetric grid of colored blocks, providing
visual uniqueness without storing actual images.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
from typing import Optional

from PIL import Image, ImageDraw


# ---------------------------------------------------------------------------
# Color generation from public key
# ---------------------------------------------------------------------------

def _generate_color(data: bytes) -> tuple[int, int, int]:
    """Generate an HSL-based color from key bytes.

    Uses the first 10 bytes of the key to produce a visually distinct
    hue with moderate saturation and lightness for good appearance on
    both dark and light backgrounds.
    """
    h = int.from_bytes(data[0:2], "big") % 360
    s = 55 + (int.from_bytes(data[2:4], "big") % 30)  # 55-85%
    l = 50 + (int.from_bytes(data[4:6], "big") % 20)  # 50-70%

    # Convert HSL to RGB
    c = (1 - abs(2 * l / 100 - 1)) * s / 100
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = l / 100 - c / 2

    if h < 60:
        r, g, b = c, x, 0
    elif h < 120:
        r, g, b = x, c, 0
    elif h < 180:
        r, g, b = 0, c, x
    elif h < 240:
        r, g, b = 0, x, c
    elif h < 300:
        r, g, b = x, 0, c
    else:
        r, g, b = c, 0, x

    return int((r + m) * 255), int((g + m) * 255), int((b + m) * 255)


def _generate_bg_color(data: bytes) -> tuple[int, int, int]:
    """Generate a lighter background color for the identicon."""
    r, g, b = _generate_color(data)
    # Lighten for background
    return min(r + 40, 255), min(g + 40, 255), min(b + 40, 255)


# ---------------------------------------------------------------------------
# Identicon generator
# ---------------------------------------------------------------------------

def generate_identicon(
    public_key_bytes: bytes,
    size: int = 128,
    grid_size: int = 5,
    padding: int = 2,
) -> bytes:
    """Generate a PNG identicon from an Ed25519 public key.

    The identicon is a symmetric 5x5 grid pattern where the left half
    is mirrored to create visual symmetry. The color is deterministically
    derived from the public key bytes.

    Args:
        public_key_bytes: 32-byte Ed25519 public key.
        size: Output image size in pixels (square).
        grid_size: Number of cells per row/column.
        padding: Cells of padding around the edge.

    Returns:
        PNG image data as bytes.
    """
    # Hash the key for extra randomness in the pattern
    key_hash = hashlib.sha256(public_key_bytes).digest()

    # Generate colors
    fg_color = _generate_color(key_hash[:10])
    bg_color = _generate_bg_color(key_hash[10:20])

    # Create image
    img = Image.new("RGB", (size, size), bg_color)
    draw = ImageDraw.Draw(img)

    cell_size = size // (grid_size + 2 * padding)
    offset = padding * cell_size

    # Determine which cells are filled
    # Use bits from the hash to fill the grid (left half only, then mirror)
    half_width = (grid_size + 1) // 2  # 3 for grid_size=5
    bit_index = 0

    for row in range(grid_size):
        for col in range(half_width):
            # Get bit from hash
            byte_idx = bit_index // 8
            bit_idx = bit_index % 8
            if byte_idx < len(key_hash):
                filled = bool(key_hash[byte_idx] & (1 << bit_idx))
            else:
                filled = False
            bit_index += 1

            if filled:
                x0 = offset + col * cell_size
                y0 = offset + row * cell_size
                x1 = x0 + cell_size - 1
                y1 = y0 + cell_size - 1
                draw.rectangle([x0, y0, x1, y1], fill=fg_color)

                # Mirror
                mirror_col = grid_size - 1 - col
                if mirror_col != col:
                    mx0 = offset + mirror_col * cell_size
                    mx1 = mx0 + cell_size - 1
                    draw.rectangle([mx0, y0, mx1, y1], fill=fg_color)

    # Add rounded corners by drawing a mask
    # SIMPLIFIED: Pillow rectangle doesn't support border-radius natively.
    # For production, use a rounded-rect mask.

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def generate_identicon_pixmap(public_key_bytes: bytes, size: int = 128):
    """Generate a QPixmap identicon for direct use in Qt widgets.

    Args:
        public_key_bytes: 32-byte Ed25519 public key.
        size: Output image size in pixels.

    Returns:
        QPixmap ready for display.
    """
    from PySide6.QtGui import QPixmap
    from PySide6.QtCore import QByteArray

    png_data = generate_identicon(public_key_bytes, size)
    pixmap = QPixmap()
    pixmap.loadFromData(QByteArray(png_data), "PNG")
    return pixmap
