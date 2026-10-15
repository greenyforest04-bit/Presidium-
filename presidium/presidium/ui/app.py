"""Main application window for Presidium.

Orchestrates all UI components:
- Sidebar navigation
- Stacked screens (welcome, chat list, chat room, settings)
- Theme management
- Integration with core services (identity, networking, chat)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QMainWindow,
    QHBoxLayout,
    QWidget,
    QStackedWidget,
    QApplication,
    QMessageBox,
)

from presidium.core.identity import IdentityManager, IdentityNotFoundError
from presidium.core.messaging.chat import ChatManager, ChatMessage, MessageStatus
from presidium.core.network.discovery import DiscoveryService
from presidium.core.network.dht import DHTService
from presidium.core.network.peer import PeerManager
from presidium.core.network.transport import UDPTransport
from presidium.db.manager import DatabaseManager
from presidium.ui.components.sidebar import Sidebar
from presidium.ui.screens.chat_list import ChatListScreen
from presidium.ui.screens.chat_room import ChatRoomScreen
from presidium.ui.screens.settings import SettingsScreen
from presidium.ui.screens.welcome import WelcomeScreen
from presidium.ui.styles.dark_theme import DarkTheme, get_dark_stylesheet
from presidium.ui.styles.light_theme import LightTheme, get_light_stylesheet
from presidium.utils.config import AppConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Screen indices
# ---------------------------------------------------------------------------

class ScreenIndex:
    WELCOME = 0
    CHAT_LIST = 1
    CHAT_ROOM = 2
    SETTINGS = 3
    LOCK = 4


# ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    """Main application window that ties together all components.

    Manages the application lifecycle:
    1. Check for existing identity → show Welcome or Chat List
    2. Initialize networking (transport, DHT, discovery)
    3. Connect UI signals to core services
    4. Handle theme changes
    """

    def __init__(
        self,
        config: AppConfig,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._current_theme = config.ui.theme

        # Core services (initialized later)
        self._identity_manager: Optional[IdentityManager] = None
        self._db_manager: Optional[DatabaseManager] = None
        self._transport: Optional[UDPTransport] = None
        self._peer_manager: Optional[PeerManager] = None
        self._dht: Optional[DHTService] = None
        self._discovery: Optional[DiscoveryService] = None
        self._chat_manager: Optional[ChatManager] = None

        # Setup window
        self._setup_window()
        self._setup_ui()
        self._apply_theme(self._current_theme)

        # Auto-lock timer
        self._lock_timer = QTimer(self)
        self._lock_timer.timeout.connect(self._check_auto_lock)
        if config.ui.auto_lock_timeout > 0:
            self._lock_timer.start(config.ui.auto_lock_timeout * 1000)

        # Async init timer
        self._init_timer = QTimer(self)
        self._init_timer.setSingleShot(True)
        self._init_timer.timeout.connect(self._deferred_init)
        self._init_timer.start(100)

    # -------------------------------------------------------------------
    # Window setup
    # -------------------------------------------------------------------

    def _setup_window(self) -> None:
        """Configure the main window properties."""
        self.setWindowTitle("Presidium")
        self.setMinimumSize(self._config.ui.min_width, 400)
        self.resize(self._config.ui.default_width, self._config.ui.default_height)

        # Try to set font
        font = QFont(self._config.ui.font_family)
        font.setPointSize(self._config.ui.font_size if hasattr(self._config.ui, 'font_size') else 15)
        self.setFont(font)

    # -------------------------------------------------------------------
    # UI setup
    # -------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Build the main layout with sidebar and stacked screens."""
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Sidebar
        self._sidebar = Sidebar()
        self._sidebar.navigate_to.connect(self._on_navigate)
        main_layout.addWidget(self._sidebar)

        # Screen stack
        self._stack = QStackedWidget()
        self._stack.addWidget(WelcomeScreen(IdentityManager(self._config)))  # placeholder
        self._stack.addWidget(ChatListScreen())
        self._stack.addWidget(ChatRoomScreen())
        self._stack.addWidget(SettingsScreen())

        main_layout.addWidget(self._stack, 1)

    # -------------------------------------------------------------------
    # Deferred initialization (async)
    # -------------------------------------------------------------------

    def _deferred_init(self) -> None:
        """Initialize core services after the UI is shown."""
        asyncio.create_task(self._async_init())

    async def _async_init(self) -> None:
        """Asynchronous initialization of all core services."""
        try:
            # Database
            self._db_manager = DatabaseManager(self._config.db_file_path)
            await self._db_manager.initialize()

            # Identity
            self._identity_manager = IdentityManager(self._config)

            if not self._identity_manager.is_initialized:
                # Show welcome screen
                welcome = WelcomeScreen(self._identity_manager)
                welcome.identity_created.connect(self._on_identity_created)
                self._stack.removeWidget(self._stack.widget(ScreenIndex.WELCOME))
                self._stack.insertWidget(ScreenIndex.WELCOME, welcome)
                self._stack.setCurrentIndex(ScreenIndex.WELCOME)
                self._sidebar.hide()
                logger.info("No identity found. Showing welcome screen.")
            else:
                # Need to unlock — for now, show a simple prompt
                # SIMPLIFIED: In production, show a proper unlock dialog
                self._sidebar.show()
                self._stack.setCurrentIndex(ScreenIndex.CHAT_LIST)
                logger.info("Identity exists. Showing chat list.")

        except Exception as exc:
            logger.error("Initialization failed: %s", exc)

    # -------------------------------------------------------------------
    # Identity creation callback
    # -------------------------------------------------------------------

    @Slot(str)
    def _on_identity_created(self, user_id: str) -> None:
        """Handle identity creation — start networking and show chats."""
        logger.info("Identity created: %s", user_id)
        self._sidebar.show()
        self._sidebar.set_user_info(
            user_id,
            self._identity_manager.nickname or "",
        )
        self._stack.setCurrentIndex(ScreenIndex.CHAT_LIST)

        # Start networking in background
        asyncio.ensure_future(self._start_networking())

    # -------------------------------------------------------------------
    # Networking
    # -------------------------------------------------------------------

    async def _start_networking(self) -> None:
        """Initialize and start the P2P networking stack."""
        if self._identity_manager is None or not self._identity_manager.is_unlocked:
            return

        user_id = self._identity_manager.user_id
        pk_bytes = self._identity_manager.public_key_bytes

        # Transport
        self._transport = UDPTransport(self._config.network, bytes.fromhex(user_id))
        await self._transport.start()

        # Peer manager
        self._peer_manager = PeerManager(
            self._transport,
            self._identity_manager.key_pair,
            user_id,
        )
        self._peer_manager.set_peer_connected_handler(self._on_peer_connected)

        # DHT
        local_addr = f"0.0.0.0:{self._config.network.listen_port}"
        self._dht = DHTService(
            self._transport,
            bytes.fromhex(user_id),
            local_addr,
            pk_bytes,
        )
        await self._dht.start()

        # Discovery
        self._discovery = DiscoveryService(
            self._config.network,
            self._transport,
            self._dht,
            self._peer_manager,
            user_id,
            pk_bytes,
        )
        await self._discovery.start()

        # Chat manager
        self._chat_manager = ChatManager(self._peer_manager, user_id)
        self._chat_manager.set_on_message_received(self._on_message_received)
        self._chat_manager.set_on_session_updated(self._on_session_updated)
        self._chat_manager.set_persist_message(self._persist_message)
        self._chat_manager.set_load_messages(self._load_messages)

        # Update settings screen
        settings = self._stack.widget(ScreenIndex.SETTINGS)
        if isinstance(settings, SettingsScreen):
            settings.set_user_info(user_id, pk_bytes)

        logger.info("Networking stack started for user %s", user_id[:8])

    # -------------------------------------------------------------------
    # Navigation
    # -------------------------------------------------------------------

    @Slot(str)
    def _on_navigate(self, screen: str) -> None:
        """Handle sidebar navigation."""
        if screen == Sidebar.CHATS:
            self._stack.setCurrentIndex(ScreenIndex.CHAT_LIST)
        elif screen == Sidebar.SETTINGS:
            self._stack.setCurrentIndex(ScreenIndex.SETTINGS)
        elif screen == Sidebar.CONTACTS:
            # SIMPLIFIED: contacts screen shares the chat list for now
            self._stack.setCurrentIndex(ScreenIndex.CHAT_LIST)

    # -------------------------------------------------------------------
    # Chat callbacks
    # -------------------------------------------------------------------

    async def _on_peer_connected(self, peer_user_id: str) -> None:
        """Handle peer connection event."""
        logger.info("Peer connected: %s", peer_user_id[:8])
        # Update UI on the main thread
        # SIMPLIFIED: update peer status in chat list

    async def _on_message_received(self, message: ChatMessage) -> None:
        """Handle incoming chat message — update UI."""
        # This runs in the async context; we need to update Qt widgets safely
        # SIMPLIFIED: in production, use signals/slots for thread-safe UI updates
        logger.debug("Message received from %s: %s", message.sender_user_id[:8], message.content[:30])

    async def _on_session_updated(self, session) -> None:
        """Handle chat session update."""
        # Update chat list screen
        chat_list_screen = self._stack.widget(ScreenIndex.CHAT_LIST)
        if isinstance(chat_list_screen, ChatListScreen) and self._chat_manager:
            chat_list_screen.set_sessions(self._chat_manager.get_all_sessions())

    async def _persist_message(self, message: ChatMessage) -> None:
        """Persist a message to the database."""
        if self._db_manager:
            try:
                await self._db_manager.save_message(message)
            except Exception as exc:
                logger.error("Failed to persist message: %s", exc)

    async def _load_messages(self, peer_user_id: str) -> list[ChatMessage]:
        """Load message history from the database."""
        if self._db_manager:
            try:
                return await self._db_manager.get_messages(peer_user_id)
            except Exception as exc:
                logger.error("Failed to load messages: %s", exc)
        return []

    # -------------------------------------------------------------------
    # Theme management
    # -------------------------------------------------------------------

    def _apply_theme(self, theme_name: str) -> None:
        """Apply the specified theme stylesheet."""
        if theme_name == "dark":
            stylesheet = get_dark_stylesheet()
        else:
            stylesheet = get_light_stylesheet()
        self.setStyleSheet(stylesheet)
        self._current_theme = theme_name

    @Slot(str)
    def _on_theme_changed(self, theme: str) -> None:
        """Handle theme change from settings."""
        self._apply_theme(theme)
        self._config.ui.theme = theme

    # -------------------------------------------------------------------
    # Auto-lock
    # -------------------------------------------------------------------

    def _check_auto_lock(self) -> None:
        """Check if the application should auto-lock due to inactivity."""
        # SIMPLIFIED: in production, track last user activity timestamp
        pass

    # -------------------------------------------------------------------
    # Cleanup
    # -------------------------------------------------------------------

    def cleanup(self) -> None:
        """Clean up resources before exit.

        If an asyncio event loop is already running (e.g. qasync), schedule
        the async cleanup task on that loop. Otherwise, run the async
        cleanup to completion on a new loop.
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Schedule async cleanup on the running loop and return.
                asyncio.create_task(self._cleanup_async())
            else:
                # No running loop: run cleanup synchronously.
                loop.run_until_complete(self._cleanup_async())
        except Exception as exc:
            logger.error("Cleanup error: %s", exc)

    async def _cleanup_async(self) -> None:
        """Async cleanup executed within the application's event loop."""
        try:
            if self._transport:
                await self._transport.stop()
            if self._dht:
                await self._dht.stop()
            if self._discovery:
                await self._discovery.stop()
            if self._db_manager:
                await self._db_manager.close()
            if self._identity_manager:
                self._identity_manager.lock()
        except Exception as exc:
            logger.error("Cleanup error: %s", exc)

    def closeEvent(self, event) -> None:
        """Handle window close event."""
        self.cleanup()
        super().closeEvent(event)
