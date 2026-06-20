"""Base plugin API for Presidium.

Defines the abstract interface that all plugins must implement, along
with supporting types for manifests, hooks, permissions, and context.

Design goals:
    - Minimal API surface: plugins only implement what they need
    - Event-driven: plugins react to hooks, not poll
    - Safe: permissions model prevents unauthorized access
    - Discoverable: manifests declare metadata and capabilities
    - Isolated: each plugin gets its own context and storage

Plugin development guide:
    1. Create a directory under ~/.presidium/plugins/my_plugin/
    2. Create a plugin.json manifest file
    3. Create a main.py with a class extending BasePlugin
    4. Implement on_load() to register hooks
    5. Implement hook handlers for the events you care about
"""

from __future__ import annotations

import abc
import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional


# ---------------------------------------------------------------------------
# Plugin permissions
# ---------------------------------------------------------------------------

class PluginPermission(enum.Flag):
    """Permissions a plugin can request.

    Plugins must declare required permissions in their manifest.
    The user must approve each permission before the plugin can use it.
    """
    NONE = 0

    # Message access
    READ_MESSAGES = auto()        # Read incoming/outgoing messages
    SEND_MESSAGES = auto()        # Send messages on behalf of the user
    REACT_MESSAGES = auto()       # Add reactions to messages

    # Contact access
    READ_CONTACTS = auto()        # Read contact list
    MODIFY_CONTACTS = auto()      # Add/remove contacts

    # Network access
    NETWORK_ACCESS = auto()       # Make outbound HTTP requests
    DHT_ACCESS = auto()           # Read/write to the DHT

    # Storage access
    LOCAL_STORAGE = auto()        # Read/write plugin-specific local storage
    READ_FILES = auto()           # Read files from file transfers

    # UI access
    SHOW_NOTIFICATIONS = auto()   # Display notifications to the user
    ADD_UI_ELEMENTS = auto()     # Add buttons, menus, or panels to the UI
    SHOW_DIALOGS = auto()        # Show modal/non-modal dialogs

    # System access
    RUN_BACKGROUND_TASKS = auto() # Run async tasks in the background
    ACCESS_CLIPBOARD = auto()     # Read/write system clipboard


# ---------------------------------------------------------------------------
# Plugin hooks (events plugins can subscribe to)
# ---------------------------------------------------------------------------

class PluginHook(enum.Enum):
    """Events that plugins can subscribe to via the hook system."""
    # Message lifecycle
    MESSAGE_RECEIVED = "message_received"          # New incoming message
    MESSAGE_SENT = "message_sent"                  # Message successfully sent
    MESSAGE_SENDING = "message_sending"            # About to send (can modify)
    MESSAGE_REACTION = "message_reaction"          # Reaction added/removed

    # Chat lifecycle
    CHAT_CREATED = "chat_created"                  # New chat session
    CHAT_OPENED = "chat_opened"                    # Chat screen opened
    CHAT_CLOSED = "chat_closed"                    # Chat screen closed

    # Contact lifecycle
    CONTACT_ADDED = "contact_added"                # New contact added
    CONTACT_REMOVED = "contact_removed"            # Contact removed
    CONTACT_ONLINE = "contact_online"              # Contact came online
    CONTACT_OFFLINE = "contact_offline"            # Contact went offline

    # Typing
    TYPING_START = "typing_start"                  # Peer started typing
    TYPING_STOP = "typing_stop"                    # Peer stopped typing

    # File transfer
    FILE_RECEIVED = "file_received"                # File transfer received
    FILE_SENT = "file_sent"                        # File transfer sent

    # Voice
    CALL_INCOMING = "call_incoming"                # Incoming voice call
    CALL_STARTED = "call_started"                  # Voice call started
    CALL_ENDED = "call_ended"                      # Voice call ended

    # Application
    APP_STARTED = "app_started"                    # Application started
    APP_CLOSING = "app_closing"                    # Application about to close
    PLUGIN_LOADED = "plugin_loaded"                # Another plugin was loaded
    PLUGIN_UNLOADED = "plugin_unloaded"            # Another plugin was unloaded


# ---------------------------------------------------------------------------
# Plugin manifest
# ---------------------------------------------------------------------------

@dataclass
class PluginManifest:
    """Metadata and capabilities declaration for a plugin.

    Loaded from a plugin.json file in the plugin directory.

    Required fields:
        - name: Unique plugin identifier (lowercase, no spaces)
        - version: Semantic version string (e.g. "1.0.0")
        - description: One-line description
        - author: Author name or handle

    Optional fields:
        - permissions: List of required permissions
        - hooks: List of hooks this plugin subscribes to
        - min_app_version: Minimum Presidium version required
        - homepage: URL to plugin homepage/docs
        - icon: Path to icon file (relative to plugin dir)
    """
    name: str = ""
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    permissions: list[PluginPermission] = field(default_factory=list)
    hooks: list[PluginHook] = field(default_factory=list)
    min_app_version: str = ""
    homepage: str = ""
    icon: str = ""
    entry_point: str = "main:Plugin"  # module:ClassName

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PluginManifest:
        """Create a manifest from a parsed JSON dict."""
        permissions = []
        for perm_str in data.get("permissions", []):
            try:
                permissions.append(PluginPermission[perm_str])
            except KeyError:
                pass  # Skip unknown permissions

        hooks = []
        for hook_str in data.get("hooks", []):
            try:
                hooks.append(PluginHook(hook_str))
            except ValueError:
                pass

        return cls(
            name=data.get("name", ""),
            version=data.get("version", "0.1.0"),
            description=data.get("description", ""),
            author=data.get("author", ""),
            permissions=permissions,
            hooks=hooks,
            min_app_version=data.get("min_app_version", ""),
            homepage=data.get("homepage", ""),
            icon=data.get("icon", ""),
            entry_point=data.get("entry_point", "main:Plugin"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the manifest to a JSON-compatible dict."""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "permissions": [p.name for p in self.permissions],
            "hooks": [h.value for h in self.hooks],
            "min_app_version": self.min_app_version,
            "homepage": self.homepage,
            "icon": self.icon,
            "entry_point": self.entry_point,
        }


# ---------------------------------------------------------------------------
# Plugin state
# ---------------------------------------------------------------------------

class PluginState(enum.IntEnum):
    """Runtime state of a plugin instance."""
    DISCOVERED = 0   # Manifest found, not yet loaded
    LOADING = 1       # Currently loading
    LOADED = 2        # Loaded, hooks registered
    RUNNING = 3       # Actively processing events
    PAUSED = 4        # Temporarily paused
    ERROR = 5         # Error state, not running
    UNLOADED = 6      # Unloaded, resources released


# ---------------------------------------------------------------------------
# Plugin context
# ---------------------------------------------------------------------------

@dataclass
class PluginContext:
    """Execution context provided to each plugin.

    Gives plugins access to Presidium's services through a controlled
    interface that respects the plugin's granted permissions.
    """
    plugin_id: str
    plugin_dir: str                # Absolute path to plugin directory
    granted_permissions: PluginPermission = PluginPermission.NONE

    # Service access (set by PluginManager)
    _send_message: Optional[Callable[[str, str], Awaitable[None]]] = None
    _send_reaction: Optional[Callable[[str, str, str], Awaitable[None]]] = None
    _show_notification: Optional[Callable[[str, str], Awaitable[None]]] = None
    _store_get: Optional[Callable[[str], Awaitable[Optional[str]]]] = None
    _store_set: Optional[Callable[[str, str], Awaitable[None]]] = None
    _http_get: Optional[Callable[[str], Awaitable[bytes]]] = None
    _http_post: Optional[Callable[[str, bytes], Awaitable[bytes]]] = None

    def _require_permission(self, perm: PluginPermission) -> None:
        """Check that a permission has been granted, raise if not."""
        if not (self.granted_permissions & perm):
            raise PermissionError(
                f"Plugin {self.plugin_id} requires {perm.name} permission."
            )

    async def send_message(self, peer_id: str, text: str) -> None:
        """Send a message on behalf of the user."""
        self._require_permission(PluginPermission.SEND_MESSAGES)
        if self._send_message:
            await self._send_message(peer_id, text)

    async def send_reaction(self, peer_id: str, message_id: str, emoji: str) -> None:
        """Add a reaction to a message."""
        self._require_permission(PluginPermission.REACT_MESSAGES)
        if self._send_reaction:
            await self._send_reaction(peer_id, message_id, emoji)

    async def show_notification(self, title: str, body: str) -> None:
        """Display a notification to the user."""
        self._require_permission(PluginPermission.SHOW_NOTIFICATIONS)
        if self._show_notification:
            await self._show_notification(title, body)

    async def store_get(self, key: str) -> Optional[str]:
        """Get a value from the plugin's local storage."""
        self._require_permission(PluginPermission.LOCAL_STORAGE)
        if self._store_get:
            return await self._store_get(key)
        return None

    async def store_set(self, key: str, value: str) -> None:
        """Set a value in the plugin's local storage."""
        self._require_permission(PluginPermission.LOCAL_STORAGE)
        if self._store_set:
            await self._store_set(key, value)

    async def http_get(self, url: str) -> bytes:
        """Make an HTTP GET request."""
        self._require_permission(PluginPermission.NETWORK_ACCESS)
        if self._http_get:
            return await self._http_get(url)
        raise RuntimeError("Network access not available.")

    async def http_post(self, url: str, data: bytes) -> bytes:
        """Make an HTTP POST request."""
        self._require_permission(PluginPermission.NETWORK_ACCESS)
        if self._http_post:
            return self._http_post(url, data)
        raise RuntimeError("Network access not available.")


# ---------------------------------------------------------------------------
# Hook callback type
# ---------------------------------------------------------------------------

HookCallback = Callable[[PluginHook, dict[str, Any]], Awaitable[None]]


# ---------------------------------------------------------------------------
# Abstract base plugin
# ---------------------------------------------------------------------------

class BasePlugin(abc.ABC):
    """Abstract base class for all Presidium plugins.

    Every plugin must implement at minimum:
        - on_load(): Called when the plugin is loaded. Register hooks here.
        - on_unload(): Called when the plugin is unloaded. Clean up here.

    Optional lifecycle methods:
        - on_start(): Called when the plugin transitions to RUNNING state.
        - on_stop(): Called when the plugin is paused or stopped.
        - on_error(error): Called when an unhandled error occurs.

    Hook handling:
        Plugins register callbacks for specific hooks during on_load().
        When a hook event fires, the plugin manager invokes all registered
        callbacks in order. Callbacks can be async.

    Example minimal plugin:

        class HelloPlugin(BasePlugin):
            async def on_load(self, ctx: PluginContext) -> None:
                self._ctx = ctx
                self.register_hook(PluginHook.MESSAGE_RECEIVED, self._on_message)

            async def _on_message(self, hook: PluginHook, data: dict) -> None:
                await self._ctx.send_message(
                    data["peer_id"], "Hello from the plugin!"
                )

            async def on_unload(self) -> None:
                pass
    """

    def __init__(self) -> None:
        self._context: Optional[PluginContext] = None
        self._state = PluginState.DISCOVERED
        self._manifest: Optional[PluginManifest] = None
        self._hook_callbacks: dict[PluginHook, list[HookCallback]] = {}
        self._background_tasks: list[asyncio.Task[None]] = []

    # -------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------

    @property
    def plugin_id(self) -> str:
        """Unique identifier for this plugin instance."""
        if self._context:
            return self._context.plugin_id
        return ""

    @property
    def state(self) -> PluginState:
        """Current plugin state."""
        return self._state

    @property
    def manifest(self) -> Optional[PluginManifest]:
        """Plugin manifest metadata."""
        return self._manifest

    # -------------------------------------------------------------------
    # Abstract lifecycle methods
    # -------------------------------------------------------------------

    @abc.abstractmethod
    async def on_load(self, ctx: PluginContext) -> None:
        """Called when the plugin is loaded.

        Use this method to:
        - Store the context for later use
        - Register hook callbacks
        - Initialize resources
        - Load settings from local storage

        Args:
            ctx: Execution context with service access.
        """

    @abc.abstractmethod
    async def on_unload(self) -> None:
        """Called when the plugin is being unloaded.

        Use this method to:
        - Cancel background tasks
        - Save settings to local storage
        - Release resources
        - Unregister hooks (handled automatically by PluginManager)
        """

    # -------------------------------------------------------------------
    # Optional lifecycle methods
    # -------------------------------------------------------------------

    async def on_start(self) -> None:
        """Called when the plugin transitions to RUNNING state.

        Override to start background processing.
        """

    async def on_stop(self) -> None:
        """Called when the plugin is paused or stopped.

        Override to pause background processing.
        """

    async def on_error(self, error: Exception) -> None:
        """Called when an unhandled error occurs in the plugin.

        Override to implement custom error handling/logging.
        The default implementation logs the error.
        """
        import logging
        logging.getLogger(f"presidium.plugin.{self.plugin_id}").error(
            "Plugin error: %s", error, exc_info=error
        )

    # -------------------------------------------------------------------
    # Hook registration
    # -------------------------------------------------------------------

    def register_hook(self, hook: PluginHook, callback: HookCallback) -> None:
        """Register a callback for a specific hook event.

        Args:
            hook: The hook event to subscribe to.
            callback: Async function called when the event fires.
        """
        if hook not in self._hook_callbacks:
            self._hook_callbacks[hook] = []
        self._hook_callbacks[hook].append(callback)

    def unregister_hook(self, hook: PluginHook, callback: HookCallback) -> None:
        """Unregister a specific callback for a hook event."""
        if hook in self._hook_callbacks:
            try:
                self._hook_callbacks[hook].remove(callback)
            except ValueError:
                pass

    # -------------------------------------------------------------------
    # Internal methods (called by PluginManager)
    # -------------------------------------------------------------------

    def _set_context(self, ctx: PluginContext) -> None:
        self._context = ctx

    def _set_manifest(self, manifest: PluginManifest) -> None:
        self._manifest = manifest

    def _set_state(self, state: PluginState) -> None:
        self._state = state

    def _get_hook_callbacks(self) -> dict[PluginHook, list[HookCallback]]:
        """Return all registered hook callbacks."""
        return dict(self._hook_callbacks)


# Import asyncio for type hints in BasePlugin
import asyncio
