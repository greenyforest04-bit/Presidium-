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
import time
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Slot, QEvent
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QMainWindow,
    QHBoxLayout,
    QWidget,
    QStackedWidget,
    QApplication,
    QMessageBox,
    QVBoxLayout,
    QLineEdit,
    QPushButton,
    QLabel,
    QInputDialog,
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
        self._identity_manager: Optional[IdentityManager] = IdentityManager(config)
        self._last_activity = time.monotonic()
        self._locking = False
        self._closing = False
        self._close_task = None
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
            self._lock_timer.start(1000)
        QApplication.instance().installEventFilter(self)

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
        font.setPointSize(
            self._config.ui.font_size if hasattr(self._config.ui, "font_size") else 15
        )
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
        self._stack.addWidget(WelcomeScreen(self._identity_manager))
        self._stack.addWidget(ChatListScreen())
        self._stack.addWidget(ChatRoomScreen())
        self._stack.addWidget(SettingsScreen())
        lock_screen = QWidget()
        lock_layout = QVBoxLayout(lock_screen)
        lock_layout.addWidget(QLabel("Presidium is locked"))
        self._unlock_password = QLineEdit()
        self._unlock_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._unlock_password.setPlaceholderText("Identity password")
        lock_layout.addWidget(self._unlock_password)
        self._unlock_error = QLabel()
        lock_layout.addWidget(self._unlock_error)
        self._unlock_button = QPushButton("Unlock")
        lock_layout.addWidget(self._unlock_button)
        self._stack.addWidget(lock_screen)
        self._unlock_button.clicked.connect(lambda: asyncio.create_task(self._unlock_identity()))
        self._unlock_password.returnPressed.connect(
            lambda: asyncio.create_task(self._unlock_identity())
        )
        self._stack.widget(ScreenIndex.CHAT_LIST).chat_opened.connect(self._open_chat)
        self._stack.widget(ScreenIndex.CHAT_LIST).new_chat_requested.connect(self._new_chat)
        self._stack.widget(ScreenIndex.CHAT_ROOM).send_message.connect(
            lambda peer, text, reply: asyncio.create_task(self._send_message(peer, text, reply))
        )
        self._stack.widget(ScreenIndex.CHAT_ROOM).back_requested.connect(
            lambda: self._stack.setCurrentIndex(ScreenIndex.CHAT_LIST)
        )
        settings = self._stack.widget(ScreenIndex.SETTINGS)
        settings.theme_changed.connect(self._on_theme_changed)
        settings.logout_requested.connect(lambda: asyncio.create_task(self._lock_identity()))

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
                self._show_lock_screen()

        except Exception as exc:
            logger.error("Initialization failed: %s", exc)

    # -------------------------------------------------------------------
    # Identity creation callback
    # -------------------------------------------------------------------

    @Slot(str)
    def _on_identity_created(self, user_id: str) -> None:
        """Handle identity creation — start networking and show chats."""
        logger.info("Identity created: %s", user_id)
        self._last_activity = time.monotonic()
        self._sidebar.show()
        self._sidebar.set_user_info(
            user_id,
            self._identity_manager.nickname or "",
        )
        self._stack.setCurrentIndex(ScreenIndex.CHAT_LIST)

        asyncio.create_task(self._finish_unlock())

    def _show_lock_screen(self) -> None:
        self._sidebar.hide()
        self._stack.setCurrentIndex(ScreenIndex.LOCK)
        self._unlock_password.clear()
        self._unlock_error.clear()

    async def _finish_unlock(self) -> None:
        from presidium.core.crypto import derive_session_key

        key = derive_session_key(
            self._identity_manager.key_pair.private_key_bytes, info=b"presidium-history-v1"
        )
        try:
            await self._db_manager.unlock_history(key)
            await self._start_networking()
        except Exception as exc:
            await self._lock_identity()
            self._unlock_error.setText(str(exc))
        finally:
            key.zero()

    async def _unlock_identity(self) -> None:
        if self._locking or not self._unlock_button.isEnabled():
            return
        self._unlock_button.setEnabled(False)
        password = self._unlock_password.text()
        self._unlock_password.clear()
        try:
            await self._identity_manager.unlock(password)
            await self._finish_unlock()
            if self._identity_manager.is_unlocked:
                self._last_activity = time.monotonic()
                self._sidebar.show()
                self._sidebar.set_user_info(
                    self._identity_manager.user_id, self._identity_manager.nickname or ""
                )
                self._stack.setCurrentIndex(ScreenIndex.CHAT_LIST)
        except Exception:
            self._unlock_error.setText("Could not unlock identity. Check the password.")
        finally:
            self._unlock_button.setEnabled(True)

    async def _lock_identity(self) -> None:
        if self._locking:
            return
        self._locking = True
        self._show_lock_screen()
        self._unlock_button.setEnabled(False)
        self._chat_manager = None
        self._stack.widget(ScreenIndex.CHAT_LIST).set_sessions([])
        self._stack.widget(ScreenIndex.CHAT_ROOM).clear_session()
        self._sidebar.set_user_info("", "")
        self._stack.widget(ScreenIndex.SETTINGS).set_user_info("", b"")
        try:
            if self._identity_manager:
                self._identity_manager.lock()
            if self._db_manager:
                self._db_manager.lock_history()
            for service in (self._discovery, self._dht, self._transport):
                if service:
                    await service.stop()
        finally:
            self._peer_manager = self._discovery = self._dht = self._transport = None
            self._locking = False
            self._unlock_button.setEnabled(True)

    @Slot(str)
    def _open_chat(self, peer_user_id: str) -> None:
        if not self._chat_manager or not self._identity_manager.is_unlocked:
            return
        session = self._chat_manager.get_session(peer_user_id)
        if session:
            self._stack.widget(ScreenIndex.CHAT_ROOM).set_session(session)
            self._stack.setCurrentIndex(ScreenIndex.CHAT_ROOM)
            asyncio.create_task(self._chat_manager.mark_as_read(peer_user_id))

    @Slot()
    def _new_chat(self) -> None:
        if not self._chat_manager or not self._peer_manager:
            return
        peers = self._peer_manager.get_all_peers()
        if not peers:
            QMessageBox.information(self, "New chat", "No peers discovered yet.")
            return
        choices = [f"{peer.nickname or 'Peer'} ({peer.user_id})" for peer in peers]
        choice, accepted = QInputDialog.getItem(self, "New chat", "Peer", choices, 0, False)
        if accepted:
            peer = peers[choices.index(choice)]
            self._chat_manager.get_or_create_session(peer.user_id, peer.nickname)
            self._stack.widget(ScreenIndex.CHAT_LIST).set_sessions(
                self._chat_manager.get_all_sessions()
            )
            self._open_chat(peer.user_id)

    async def _send_message(self, peer: str, text: str, reply: bytes) -> None:
        manager = self._chat_manager
        if manager is None or not self._identity_manager.is_unlocked:
            return
        try:
            await manager.send_message(peer, text, reply)
            if manager is self._chat_manager:
                self._stack.widget(ScreenIndex.CHAT_ROOM).set_session(manager.get_session(peer))
        except Exception as exc:
            QMessageBox.warning(self, "Message could not be sent", str(exc))

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
        self._chat_manager.set_on_message_status_changed(self._on_message_status_changed)
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
        if not self._identity_manager or not self._identity_manager.is_unlocked:
            return
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
        if self._chat_manager and self._identity_manager.is_unlocked:
            session = self._chat_manager.get_or_create_session(peer_user_id)
            await self._on_session_updated(session)

    async def _on_message_received(self, message: ChatMessage) -> None:
        """Handle incoming chat message — update UI."""
        # This runs in the async context; we need to update Qt widgets safely
        # SIMPLIFIED: in production, use signals/slots for thread-safe UI updates
        if not self._chat_manager or not self._identity_manager.is_unlocked:
            return
        screen = self._stack.widget(ScreenIndex.CHAT_ROOM)
        if screen.current_peer == message.sender_user_id:
            screen.add_message(message)

    async def _on_message_status_changed(self, message_id: bytes, status: MessageStatus) -> None:
        if self._chat_manager:
            self._stack.widget(ScreenIndex.CHAT_ROOM).update_message_status(
                message_id.hex(), status
            )

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
        timeout = self._config.ui.auto_lock_timeout
        if (
            timeout > 0
            and self._identity_manager
            and self._identity_manager.is_unlocked
            and time.monotonic() - self._last_activity >= timeout
        ):
            asyncio.create_task(self._lock_identity())

    def eventFilter(self, watched, event) -> bool:
        if event.type() in (
            QEvent.Type.KeyPress,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseMove,
            QEvent.Type.Wheel,
            QEvent.Type.TouchBegin,
        ):
            self._last_activity = time.monotonic()
        return super().eventFilter(watched, event)

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
            await self._lock_identity()
            if self._db_manager:
                await self._db_manager.close()
            if self._identity_manager:
                self._identity_manager.lock()
        except Exception as exc:
            logger.error("Cleanup error: %s", exc)

    async def _close_async(self) -> None:
        await self._cleanup_async()
        self._closing = True
        self.close()

    def closeEvent(self, event) -> None:
        if self._closing:
            QApplication.instance().removeEventFilter(self)
            event.accept()
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.cleanup()
            event.accept()
            return
        event.ignore()
        if self._close_task is None:
            self._close_task = loop.create_task(self._close_async())
