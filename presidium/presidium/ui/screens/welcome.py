"""Welcome / onboarding screen for Presidium.

Provides:
- First-run experience with key generation
- Password setup for encrypting the identity
- Optional nickname entry
- Visual key generation animation (skeleton loading)
- Display of User ID and QR code for sharing
"""

from __future__ import annotations

import asyncio

from PySide6.QtCore import Qt, Signal, QThread, Slot
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QProgressBar,
    QStackedWidget,
    QSizePolicy,
)

from presidium.ui.components.glass_panel import GlassPanel
from presidium.ui.components.identicon import generate_identicon_pixmap


# ---------------------------------------------------------------------------
# Key generation worker (runs in thread to avoid blocking UI)
# ---------------------------------------------------------------------------

class KeyGenWorker(QThread):
    """Background thread for key pair generation."""

    finished = Signal(str)  # user_id
    error = Signal(str)

    def __init__(self, identity_manager, password: str, nickname: str) -> None:
        super().__init__()
        self._identity_manager = identity_manager
        self._password = password
        self._nickname = nickname

    def run(self) -> None:
        """Generate identity in background thread."""
        loop = asyncio.new_event_loop()
        try:
            user_id = loop.run_until_complete(
                self._identity_manager.create_identity(
                    self._password,
                    self._nickname or None,
                )
            )
            self.finished.emit(user_id)
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# Welcome Screen
# ---------------------------------------------------------------------------

class WelcomeScreen(QWidget):
    """Onboarding screen for first-time users.

    Flow:
    1. Welcome message + "Create Identity" button
    2. Password + nickname input form
    3. Key generation progress (skeleton animation)
    4. Success: display User ID + identicon + QR code data

    Signals:
        identity_created(str): Emitted with user_id when identity is created.
    """

    identity_created = Signal(str)

    def __init__(
        self,
        identity_manager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._identity_manager = identity_manager
        self._worker: KeyGenWorker | None = None

        self._stack = QStackedWidget(self)
        self._setup_welcome_page()
        self._setup_form_page()
        self._setup_progress_page()
        self._setup_success_page()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)

    # ── Page 1: Welcome ──

    def _setup_welcome_page(self) -> None:
        """Create the initial welcome page."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(24)

        # Logo / Brand
        logo = QLabel("P")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setFixedSize(80, 80)
        logo_font = QFont()
        logo_font.setPointSize(40)
        logo_font.setBold(True)
        logo.setFont(logo_font)
        logo.setStyleSheet("color: #6366F1;")
        layout.addWidget(logo)

        title = QLabel("Welcome to Presidium")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel(
            "A decentralized, end-to-end encrypted messenger.\n"
            "No servers. No tracking. Your keys, your data."
        )
        subtitle.setObjectName("subtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)

        layout.addSpacing(32)

        create_btn = QPushButton("Create New Identity")
        create_btn.setMinimumWidth(240)
        create_btn.setMinimumHeight(48)
        create_btn.clicked.connect(lambda: self._stack.setCurrentIndex(1))
        layout.addWidget(create_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        info = QLabel(
            "Your identity is a cryptographic key pair. "
            "There are no email addresses or phone numbers — "
            "just math and your password."
        )
        info.setObjectName("caption")
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info.setWordWrap(True)
        info.setMaximumWidth(400)
        layout.addWidget(info)

        self._stack.addWidget(page)

    # ── Page 2: Form ──

    def _setup_form_page(self) -> None:
        """Create the identity creation form page."""
        panel = GlassPanel(self)
        layout = QVBoxLayout(panel)
        layout.setSpacing(16)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("Set Up Your Identity")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        layout.addSpacing(16)

        # Nickname
        nick_label = QLabel("Nickname (optional)")
        nick_label.setObjectName("subtitle")
        layout.addWidget(nick_label)
        self._nickname_input = QLineEdit()
        self._nickname_input.setPlaceholderText("How others will see you")
        self._nickname_input.setMinimumHeight(40)
        layout.addWidget(self._nickname_input)

        layout.addSpacing(8)

        # Password
        pass_label = QLabel("Password (min 6 characters)")
        pass_label.setObjectName("subtitle")
        layout.addWidget(pass_label)
        self._password_input = QLineEdit()
        self._password_input.setPlaceholderText("Choose a strong password")
        self._password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._password_input.setMinimumHeight(40)
        layout.addWidget(self._password_input)

        # Confirm password
        confirm_label = QLabel("Confirm Password")
        confirm_label.setObjectName("subtitle")
        layout.addWidget(confirm_label)
        self._confirm_input = QLineEdit()
        self._confirm_input.setPlaceholderText("Re-enter your password")
        self._confirm_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._confirm_input.setMinimumHeight(40)
        layout.addWidget(self._confirm_input)

        self._error_label = QLabel("")
        self._error_label.setStyleSheet("color: #F87171;")
        self._error_label.setWordWrap(True)
        self._error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._error_label)

        layout.addSpacing(16)

        btn_layout = QHBoxLayout()
        back_btn = QPushButton("Back")
        back_btn.setObjectName("secondary-button")
        back_btn.clicked.connect(lambda: self._stack.setCurrentIndex(0))
        btn_layout.addWidget(back_btn)

        create_btn = QPushButton("Generate Keys")
        create_btn.clicked.connect(self._on_create)
        btn_layout.addWidget(create_btn)
        layout.addLayout(btn_layout)

        self._stack.addWidget(panel)

    # ── Page 3: Progress ──

    def _setup_progress_page(self) -> None:
        """Create the key generation progress page."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(16)

        title = QLabel("Generating Your Keys...")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel(
            "Creating Ed25519 signing key and encrypting with Argon2id.\n"
            "This may take a few seconds."
        )
        subtitle.setObjectName("subtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)

        self._progress = QProgressBar()
        self._progress.setMinimum(0)
        self._progress.setMaximum(100)
        self._progress.setValue(0)
        self._progress.setMinimumWidth(300)
        layout.addWidget(self._progress, alignment=Qt.AlignmentFlag.AlignCenter)

        # Simulate progress animation
        self._progress_timer = None
        self._progress_value = 0

        self._stack.addWidget(page)

    # ── Page 4: Success ──

    def _setup_success_page(self) -> None:
        """Create the success page with User ID and identicon."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(16)

        title = QLabel("Identity Created!")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Identicon avatar
        self._identicon_label = QLabel()
        self._identicon_label.setFixedSize(96, 96)
        self._identicon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._identicon_label, alignment=Qt.AlignmentFlag.AlignCenter)

        # User ID
        uid_label = QLabel("Your User ID:")
        uid_label.setObjectName("subtitle")
        uid_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(uid_label)

        self._user_id_display = QLabel()
        self._user_id_display.setObjectName("title")
        self._user_id_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        uid_font = QFont()
        uid_font.setPointSize(11)
        uid_font.setFamily("monospace")
        self._user_id_display.setFont(uid_font)
        layout.addWidget(self._user_id_display)

        info = QLabel(
            "Share your User ID with contacts to start chatting.\n"
            "You can also share via QR code from the settings."
        )
        info.setObjectName("caption")
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info.setWordWrap(True)
        layout.addWidget(info)

        layout.addSpacing(24)

        continue_btn = QPushButton("Start Messaging")
        continue_btn.setMinimumWidth(240)
        continue_btn.setMinimumHeight(48)
        continue_btn.clicked.connect(self._on_continue)
        layout.addWidget(continue_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._stack.addWidget(page)

    # ── Event handlers ──

    def _on_create(self) -> None:
        """Validate form and start key generation."""
        password = self._password_input.text()
        confirm = self._confirm_input.text()
        nickname = self._nickname_input.text()

        if len(password) < 6:
            self._error_label.setText("Password must be at least 6 characters.")
            return

        if password != confirm:
            self._error_label.setText("Passwords do not match.")
            return

        self._error_label.setText("")

        # Switch to progress page
        self._stack.setCurrentIndex(2)
        self._progress_value = 0
        self._animate_progress()

        # Start key generation in background thread
        self._worker = KeyGenWorker(self._identity_manager, password, nickname)
        self._worker.finished.connect(self._on_key_gen_success)
        self._worker.error.connect(self._on_key_gen_error)
        self._worker.start()

    def _animate_progress(self) -> None:
        """Simulate progress bar animation during key generation."""
        if self._progress_value < 90:
            self._progress_value += 5
            self._progress.setValue(self._progress_value)
            from PySide6.QtCore import QTimer
            QTimer.singleShot(200, self._animate_progress)

    @Slot(str)
    def _on_key_gen_success(self, user_id: str) -> None:
        """Handle successful key generation."""
        self._progress.setValue(100)

        # Generate identicon
        try:
            pk_bytes = self._identity_manager.public_key_bytes
            pixmap = generate_identicon_pixmap(pk_bytes, 96)
            self._identicon_label.setPixmap(pixmap)
        except Exception:
            self._identicon_label.setText("\U0001f464")

        # Display User ID
        self._user_id_display.setText(user_id)

        # Switch to success page
        self._stack.setCurrentIndex(3)

    @Slot(str)
    def _on_key_gen_error(self, error_msg: str) -> None:
        """Handle key generation error."""
        self._error_label.setText(f"Error: {error_msg}")
        self._stack.setCurrentIndex(1)

    def _on_continue(self) -> None:
        """Proceed to the main app after identity creation."""
        try:
            user_id = self._identity_manager.user_id
            self.identity_created.emit(user_id)
        except Exception:
            pass
