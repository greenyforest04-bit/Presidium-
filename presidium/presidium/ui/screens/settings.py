"""Settings screen for Presidium.

Provides:
- User profile display (User ID, public key, QR data)
- Theme toggle (dark/light)
- Network settings (bootstrap nodes, ports)
- Security settings (auto-lock, key export)
- About section
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QClipboard
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QComboBox,
    QSpinBox,
    QCheckBox,
    QGroupBox,
    QFormLayout,
    QApplication,
    QSizePolicy,
)

from presidium.ui.components.identicon import generate_identicon_pixmap


class SettingsScreen(QWidget):
    """Application settings screen.

    Signals:
        theme_changed(str): Emitted with "dark" or "light" when theme is toggled.
        logout_requested(): Emitted when the user wants to lock/logout.
    """

    theme_changed = Signal(str)
    logout_requested = Signal()

    def __init__(
        self,
        user_id: str = "",
        public_key_hex: str = "",
        public_key_bytes: bytes = b"",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._user_id = user_id
        self._public_key_hex = public_key_hex
        self._public_key_bytes = public_key_bytes
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Build the settings layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)

        # ── Title ──
        title = QLabel("Settings")
        title.setObjectName("title")
        title_font = QFont()
        title_font.setPointSize(22)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        # ── Profile Section ──
        profile_group = QGroupBox("Profile")
        profile_layout = QVBoxLayout(profile_group)

        # Avatar
        avatar_row = QHBoxLayout()
        self._avatar_label = QLabel()
        self._avatar_label.setFixedSize(64, 64)
        self._avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if self._public_key_bytes:
            try:
                pixmap = generate_identicon_pixmap(self._public_key_bytes, 64)
                self._avatar_label.setPixmap(pixmap)
            except Exception:
                self._avatar_label.setText("\U0001f464")
        else:
            self._avatar_label.setText("\U0001f464")
        avatar_row.addWidget(self._avatar_label)

        profile_info = QVBoxLayout()
        profile_info.setSpacing(4)

        uid_title = QLabel("User ID:")
        uid_title.setObjectName("caption")
        profile_info.addWidget(uid_title)

        self._uid_display = QLabel(self._user_id)
        uid_font = QFont()
        uid_font.setFamily("monospace")
        uid_font.setPointSize(11)
        self._uid_display.setFont(uid_font)
        self._uid_display.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        profile_info.addWidget(self._uid_display)

        copy_uid_btn = QPushButton("Copy User ID")
        copy_uid_btn.setObjectName("secondary-button")
        copy_uid_btn.clicked.connect(self._copy_user_id)
        profile_info.addWidget(copy_uid_btn)

        avatar_row.addLayout(profile_info, 1)
        profile_layout.addLayout(avatar_row)

        # QR data
        qr_label = QLabel("Share String:")
        qr_label.setObjectName("caption")
        profile_layout.addWidget(qr_label)

        self._qr_display = QLabel()
        self._qr_display.setWordWrap(True)
        self._qr_display.setFont(QFont("monospace", 10))
        self._qr_display.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        qr_text = f"presidium://{self._user_id}:{self._public_key_hex}" if self._public_key_hex else ""
        self._qr_display.setText(qr_text)
        profile_layout.addWidget(self._qr_display)

        copy_qr_btn = QPushButton("Copy Share String")
        copy_qr_btn.setObjectName("secondary-button")
        copy_qr_btn.clicked.connect(self._copy_qr_data)
        profile_layout.addWidget(copy_qr_btn)

        layout.addWidget(profile_group)

        # ── Appearance Section ──
        appearance_group = QGroupBox("Appearance")
        appearance_layout = QFormLayout(appearance_group)

        self._theme_combo = QComboBox()
        self._theme_combo.addItems(["Dark", "Light"])
        self._theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        appearance_layout.addRow("Theme:", self._theme_combo)

        layout.addWidget(appearance_group)

        # ── Network Section ──
        network_group = QGroupBox("Network")
        network_layout = QFormLayout(network_group)

        self._listen_port = QSpinBox()
        self._listen_port.setRange(1024, 65535)
        self._listen_port.setValue(9478)
        network_layout.addRow("Listen Port:", self._listen_port)

        self._mdns_check = QCheckBox("Enable mDNS (LAN discovery)")
        self._mdns_check.setChecked(True)
        network_layout.addRow(self._mdns_check)

        self._dht_check = QCheckBox("Enable DHT (global discovery)")
        self._dht_check.setChecked(True)
        network_layout.addRow(self._dht_check)

        layout.addWidget(network_group)

        # ── Security Section ──
        security_group = QGroupBox("Security")
        security_layout = QFormLayout(security_group)

        self._auto_lock = QCheckBox("Auto-lock after inactivity")
        self._auto_lock.setChecked(True)
        security_layout.addRow(self._auto_lock)

        self._lock_timeout = QSpinBox()
        self._lock_timeout.setRange(1, 60)
        self._lock_timeout.setValue(5)
        self._lock_timeout.setSuffix(" min")
        security_layout.addRow("Lock timeout:", self._lock_timeout)

        layout.addWidget(security_group)

        # ── Danger Zone ──
        danger_group = QGroupBox("Account")
        danger_layout = QVBoxLayout(danger_group)

        lock_btn = QPushButton("Lock Application")
        lock_btn.setObjectName("secondary-button")
        lock_btn.clicked.connect(self.logout_requested.emit)
        danger_layout.addWidget(lock_btn)

        warning = QLabel(
            "Locking will require your password to resume. "
            "Your private key remains encrypted on disk."
        )
        warning.setObjectName("caption")
        warning.setWordWrap(True)
        danger_layout.addWidget(warning)

        layout.addWidget(danger_group)

        layout.addStretch()

        # ── About ──
        about_label = QLabel("Presidium v0.1.0 — Decentralized E2EE Messenger")
        about_label.setObjectName("caption")
        about_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(about_label)

    # ── Helpers ──

    def set_user_info(self, user_id: str, public_key_bytes: bytes) -> None:
        """Update user profile information."""
        self._user_id = user_id
        self._public_key_bytes = public_key_bytes
        self._uid_display.setText(user_id)

        if public_key_bytes:
            import base64
            self._public_key_hex = base64.b64encode(public_key_bytes).decode("ascii")
            qr_text = f"presidium://{user_id}:{self._public_key_hex}"
            self._qr_display.setText(qr_text)

            try:
                pixmap = generate_identicon_pixmap(public_key_bytes, 64)
                self._avatar_label.setPixmap(pixmap)
            except Exception:
                pass

    def _copy_user_id(self) -> None:
        """Copy User ID to clipboard."""
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(self._user_id)

    def _copy_qr_data(self) -> None:
        """Copy share string to clipboard."""
        clipboard = QApplication.clipboard()
        if clipboard:
            import base64
            pk_b64 = base64.b64encode(self._public_key_bytes).decode("ascii") if self._public_key_bytes else ""
            clipboard.setText(f"presidium://{self._user_id}:{pk_b64}")

    def _on_theme_changed(self, index: int) -> None:
        """Handle theme selection change."""
        theme = "dark" if index == 0 else "light"
        self.theme_changed.emit(theme)
