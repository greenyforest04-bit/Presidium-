"""Plugin system for Presidium.

Provides an extensible plugin architecture that allows third-party
developers to extend Presidium's functionality without modifying core code.

Built-in plugins:
    - StickersPlugin: Sticker pack management and sending
    - BotApiPlugin: Bot framework for automated interactions
    - ScheduledMessagesPlugin: Schedule messages for future delivery

Plugin lifecycle:
    1. Discovery: Scan plugin directories for manifest files
    2. Loading: Import plugin module, instantiate plugin class
    3. Registration: Register hooks, commands, and UI elements
    4. Running: Plugin receives events and can act on them
    5. Unloading: Clean up resources, unregister hooks

Security model:
    - Plugins run in the same process (no sandboxing in v1)
    - Plugins cannot access private keys directly
    - Plugins must declare required permissions in manifest
    - User must approve permissions on first use
"""

from presidium.plugins.base import (
    BasePlugin,
    PluginManifest,
    PluginState,
    PluginHook,
    PluginContext,
    PluginPermission,
)
from presidium.plugins.manager import PluginManager
from presidium.plugins.stickers import StickersPlugin
from presidium.plugins.bot_api import BotApiPlugin
from presidium.plugins.scheduled_messages import ScheduledMessagesPlugin

__all__ = [
    "BasePlugin",
    "PluginManifest",
    "PluginState",
    "PluginHook",
    "PluginContext",
    "PluginPermission",
    "PluginManager",
    "StickersPlugin",
    "BotApiPlugin",
    "ScheduledMessagesPlugin",
]
