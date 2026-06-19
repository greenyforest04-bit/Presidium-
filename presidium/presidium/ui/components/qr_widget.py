"""QR code widget for Presidium contact sharing.

Generates and displays QR codes containing the Presidium contact URI:
    presidium://<user_id>:<base64url_ed25519_pubkey>:<base64url_x25519_prekey>

Also supports decoding QR codes from image files (drag-and-drop).
Uses the `qrcode` library for generation and `Pillow` for image handling.
"""

from __future__ import annotations

import base64
import io
import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal, QByteArray
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QFileDialog,
    QSizePolicy,
)

from presidium.ui.components.glass_panel import GlassPanel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Contact URI encoding/decoding
# ---------------------------------------------------------------------------

def encode_contact_uri(user_id: str, ed25519_pubkey: bytes, x25519_prekey: bytes = b"") -> str:
    """Encode a contact URI for QR code sharing.

    Format: presidium://<user_id>:<base64url_ed25519>:<base64url_x25519>

    Args:
        user_id: 32-char hex user ID.
        ed25519_pubkey: 32-byte Ed25519 public key.
        x25519_prekey: Optional 32-byte X25519 prekey.

    Returns:
        Contact URI string.
    """
    pk_b64 = base64.urlsafe_b64encode(ed25519_pubkey).decode("ascii").rstrip("=")
    parts = [user_id, pk_b64]
    if x25519_prekey:
        prekey_b64 = base64.urlsafe_b64encode(x25519_prekey).decode("ascii").rstrip("=")
        parts.append(prekey_b64)
    return "presidium://" + ":".join(parts)


def decode_contact_uri(uri: str) -> tuple[str, bytes, bytes]:
    """Decode a contact URI.

    Args:
        uri: Contact URI string.

    Returns:
        Tuple of (user_id, ed25519_pubkey_bytes, x25519_prekey_bytes).
        x25519_prekey will be b"" if not present.

    Raises:
        ValueError: If the URI is malformed.
    """
    if not uri.startswith("presidium://"):
        raise ValueError(f"Invalid contact URI: must start with 'presidium://'")

    payload = uri[len("presidium://"):]
    parts = payload.split(":")

    if len(parts) < 2:
        raise ValueError("Invalid contact URI: missing fields.")

    user_id = parts[0]

    # Validate user_id
    if len(user_id) != 32 or not all(c in "0123456789abcdef" for c in user_id):
        raise ValueError(f"Invalid User ID in URI: {user_id!r}")

    # Decode Ed25519 public key
    pk_b64 = parts[1]
    # Re-add padding
    pk_b64 += "=" * (4 - len(pk_b64) % 4) if len(pk_b64) % 4 else ""
    try:
        ed25519_pubkey = base64.urlsafe_b64decode(pk_b64)
    except Exception as exc:
        raise ValueError(f"Invalid Ed25519 public key in URI: {exc}") from exc

    if len(ed25519_pubkey) != 32:
        raise ValueError(f"Ed25519 public key must be 32 bytes, got {len(ed25519_pubkey)}")

    # Decode optional X25519 prekey
    x25519_prekey = b""
    if len(parts) >= 3:
        prekey_b64 = parts[2]
        prekey_b64 += "=" * (4 - len(prekey_b64) % 4) if len(prekey_b64) % 4 else ""
        try:
            x25519_prekey = base64.urlsafe_b64decode(prekey_b64)
        except Exception as exc:
            raise ValueError(f"Invalid X25519 prekey in URI: {exc}") from exc
        if len(x25519_prekey) != 32:
            raise ValueError(f"X25519 prekey must be 32 bytes, got {len(x25519_prekey)}")

    return user_id, ed25519_pubkey, x25519_prekey


# ---------------------------------------------------------------------------
# QR code image generation
# ---------------------------------------------------------------------------

def generate_qr_pixmap(data: str, size: int = 256, error_correction: str = "M") -> QPixmap:
    """Generate a QR code as a QPixmap.

    Args:
        data: The string data to encode.
        size: Output image size in pixels.
        error_correction: Error correction level (L, M, Q, H).

    Returns:
        QPixmap containing the QR code image.

    Raises:
        ImportError: If the `qrcode` library is not installed.
    """
    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_L, ERROR_CORRECT_M, ERROR_CORRECT_Q, ERROR_CORRECT_H
    except ImportError:
        raise ImportError(
            "The 'qrcode' library is required for QR code generation. "
            "Install with: pip install qrcode[pil]"
        )

    ec_map = {
        "L": ERROR_CORRECT_L,
        "M": ERROR_CORRECT_M,
        "Q": ERROR_CORRECT_Q,
        "H": ERROR_CORRECT_H,
    }

    qr = qrcode.QRCode(
        version=None,  # Auto-detect
        error_correction=ec_map.get(error_correction, ERROR_CORRECT_M),
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)

    img = qr.make_image(fill_color="#1A1A2E", back_color="#FFFFFF")

    # Convert PIL Image to QPixmap
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    qimage = QImage()
    qimage.loadFromData(QByteArray(buf.read()), "PNG")

    # Scale to desired size
    if qimage.width() != size or qimage.height() != size:
        qimage = qimage.scaled(
            size, size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    return QPixmap.fromImage(qimage)


def decode_qr_from_image(image_path: str) -> Optional[str]:
    """Decode a QR code from an image file.

    Args:
        image_path: Path to a PNG/JPG image containing a QR code.

    Returns:
        The decoded string, or None if no QR code is found.

    Raises:
        ImportError: If required libraries are not installed.
    """
    try:
        from pyzbar.pyzbar import decode as pyzbar_decode
        from PIL import Image
    except ImportError:
        raise ImportError(
            "QR code decoding requires 'pyzbar' and 'Pillow'. "
            "Install with: pip install pyzbar Pillow"
        )

    try:
        img = Image.open(image_path)
        decoded = pyzbar_decode(img)
        if decoded:
            return decoded[0].data.decode("utf-8")
        return None
    except Exception as exc:
        logger.error("Failed to decode QR from image: %s", exc)
        return None


# ---------------------------------------------------------------------------
# QR Code Display Widget
# ---------------------------------------------------------------------------

class QRCodeWidget(GlassPanel):
    """Widget that displays a QR code for contact sharing.

    Also provides:
    - Copy-to-clipboard button
    - Manual URI input field
    - Load-from-file button for decoding received QR codes

    Signals:
        contact_decoded(str, bytes, bytes): Emitted when a QR is decoded:
            (user_id, ed25519_pubkey, x25519_prekey)
    """

    contact_decoded = Signal(str, bytes, bytes)

    def __init__(
        self,
        user_id: str = "",
        ed25519_pubkey: bytes = b"",
        x25519_prekey: bytes = b"",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._user_id = user_id
        self._ed25519_pubkey = ed25519_pubkey
        self._x25519_prekey = x25519_prekey
        self._qr_pixmap: Optional[QPixmap] = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Build the QR code display layout."""
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 24, 24, 24)

        title = QLabel("Share Contact")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # QR code image
        self._qr_label = QLabel()
        self._qr_label.setFixedSize(256, 256)
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setStyleSheet("background-color: white; border-radius: 8px;")
        layout.addWidget(self._qr_label, alignment=Qt.AlignmentFlag.AlignCenter)

        # URI display
        self._uri_label = QLabel()
        self._uri_label.setObjectName("caption")
        self._uri_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._uri_label.setWordWrap(True)
        self._uri_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._uri_label)

        # Buttons row
        btn_layout = QHBoxLayout()

        copy_btn = QPushButton("Copy URI")
        copy_btn.setObjectName("secondary-button")
        copy_btn.clicked.connect(self._copy_uri)
        btn_layout.addWidget(copy_btn)

        decode_btn = QPushButton("Load QR Image")
        decode_btn.setObjectName("secondary-button")
        decode_btn.clicked.connect(self._load_qr_image)
        btn_layout.addWidget(decode_btn)

        layout.addLayout(btn_layout)

        # Manual URI input
        input_label = QLabel("Or paste a contact URI:")
        input_label.setObjectName("caption")
        layout.addWidget(input_label)

        input_layout = QHBoxLayout()
        self._uri_input = QLineEdit()
        self._uri_input.setPlaceholderText("presidium://abc123...")
        self._uri_input.setMinimumHeight(36)
        input_layout.addWidget(self._uri_input, 1)

        add_btn = QPushButton("Add Contact")
        add_btn.clicked.connect(self._decode_manual_uri)
        input_layout.addWidget(add_btn)

        layout.addLayout(input_layout)

        # Generate QR if data is available
        if self._user_id and self._ed25519_pubkey:
            self._generate_qr()

    def set_contact_data(
        self,
        user_id: str,
        ed25519_pubkey: bytes,
        x25519_prekey: bytes = b"",
    ) -> None:
        """Update the contact data and regenerate QR code."""
        self._user_id = user_id
        self._ed25519_pubkey = ed25519_pubkey
        self._x25519_prekey = x25519_prekey
        self._generate_qr()

    def _generate_qr(self) -> None:
        """Generate and display the QR code."""
        uri = encode_contact_uri(
            self._user_id,
            self._ed25519_pubkey,
            self._x25519_prekey,
        )
        self._uri_label.setText(uri)

        try:
            pixmap = generate_qr_pixmap(uri, size=240)
            self._qr_label.setPixmap(pixmap)
        except ImportError:
            self._qr_label.setText("Install qrcode\nlibrary to\ngenerate QR")
            self._qr_label.setStyleSheet(
                "background-color: #242436; color: #8888A8; "
                "border-radius: 8px; font-size: 14px;"
            )
        except Exception as exc:
            self._qr_label.setText(f"QR Error:\n{exc}")
            logger.error("QR generation failed: %s", exc)

    def _copy_uri(self) -> None:
        """Copy the contact URI to clipboard."""
        uri = encode_contact_uri(
            self._user_id,
            self._ed25519_pubkey,
            self._x25519_prekey,
        )
        from PySide6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(uri)

    def _load_qr_image(self) -> None:
        """Open a file dialog to load and decode a QR image."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select QR Code Image",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif);;All Files (*)",
        )
        if not file_path:
            return

        try:
            decoded = decode_qr_from_image(file_path)
            if decoded and decoded.startswith("presidium://"):
                user_id, ed25519_pk, x25519_pk = decode_contact_uri(decoded)
                self.contact_decoded.emit(user_id, ed25519_pk, x25519_pk)
            elif decoded:
                # Not a Presidium URI
                self._uri_input.setText(decoded)
            else:
                logger.warning("No QR code found in image: %s", file_path)
        except ImportError:
            logger.error("QR decoding requires pyzbar library")
        except ValueError as exc:
            logger.error("Invalid contact URI in QR code: %s", exc)

    def _decode_manual_uri(self) -> None:
        """Decode a manually entered contact URI."""
        uri = self._uri_input.text().strip()
        if not uri:
            return

        try:
            user_id, ed25519_pk, x25519_pk = decode_contact_uri(uri)
            self.contact_decoded.emit(user_id, ed25519_pk, x25519_pk)
        except ValueError as exc:
            self._uri_input.setStyleSheet("border-color: #F87171;")
            logger.warning("Invalid contact URI: %s", exc)
