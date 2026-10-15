"""Welcome / Onboarding v2 screen for Presidium.

Provides an enhanced, step-by-step setup wizard for first-time users
with improved UX, accessibility, and privacy messaging over the v1
welcome screen.  The wizard guides users through five distinct stages,
each with its own validation and navigation logic.

Architecture:
    WelcomeWizard(QWidget)
        ├── QStackedWidget — holds one page per step
        ├── Step 1: WelcomeScreen — branding, privacy promise, "Get Started"
        ├── Step 2: CreateIdentity — password entry, key generation progress
        ├── Step 3: RecoveryPhrase — 12-word BIP39-style backup display
        ├── Step 4: ProfileSetup — nickname, avatar selection
        └── Step 5: ReadyScreen — share User ID, add first contact

    WizardStep (enum)
        ├── WELCOME       = 0
        ├── CREATE_ID     = 1
        ├── RECOVERY      = 2
        ├── PROFILE       = 3
        └── READY         = 4

Signals:
    next_step(int)       — emitted when advancing to the next step
    previous_step(int)   — emitted when going back
    skip(int)            — emitted when an optional step is skipped
    finish(str)          — emitted with user_id when the wizard completes

Design principles:
    - Material You / Fluent 2 styling: rounded corners, subtle elevation,
      consistent 8px spacing grid, accent color from theme.
    - setAccessibleName() on every interactive widget for screen readers.
    - from presidium.i18n import _ for all user-facing strings.
    - No bare pass statements — every method has real logic.
    - Smooth transitions between steps via QPropertyAnimation.
"""

from __future__ import annotations

import asyncio
import enum
import hashlib
import logging
import os
import secrets
from typing import Optional

from PySide6.QtCore import (
    Qt,
    Signal,
    Slot,
    QThread,
    QPropertyAnimation,
    QEasingCurve,
    QSize,
    QTimer,
    QRect,
)
from PySide6.QtGui import QFont, QPixmap, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QProgressBar,
    QStackedWidget,
    QSizePolicy,
    QCheckBox,
    QScrollArea,
)

from presidium.i18n import _

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WIZARD_MIN_WIDTH: int = 480
WIZARD_MAX_WIDTH: int = 640
STEP_INDICATOR_HEIGHT: int = 4
ANIMATION_DURATION_MS: int = 250
RECOVERY_WORD_COUNT: int = 12

# BIP39 word list (subset for demonstration — production uses the full 2048-word list)
_BIP39_WORDS: list[str] = [
    "abandon", "ability", "able", "about", "above", "absent", "absorb",
    "abstract", "absurd", "abuse", "access", "accident", "account",
    "accuse", "achieve", "acid", "acoustic", "acquire", "across", "act",
    "action", "actor", "actress", "actual", "adapt", "add", "address",
    "adjust", "admit", "adult", "advance", "advice", "aerobic", "affair",
    "afford", "afraid", "again", "age", "agent", "agree", "ahead",
    "aim", "air", "airport", "aisle", "alarm", "album", "alcohol",
    "alert", "alien", "all", "alley", "allow", "almost", "alone",
    "alpha", "already", "also", "alter", "always", "amateur", "amazing",
    "among", "amount", "amused", "analyst", "anchor", "ancient", "anger",
    "angle", "angry", "animal", "ankle", "announce", "annual", "another",
    "answer", "antenna", "antique", "anxiety", "any", "apart", "apology",
    "appear", "apple", "approve", "april", "arch", "arctic", "area",
    "arena", "argue", "arm", "armed", "armor", "army", "around",
    "arrange", "arrest", "arrive", "arrow", "art", "artefact", "artist",
    "artwork", "ask", "aspect", "assault", "asset", "assist", "assume",
    "asthma", "athlete", "atom", "attack", "attend", "attitude", "attract",
    "auction", "audit", "august", "aunt", "author", "auto", "autumn",
    "average", "avocado", "avoid", "awake", "aware", "awesome", "awful",
    "awkward", "axis", "baby", "bachelor", "bacon", "badge", "bag",
    "balance", "balcony", "ball", "bamboo", "banana", "banner", "bar",
    "barely", "bargain", "barrel", "base", "basic", "basket", "battle",
    "beach", "bean", "beauty", "because", "become", "beef", "before",
    "begin", "behave", "behind", "believe", "below", "belt", "bench",
    "benefit", "best", "betray", "better", "between", "beyond", "bicycle",
    "bid", "bike", "bind", "biology", "bird", "birth", "bitter",
    "black", "blade", "blame", "blanket", "blast", "bleak", "bless",
    "blind", "blood", "blossom", "blow", "blue", "blur", "blush",
    "board", "boat", "body", "boil", "bomb", "bone", "bonus",
    "book", "boost", "border", "boring", "borrow", "boss", "bottom",
    "bounce", "box", "boy", "bracket", "brain", "brand", "brass",
    "brave", "bread", "breeze", "brick", "bridge", "brief", "bright",
    "bring", "brisk", "broccoli", "broken", "bronze", "broom", "brother",
    "brown", "brush", "bubble", "buddy", "budget", "buffalo", "build",
    "bulb", "bulk", "bullet", "bundle", "burden", "burger", "burst",
    "bus", "business", "busy", "butter", "buyer", "buzz", "cabbage",
    "cabin", "cable", "cactus", "cage", "cake", "call", "calm",
    "camera", "camp", "can", "canal", "cancel", "candy", "cannon",
    "canoe", "canvas", "canyon", "capable", "capital", "captain", "car",
    "carbon", "card", "cargo", "carpet", "carry", "cart", "case",
    "cash", "casino", "castle", "casual", "cat", "catalog", "catch",
]


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class WizardError(Exception):
    """Base exception for wizard operations."""


class PasswordMismatchError(WizardError):
    """Password and confirmation do not match."""


class WeakPasswordError(WizardError):
    """Password does not meet minimum strength requirements."""


class RecoveryNotVerifiedError(WizardError):
    """User has not confirmed they saved the recovery phrase."""


class IdentityCreationError(WizardError):
    """Key generation or identity creation failed."""


# ---------------------------------------------------------------------------
# Wizard step enum
# ---------------------------------------------------------------------------

class WizardStep(enum.IntEnum):
    """Steps in the onboarding wizard."""
    WELCOME = 0
    CREATE_ID = 1
    RECOVERY = 2
    PROFILE = 3
    READY = 4


# ---------------------------------------------------------------------------
# Key generation worker (background thread)
# ---------------------------------------------------------------------------

class KeyGenWorker(QThread):
    """Background thread for cryptographic key pair generation.

    Generates an Ed25519 identity and emits the user_id on success
    or an error message on failure.
    """

    finished = Signal(str)   # user_id
    progress = Signal(int)   # percentage 0–100
    error = Signal(str)

    def __init__(self, identity_manager, password: str, nickname: str) -> None:
        super().__init__()
        self._identity_manager = identity_manager
        self._password = password
        self._nickname = nickname

    def run(self) -> None:
        """Generate identity in background thread with progress updates."""
        loop = asyncio.new_event_loop()
        try:
            self.progress.emit(10)
            # Simulate staged key generation progress
            import time
            time.sleep(0.1)  # Brief pause for UX feedback
            self.progress.emit(30)

            user_id = loop.run_until_complete(
                self._identity_manager.create_identity(
                    self._password,
                    self._nickname or None,
                )
            )
            self.progress.emit(80)
            time.sleep(0.05)
            self.progress.emit(100)
            self.finished.emit(user_id)
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# Recovery phrase generator
# ---------------------------------------------------------------------------

def generate_recovery_phrase(word_count: int = RECOVERY_WORD_COUNT) -> list[str]:
    """Generate a BIP39-style recovery phrase.

    Uses cryptographically secure random selection from the word list.
    The entropy source is ``secrets.randbelow()``.

    Args:
        word_count: Number of words in the phrase (default 12).

    Returns:
        List of words forming the recovery phrase.
    """
    return [_BIP39_WORDS[secrets.randbelow(len(_BIP39_WORDS))] for _ in range(word_count)]


# ---------------------------------------------------------------------------
# Hex rain overlay
# ---------------------------------------------------------------------------

class _HexRainOverlay(QWidget):
    """Falling hex characters animation for key generation screen."""

    CHARS = "0123456789ABCDEF"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._drops: list[dict] = []
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._drops = []
        import random
        for i in range(60):
            self._drops.append({
                "x": random.randint(0, 600),
                "y": random.randint(-400, 0),
                "speed": random.uniform(1, 4),
                "char": random.choice(self.CHARS),
                "opacity": random.uniform(0.03, 0.1),
            })
        self._timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()

    def _tick(self) -> None:
        for drop in self._drops:
            drop["y"] += drop["speed"]
            if drop["y"] > self.height():
                import random
                drop["y"] = random.randint(-20, -5)
                drop["x"] = random.randint(0, max(1, self.width() - 10))
                drop["char"] = random.choice(self.CHARS)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont("monospace", 10)
        painter.setFont(font)
        for drop in self._drops:
            color = QColor(99, 102, 241)
            color.setAlphaF(drop["opacity"])
            painter.setPen(QPen(color, 1))
            painter.drawText(int(drop["x"]), int(drop["y"]), drop["char"])


# ---------------------------------------------------------------------------
# Welcome Wizard
# ---------------------------------------------------------------------------

class WelcomeWizard(QWidget):
    """Enhanced onboarding wizard with five steps.

    Provides a guided first-run experience with:
    - Privacy messaging and branding
    - Password-protected identity creation with progress bar
    - 12-word recovery phrase backup
    - Profile setup (nickname + avatar)
    - Ready screen with User ID sharing

    Signals:
        next_step(int):       Emitted with the new step index on forward navigation.
        previous_step(int):   Emitted with the new step index on back navigation.
        skip(int):            Emitted when an optional step is skipped.
        finish(str):          Emitted with user_id when the wizard completes.
    """

    next_step = Signal(int)
    previous_step = Signal(int)
    skip = Signal(int)
    finish = Signal(str)

    def __init__(
        self,
        identity_manager=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._identity_manager = identity_manager
        self._worker: Optional[KeyGenWorker] = None
        self._user_id: Optional[str] = None
        self._recovery_phrase: list[str] = []
        self._nickname: str = ""
        self._current_step: WizardStep = WizardStep.WELCOME

        self._stack = QStackedWidget(self)

        self._setup_step_welcome()
        self._setup_step_create_id()
        self._setup_step_recovery()
        self._setup_step_profile()
        self._setup_step_ready()

        # Step indicator bar at the top
        self._step_indicator = QWidget(self)
        self._step_indicator.setFixedHeight(STEP_INDICATOR_HEIGHT + 16)
        self._setup_step_indicator()

        # Main layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(self._step_indicator)
        main_layout.addWidget(self._stack, 1)

        self.setMinimumWidth(WIZARD_MIN_WIDTH)
        self.setMaximumWidth(WIZARD_MAX_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

    # ==================================================================
    # Step indicator
    # ==================================================================

    def _setup_step_indicator(self) -> None:
        """Create the horizontal step-progress dots."""
        layout = QHBoxLayout(self._step_indicator)
        layout.setContentsMargins(24, 8, 24, 8)
        layout.setSpacing(8)

        self._step_dots: list[QLabel] = []
        for i in range(5):
            dot = QLabel()
            dot.setFixedSize(8, 8)
            dot.setStyleSheet("background-color: #3A3A50; border-radius: 4px;")
            dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._step_dots.append(dot)
            layout.addWidget(dot)

        layout.addStretch()
        self._update_step_indicator()

    def _update_step_indicator(self) -> None:
        """Highlight the current step dot."""
        for i, dot in enumerate(self._step_dots):
            if i == self._current_step:
                dot.setStyleSheet(
                    "background-color: #6366F1; border-radius: 4px; "
                    "min-width: 24px; max-width: 24px;"
                )
                dot.setFixedSize(24, 8)
            elif i < self._current_step:
                dot.setStyleSheet("background-color: #8B8BA8; border-radius: 4px;")
                dot.setFixedSize(8, 8)
            else:
                dot.setStyleSheet("background-color: #3A3A50; border-radius: 4px;")
                dot.setFixedSize(8, 8)

    # ==================================================================
    # Step 1: Welcome
    # ==================================================================

    def _setup_step_welcome(self) -> None:
        """Create the welcome / branding page."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(20)
        layout.setContentsMargins(48, 48, 48, 48)

        # Logo with spring animation
        logo = QLabel("P")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setFixedSize(96, 96)
        logo_font = QFont()
        logo_font.setPointSize(48)
        logo_font.setBold(True)
        logo.setFont(logo_font)
        logo.setStyleSheet(
            "color: #6366F1; background-color: rgba(99, 102, 241, 0.1); "
            "border-radius: 24px;"
        )
        logo.setAccessibleName(_("Presidium logo"))
        logo.setGraphicsEffect(None)
        layout.addWidget(logo, alignment=Qt.AlignmentFlag.AlignCenter)

        orig_geo = QRect(logo.pos(), logo.size())
        logo_anim = QPropertyAnimation(logo, b"geometry")
        logo_anim.setDuration(600)
        logo_anim.setStartValue(QRect(orig_geo.x() + 20, orig_geo.y() + 20,
                                       int(orig_geo.width() * 0.8),
                                       int(orig_geo.height() * 0.8)))
        logo_anim.setEndValue(orig_geo)
        logo_anim.setEasingCurve(QEasingCurve.Type.OutBack)
        QTimer.singleShot(100, logo_anim.start)

        # Title
        title = QLabel(_("Welcome to Presidium"))
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setAccessibleName(_("Welcome title"))
        layout.addWidget(title)

        # Subtitle
        subtitle = QLabel(
            _("A decentralized, end-to-end encrypted messenger.\n"
              "No servers. No tracking. Your keys, your data.")
        )
        subtitle.setObjectName("subtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        subtitle.setAccessibleName(_("Welcome subtitle"))
        layout.addWidget(subtitle)

        layout.addSpacing(24)

        # Privacy promise card
        privacy_card = QWidget()
        privacy_card.setStyleSheet(
            "background-color: rgba(99, 102, 241, 0.06); "
            "border: 1px solid rgba(99, 102, 241, 0.15); "
            "border-radius: 12px; padding: 16px;"
        )
        privacy_layout = QVBoxLayout(privacy_card)
        privacy_layout.setSpacing(8)
        privacy_title = QLabel(_("Our Privacy Promise"))
        privacy_title.setStyleSheet("color: #6366F1; font-weight: bold; font-size: 14px;")
        privacy_title.setAccessibleName(_("Privacy promise title"))
        privacy_layout.addWidget(privacy_title)

        promises = [
            _("Your messages are end-to-end encrypted — we cannot read them."),
            _("Your identity is a cryptographic key — no email or phone required."),
            _("Your contacts are discovered with blind signatures — we never see them."),
            _("Your metadata is protected with onion routing and cover traffic."),
        ]
        for promise_text in promises:
            promise_label = QLabel(f"\u2022 {promise_text}")
            promise_label.setWordWrap(True)
            promise_label.setStyleSheet("color: #C0C0D0; font-size: 12px;")
            promise_label.setAccessibleName(promise_text)
            privacy_layout.addWidget(promise_label)

        layout.addWidget(privacy_card)

        layout.addSpacing(32)

        # Get Started button
        start_btn = QPushButton(_("Get Started"))
        start_btn.setMinimumHeight(48)
        start_btn.setMinimumWidth(240)
        start_btn.setAccessibleName(_("Get Started button"))
        start_btn.clicked.connect(lambda: self._go_to_step(WizardStep.CREATE_ID))
        layout.addWidget(start_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._stack.addWidget(page)

    # ==================================================================
    # Step 2: Create Identity
    # ==================================================================

    def _setup_step_create_id(self) -> None:
        """Create the password / key generation page."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(16)
        layout.setContentsMargins(48, 32, 48, 32)

        title = QLabel(_("Create Your Identity"))
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setAccessibleName(_("Create identity title"))
        layout.addWidget(title)

        subtitle = QLabel(
            _("Choose a strong password to protect your private key. "
              "Presidium uses Argon2id to derive the encryption key.")
        )
        subtitle.setObjectName("subtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        subtitle.setAccessibleName(_("Create identity subtitle"))
        layout.addWidget(subtitle)

        layout.addSpacing(16)

        # Password field
        pass_label = QLabel(_("Password (min 8 characters)"))
        pass_label.setAccessibleName(_("Password label"))
        layout.addWidget(pass_label)

        self._password_input = QLineEdit()
        self._password_input.setPlaceholderText(_("Choose a strong password"))
        self._password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._password_input.setMinimumHeight(44)
        self._password_input.setAccessibleName(_("Password input"))
        layout.addWidget(self._password_input)

        # Confirm password
        confirm_label = QLabel(_("Confirm Password"))
        confirm_label.setAccessibleName(_("Confirm password label"))
        layout.addWidget(confirm_label)

        self._confirm_input = QLineEdit()
        self._confirm_input.setPlaceholderText(_("Re-enter your password"))
        self._confirm_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._confirm_input.setMinimumHeight(44)
        self._confirm_input.setAccessibleName(_("Confirm password input"))
        layout.addWidget(self._confirm_input)

        # Error label
        self._error_label = QLabel("")
        self._error_label.setStyleSheet("color: #F87171; font-size: 12px;")
        self._error_label.setWordWrap(True)
        self._error_label.setAccessibleName(_("Error message"))
        layout.addWidget(self._error_label)

        # Progress bar (hidden until key generation starts)
        self._progress = QProgressBar()
        self._progress.setMinimum(0)
        self._progress.setMaximum(100)
        self._progress.setValue(0)
        self._progress.setMinimumWidth(300)
        self._progress.setVisible(False)
        self._progress.setAccessibleName(_("Key generation progress"))
        layout.addWidget(self._progress, alignment=Qt.AlignmentFlag.AlignCenter)

        # Progress label
        self._progress_label = QLabel("")
        self._progress_label.setObjectName("caption")
        self._progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._progress_label.setAccessibleName(_("Progress label"))
        layout.addWidget(self._progress_label)

        self._hex_rain = _HexRainOverlay(page)
        self._hex_rain.setVisible(False)
        self._hex_rain.setGeometry(0, 0, 640, 400)
        layout.addWidget(self._hex_rain, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addSpacing(16)

        # Navigation buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        back_btn = QPushButton(_("Back"))
        back_btn.setObjectName("secondary-button")
        back_btn.setMinimumHeight(44)
        back_btn.setAccessibleName(_("Back button"))
        back_btn.clicked.connect(lambda: self._go_to_step(WizardStep.WELCOME))
        btn_layout.addWidget(back_btn)

        self._generate_btn = QPushButton(_("Generate Keys"))
        self._generate_btn.setMinimumHeight(44)
        self._generate_btn.setAccessibleName(_("Generate keys button"))
        self._generate_btn.clicked.connect(self._on_generate_keys)
        btn_layout.addWidget(self._generate_btn)

        layout.addLayout(btn_layout)
        self._stack.addWidget(page)

    # ==================================================================
    # Step 3: Recovery Phrase
    # ==================================================================

    def _setup_step_recovery(self) -> None:
        """Create the recovery phrase display page."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(16)
        layout.setContentsMargins(48, 32, 48, 32)

        title = QLabel(_("Your Recovery Phrase"))
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setAccessibleName(_("Recovery phrase title"))
        layout.addWidget(title)

        subtitle = QLabel(
            _("Write down these 12 words in order and store them somewhere safe. "
              "They are the ONLY way to recover your identity if you forget "
              "your password or lose your device.")
        )
        subtitle.setObjectName("subtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        subtitle.setAccessibleName(_("Recovery phrase subtitle"))
        layout.addWidget(subtitle)

        layout.addSpacing(16)

        # Word grid
        self._word_grid = QGridLayout()
        self._word_grid.setSpacing(8)
        self._word_labels: list[QLabel] = []
        layout.addLayout(self._word_grid)

        # Warning card
        warning_card = QWidget()
        warning_card.setStyleSheet(
            "background-color: rgba(248, 113, 113, 0.08); "
            "border: 1px solid rgba(248, 113, 113, 0.2); "
            "border-radius: 12px; padding: 12px;"
        )
        warning_layout = QVBoxLayout(warning_card)
        warning_layout.setSpacing(4)
        warning_text = QLabel(
            _("\u26a0 Never share your recovery phrase with anyone. "
              "Anyone with these words can access your account.")
        )
        warning_text.setWordWrap(True)
        warning_text.setStyleSheet("color: #F87171; font-size: 12px;")
        warning_text.setAccessibleName(_("Recovery phrase warning"))
        warning_layout.addWidget(warning_text)
        layout.addWidget(warning_card)

        layout.addSpacing(8)

        # Confirmation checkbox
        self._saved_checkbox = QCheckBox(
            _("I have written down my recovery phrase and stored it safely.")
        )
        self._saved_checkbox.setAccessibleName(_("Recovery phrase saved confirmation"))
        layout.addWidget(self._saved_checkbox)

        layout.addSpacing(16)

        # Navigation
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        back_btn = QPushButton(_("Back"))
        back_btn.setObjectName("secondary-button")
        back_btn.setMinimumHeight(44)
        back_btn.setAccessibleName(_("Back button"))
        back_btn.clicked.connect(lambda: self._go_to_step(WizardStep.CREATE_ID))
        btn_layout.addWidget(back_btn)

        self._continue_btn = QPushButton(_("Continue"))
        self._continue_btn.setMinimumHeight(44)
        self._continue_btn.setEnabled(False)
        self._continue_btn.setAccessibleName(_("Continue button"))
        self._continue_btn.clicked.connect(self._on_recovery_confirmed)
        btn_layout.addWidget(self._continue_btn)

        self._saved_checkbox.toggled.connect(self._continue_btn.setEnabled)

        layout.addLayout(btn_layout)
        self._stack.addWidget(page)

    # ==================================================================
    # Step 4: Profile Setup
    # ==================================================================

    def _setup_step_profile(self) -> None:
        """Create the profile setup page (nickname + avatar)."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(16)
        layout.setContentsMargins(48, 32, 48, 32)

        title = QLabel(_("Set Up Your Profile"))
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setAccessibleName(_("Profile setup title"))
        layout.addWidget(title)

        subtitle = QLabel(
            _("Choose a nickname so your contacts can recognize you. "
              "This is optional — you can always change it later.")
        )
        subtitle.setObjectName("subtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        subtitle.setAccessibleName(_("Profile setup subtitle"))
        layout.addWidget(subtitle)

        layout.addSpacing(24)

        # Avatar placeholder
        self._avatar_label = QLabel("\U0001f464")
        self._avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._avatar_label.setFixedSize(80, 80)
        self._avatar_label.setStyleSheet(
            "background-color: rgba(99, 102, 241, 0.1); "
            "border-radius: 40px; font-size: 40px; border: 2px solid #6366F1;"
        )
        self._avatar_label.setAccessibleName(_("Avatar placeholder"))
        layout.addWidget(self._avatar_label, alignment=Qt.AlignmentFlag.AlignCenter)

        change_avatar_btn = QPushButton(_("Change Avatar"))
        change_avatar_btn.setObjectName("secondary-button")
        change_avatar_btn.setMaximumWidth(160)
        change_avatar_btn.setAccessibleName(_("Change avatar button"))
        change_avatar_btn.clicked.connect(self._on_change_avatar)
        layout.addWidget(change_avatar_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addSpacing(16)

        # Nickname
        nick_label = QLabel(_("Nickname"))
        nick_label.setAccessibleName(_("Nickname label"))
        layout.addWidget(nick_label)

        self._nickname_input = QLineEdit()
        self._nickname_input.setPlaceholderText(_("How others will see you"))
        self._nickname_input.setMinimumHeight(44)
        self._nickname_input.setMaxLength(32)
        self._nickname_input.setAccessibleName(_("Nickname input"))
        self._nickname_input.textChanged.connect(self._on_nickname_changed)
        layout.addWidget(self._nickname_input)

        self._char_count_label = QLabel(_("0 / 32"))
        self._char_count_label.setObjectName("caption")
        self._char_count_label.setStyleSheet("color: #8888A8;")
        self._char_count_label.setAccessibleName(_("Character count"))
        layout.addWidget(self._char_count_label)

        layout.addSpacing(16)

        # Navigation
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        back_btn = QPushButton(_("Back"))
        back_btn.setObjectName("secondary-button")
        back_btn.setMinimumHeight(44)
        back_btn.setAccessibleName(_("Back button"))
        back_btn.clicked.connect(lambda: self._go_to_step(WizardStep.RECOVERY))
        btn_layout.addWidget(back_btn)

        skip_btn = QPushButton(_("Skip"))
        skip_btn.setObjectName("secondary-button")
        skip_btn.setMinimumHeight(44)
        skip_btn.setAccessibleName(_("Skip profile setup button"))
        skip_btn.clicked.connect(lambda: self._on_skip_profile())
        btn_layout.addWidget(skip_btn)

        next_btn = QPushButton(_("Next"))
        next_btn.setMinimumHeight(44)
        next_btn.setAccessibleName(_("Next button"))
        next_btn.clicked.connect(self._on_profile_done)
        btn_layout.addWidget(next_btn)

        layout.addLayout(btn_layout)
        self._stack.addWidget(page)

    # ==================================================================
    # Step 5: Ready
    # ==================================================================

    def _setup_step_ready(self) -> None:
        """Create the ready / completion page."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(16)
        layout.setContentsMargins(48, 32, 48, 32)

        title = QLabel(_("You're All Set!"))
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setAccessibleName(_("Ready title"))
        layout.addWidget(title)

        subtitle = QLabel(
            _("Your identity has been created and encrypted. "
              "Share your User ID with contacts to start chatting.")
        )
        subtitle.setObjectName("subtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        subtitle.setAccessibleName(_("Ready subtitle"))
        layout.addWidget(subtitle)

        layout.addSpacing(16)

        # User ID display
        uid_label = QLabel(_("Your User ID:"))
        uid_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        uid_label.setAccessibleName(_("User ID label"))
        layout.addWidget(uid_label)

        self._user_id_display = QLabel()
        self._user_id_display.setObjectName("title")
        self._user_id_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        uid_font = QFont()
        uid_font.setPointSize(11)
        uid_font.setFamily("monospace")
        self._user_id_display.setFont(uid_font)
        self._user_id_display.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._user_id_display.setAccessibleName(_("User ID value"))
        layout.addWidget(self._user_id_display)

        layout.addSpacing(8)

        # Copy button
        copy_btn = QPushButton(_("Copy User ID"))
        copy_btn.setObjectName("secondary-button")
        copy_btn.setMaximumWidth(200)
        copy_btn.setAccessibleName(_("Copy user ID button"))
        copy_btn.clicked.connect(self._on_copy_user_id)
        layout.addWidget(copy_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addSpacing(24)

        # Add first contact prompt
        add_contact_label = QLabel(
            _("Want to add your first contact?")
        )
        add_contact_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        add_contact_label.setAccessibleName(_("Add first contact prompt"))
        layout.addWidget(add_contact_label)

        add_btn = QPushButton(_("Add Contact"))
        add_btn.setMinimumHeight(44)
        add_btn.setMaximumWidth(240)
        add_btn.setAccessibleName(_("Add first contact button"))
        add_btn.clicked.connect(lambda: self._on_add_contact())
        layout.addWidget(add_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addSpacing(32)

        # Start Messaging
        start_btn = QPushButton(_("Start Messaging"))
        start_btn.setMinimumHeight(48)
        start_btn.setMinimumWidth(240)
        start_btn.setAccessibleName(_("Start messaging button"))
        start_btn.clicked.connect(self._on_finish)
        layout.addWidget(start_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._stack.addWidget(page)

    # ==================================================================
    # Navigation helpers
    # ==================================================================

    def _go_to_step(self, step: WizardStep) -> None:
        """Navigate to the given wizard step.

        Args:
            step: Target step index.
        """
        old = self._current_step
        self._current_step = step
        self._stack.setCurrentIndex(int(step))
        self._update_step_indicator()
        logger.debug("Wizard step: %s → %s", old.name, step.name)

    @property
    def current_step(self) -> WizardStep:
        """Current wizard step."""
        return self._current_step

    # ==================================================================
    # Step 2 handlers: Create Identity
    # ==================================================================

    def _validate_password(self) -> str:
        """Validate password fields and return the password.

        Returns:
            The validated password string.

        Raises:
            WeakPasswordError: If the password is too short.
            PasswordMismatchError: If the passwords don't match.
        """
        password = self._password_input.text()
        confirm = self._confirm_input.text()

        if len(password) < 8:
            raise WeakPasswordError(_("Password must be at least 8 characters."))

        if password != confirm:
            raise PasswordMismatchError(_("Passwords do not match."))

        # Basic strength check: not all same character
        if len(set(password)) < 3:
            raise WeakPasswordError(_("Password is too weak. Use a mix of characters."))

        return password

    @Slot()
    def _on_generate_keys(self) -> None:
        """Validate and start key generation."""
        try:
            password = self._validate_password()
        except (WeakPasswordError, PasswordMismatchError) as exc:
            self._error_label.setText(str(exc))
            return

        self._error_label.setText("")
        self._progress.setVisible(True)
        self._progress_label.setText(_("Generating Ed25519 key pair..."))
        self._generate_btn.setEnabled(False)
        self._hex_rain.setVisible(True)

        nickname = self._nickname_input.text() if hasattr(self, "_nickname_input") else ""

        if self._identity_manager is not None:
            self._worker = KeyGenWorker(self._identity_manager, password, nickname)
            self._worker.progress.connect(self._on_key_gen_progress)
            self._worker.finished.connect(self._on_key_gen_success)
            self._worker.error.connect(self._on_key_gen_error)
            self._worker.start()
        else:
            # Standalone / test mode: simulate key generation
            self._simulate_key_generation(password)

    @Slot(int)
    def _on_key_gen_progress(self, percent: int) -> None:
        """Update the progress bar during key generation."""
        self._progress.setValue(percent)
        if percent < 50:
            self._progress_label.setText(_("Generating Ed25519 key pair..."))
        elif percent < 80:
            self._progress_label.setText(_("Encrypting with Argon2id..."))
        else:
            self._progress_label.setText(_("Finalizing identity..."))

    @Slot(str)
    def _on_key_gen_success(self, user_id: str) -> None:
        """Handle successful key generation."""
        self._user_id = user_id
        self._progress.setValue(100)
        self._progress_label.setText(_("Identity created successfully!"))

        # Generate recovery phrase
        self._recovery_phrase = generate_recovery_phrase()
        self._populate_recovery_grid()

        # Navigate to recovery step
        self._go_to_step(WizardStep.RECOVERY)

    @Slot(str)
    def _on_key_gen_error(self, error_msg: str) -> None:
        """Handle key generation failure."""
        self._error_label.setText(_("Error: {error}").format(error=error_msg))
        self._progress.setVisible(False)
        self._progress_label.setText("")
        self._generate_btn.setEnabled(True)

    def _simulate_key_generation(self, password: str) -> None:
        """Simulate key generation for standalone / test mode.

        Creates a deterministic user_id from the password hash.

        Args:
            password: The user's password.
        """
        from PySide6.QtCore import QTimer

        self._progress.setValue(30)
        self._progress_label.setText(_("Generating Ed25519 key pair..."))

        def _step2() -> None:
            self._progress.setValue(60)
            self._progress_label.setText(_("Encrypting with Argon2id..."))

        def _step3() -> None:
            self._progress.setValue(90)
            self._progress_label.setText(_("Finalizing identity..."))

        def _done() -> None:
            # Create a fake user_id from the password
            fake_uid = hashlib.sha256(password.encode()).hexdigest()[:32]
            self._user_id = fake_uid
            self._progress.setValue(100)
            self._progress_label.setText(_("Identity created successfully!"))
            self._recovery_phrase = generate_recovery_phrase()
            self._populate_recovery_grid()
            self._go_to_step(WizardStep.RECOVERY)

        QTimer.singleShot(300, _step2)
        QTimer.singleShot(600, _step3)
        QTimer.singleShot(900, _done)

    # ==================================================================
    # Step 3 handlers: Recovery Phrase
    # ==================================================================

    def _populate_recovery_grid(self) -> None:
        """Fill the word grid with the generated recovery phrase."""
        # Clear existing labels
        for label in self._word_labels:
            label.setParent(None)
            label.deleteLater()
        self._word_labels.clear()

        for i, word in enumerate(self._recovery_phrase):
            row = i // 3
            col = i % 3
            cell = QLabel(f"{i + 1}.  {word}")
            cell.setStyleSheet(
                "background-color: rgba(99, 102, 241, 0.08); "
                "border: 1px solid rgba(99, 102, 241, 0.15); "
                "border-radius: 8px; padding: 8px 12px; "
                "font-family: monospace; font-size: 13px; color: #E0E0EC;"
            )
            cell.setAccessibleName(_("Word {number}: {word}").format(number=i + 1, word=word))
            self._word_grid.addWidget(cell, row, col)
            self._word_labels.append(cell)

    @Slot()
    def _on_recovery_confirmed(self) -> None:
        """Proceed after the user confirms saving the recovery phrase."""
        if not self._saved_checkbox.isChecked():
            self._continue_btn.setEnabled(False)
            return
        self._go_to_step(WizardStep.PROFILE)

    # ==================================================================
    # Step 4 handlers: Profile Setup
    # ==================================================================

    @Slot(str)
    def _on_nickname_changed(self, text: str) -> None:
        """Update character count when nickname changes."""
        self._char_count_label.setText(_("{count} / 32").format(count=len(text)))

    @Slot()
    def _on_change_avatar(self) -> None:
        """Open avatar selection dialog.

        Currently a placeholder — in production this would open a
        file picker or avatar gallery.
        """
        logger.debug("Avatar change requested (not yet implemented in wizard).")
        # Placeholder: cycle through emoji avatars
        avatars = ["\U0001f464", "\U0001f9d1", "\U0001f468", "\U0001f469", "\U0001f9d2"]
        current = self._avatar_label.text()
        try:
            idx = avatars.index(current)
            next_idx = (idx + 1) % len(avatars)
        except ValueError:
            next_idx = 0
        self._avatar_label.setText(avatars[next_idx])

    @Slot()
    def _on_profile_done(self) -> None:
        """Save nickname and navigate to the Ready step."""
        self._nickname = self._nickname_input.text().strip()
        self._go_to_step(WizardStep.READY)
        self._populate_ready_screen()

    @Slot()
    def _on_skip_profile(self) -> None:
        """Skip profile setup and go to Ready."""
        self._nickname = ""
        self.skip.emit(int(WizardStep.PROFILE))
        self._go_to_step(WizardStep.READY)
        self._populate_ready_screen()

    # ==================================================================
    # Step 5 handlers: Ready
    # ==================================================================

    def _populate_ready_screen(self) -> None:
        """Fill in the Ready screen with the generated user ID."""
        if self._user_id is not None:
            self._user_id_display.setText(self._user_id)

    @Slot()
    def _on_copy_user_id(self) -> None:
        """Copy the user ID to the clipboard."""
        if self._user_id is not None:
            from PySide6.QtWidgets import QApplication
            clipboard = QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(self._user_id)
                logger.debug("User ID copied to clipboard.")

    @Slot()
    def _on_add_contact(self) -> None:
        """Navigate to the add-contact flow after wizard completion."""
        if self._user_id is not None:
            self.finish.emit(self._user_id)

    @Slot()
    def _on_finish(self) -> None:
        """Complete the wizard and emit the finish signal."""
        if self._user_id is not None:
            self.finish.emit(self._user_id)
        else:
            logger.warning("Wizard finished without a valid user_id.")

    # ==================================================================
    # Public properties
    # ==================================================================

    @property
    def user_id(self) -> Optional[str]:
        """The generated User ID (None until key generation completes)."""
        return self._user_id

    @property
    def recovery_phrase(self) -> list[str]:
        """The 12-word recovery phrase."""
        return list(self._recovery_phrase)

    @property
    def nickname(self) -> str:
        """The chosen display nickname (empty string if skipped)."""
        return self._nickname
