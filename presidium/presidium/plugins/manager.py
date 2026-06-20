"""Plugin manager for Presidium.

Handles plugin discovery, loading, lifecycle management, and event
dispatching. The PluginManager is the central coordinator that connects
plugins to the Presidium core services.

Responsibilities:
    1. Discovery: Scan plugin directories for valid manifests
    2. Loading: Import and instantiate plugin classes
    3. Permission management: Track and enforce granted permissions
    4. Hook dispatch: Route events to registered plugin callbacks
    5. Storage: Provide per-plugin key-value storage
    6. Lifecycle: Start, stop, pause, unload plugins
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from presidium.plugins.base import (
    BasePlugin,
    HookCallback,
    PluginContext,
    PluginHook,
    PluginManifest,
    PluginPermission,
    PluginState,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Plugin manager
# ---------------------------------------------------------------------------

class PluginManager:
    """Central coordinator for the Presidium plugin system.

    Manages the full lifecycle of plugins from discovery to unloading,
    and dispatches hook events to registered callbacks.

    Usage:
        manager = PluginManager(
            plugin_dirs=[Path.home() / ".presidium" / "plugins"],
            data_dir=Path.home() / ".presidium" / "plugin_data",
        )
        await manager.discover()
        await manager.load_all()
        # ... later ...
        await manager.dispatch(PluginHook.MESSAGE_RECEIVED, {...})
        # ... on shutdown ...
        await manager.unload_all()
    """

    def __init__(
        self,
        plugin_dirs: Optional[list[Path]] = None,
        data_dir: Optional[Path] = None,
    ) -> None:
        self._plugin_dirs = plugin_dirs or [
            Path.home() / ".presidium" / "plugins",
        ]
        self._data_dir = data_dir or Path.home() / ".presidium" / "plugin_data"

        # Loaded plugins: plugin_id → BasePlugin instance
        self._plugins: dict[str, BasePlugin] = {}

        # Manifests: plugin_id → PluginManifest
        self._manifests: dict[str, PluginManifest] = {}

        # Hook registry: PluginHook → list of (plugin_id, callback)
        self._hook_registry: dict[PluginHook, list[tuple[str, HookCallback]]] = {
            hook: [] for hook in PluginHook
        }

        # Granted permissions: plugin_id → PluginPermission
        self._granted_permissions: dict[str, PluginPermission] = {}

        # Plugin storage: plugin_id → {key → value}
        self._storage: dict[str, dict[str, str]] = {}

        # Service callbacks (set by the application)
        self._send_message: Optional[Callable[[str, str], Awaitable[None]]] = None
        self._send_reaction: Optional[Callable[[str, str, str], Awaitable[None]]] = None
        self._show_notification: Optional[Callable[[str, str], Awaitable[None]]] = None
        self._http_get: Optional[Callable[[str], Awaitable[bytes]]] = None
        self._http_post: Optional[Callable[[str, bytes], Awaitable[bytes]]] = None

    # -------------------------------------------------------------------
    # Service registration (called by the application)
    # -------------------------------------------------------------------

    def register_send_message(
        self, callback: Callable[[str, str], Awaitable[None]]
    ) -> None:
        self._send_message = callback

    def register_send_reaction(
        self, callback: Callable[[str, str, str], Awaitable[None]]
    ) -> None:
        self._send_reaction = callback

    def register_show_notification(
        self, callback: Callable[[str, str], Awaitable[None]]
    ) -> None:
        self._show_notification = callback

    def register_http_get(
        self, callback: Callable[[str], Awaitable[bytes]]
    ) -> None:
        self._http_get = callback

    def register_http_post(
        self, callback: Callable[[str, bytes], Awaitable[bytes]]
    ) -> None:
        self._http_post = callback

    # -------------------------------------------------------------------
    # Discovery
    # -------------------------------------------------------------------

    async def discover(self) -> list[str]:
        """Scan plugin directories for valid plugin manifests.

        Returns:
            List of discovered plugin IDs.
        """
        discovered: list[str] = []

        for plugin_dir in self._plugin_dirs:
            if not plugin_dir.exists():
                continue

            for entry in plugin_dir.iterdir():
                if not entry.is_dir():
                    continue

                manifest_path = entry / "plugin.json"
                if not manifest_path.exists():
                    continue

                try:
                    manifest = await self._load_manifest(manifest_path)
                    if manifest and manifest.name:
                        self._manifests[manifest.name] = manifest
                        discovered.append(manifest.name)
                        logger.info(
                            "Discovered plugin: %s v%s at %s",
                            manifest.name, manifest.version, entry,
                        )
                except Exception as exc:
                    logger.error(
                        "Failed to load manifest from %s: %s",
                        manifest_path, exc,
                    )

        return discovered

    async def _load_manifest(self, path: Path) -> Optional[PluginManifest]:
        """Load and parse a plugin.json manifest file."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return PluginManifest.from_dict(data)
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Invalid manifest at %s: %s", path, exc)
            return None

    # -------------------------------------------------------------------
    # Loading / Unloading
    # -------------------------------------------------------------------

    async def load_plugin(self, plugin_id: str) -> bool:
        """Load a single plugin by its ID.

        Args:
            plugin_id: The plugin's unique name from its manifest.

        Returns:
            True if the plugin was loaded successfully.
        """
        manifest = self._manifests.get(plugin_id)
        if manifest is None:
            logger.error("No manifest found for plugin %s", plugin_id)
            return False

        # Find the plugin directory
        plugin_dir = self._find_plugin_dir(plugin_id)
        if plugin_dir is None:
            logger.error("Plugin directory not found for %s", plugin_id)
            return False

        try:
            # Import the plugin module
            plugin_instance = await self._import_plugin(plugin_dir, manifest)
            if plugin_instance is None:
                return False

            # Create context
            permissions = self._granted_permissions.get(
                plugin_id, PluginPermission.NONE
            )
            # Auto-grant requested permissions (in v1; future: prompt user)
            for perm in manifest.permissions:
                permissions |= perm
            self._granted_permissions[plugin_id] = permissions

            ctx = PluginContext(
                plugin_id=plugin_id,
                plugin_dir=str(plugin_dir),
                granted_permissions=permissions,
                _send_message=self._send_message,
                _send_reaction=self._send_reaction,
                _show_notification=self._show_notification,
                _store_get=self._make_store_get(plugin_id),
                _store_set=self._make_store_set(plugin_id),
                _http_get=self._http_get,
                _http_post=self._http_post,
            )

            # Initialize plugin
            plugin_instance._set_context(ctx)
            plugin_instance._set_manifest(manifest)
            plugin_instance._set_state(PluginState.LOADING)

            await plugin_instance.on_load(ctx)

            # Register hooks
            hook_callbacks = plugin_instance._get_hook_callbacks()
            for hook, callbacks in hook_callbacks.items():
                for cb in callbacks:
                    self._hook_registry[hook].append((plugin_id, cb))

            plugin_instance._set_state(PluginState.LOADED)
            self._plugins[plugin_id] = plugin_instance

            # Initialize storage
            if plugin_id not in self._storage:
                self._storage[plugin_id] = {}

            logger.info("Plugin %s loaded successfully", plugin_id)
            return True

        except Exception as exc:
            logger.error("Failed to load plugin %s: %s", plugin_id, exc, exc_info=exc)
            return False

    async def load_all(self) -> dict[str, bool]:
        """Load all discovered plugins.

        Returns:
            Dict mapping plugin_id → success (True/False).
        """
        results: dict[str, bool] = {}
        for plugin_id in self._manifests:
            results[plugin_id] = await self.load_plugin(plugin_id)
        return results

    async def unload_plugin(self, plugin_id: str) -> bool:
        """Unload a plugin and release its resources."""
        plugin = self._plugins.get(plugin_id)
        if plugin is None:
            return False

        try:
            # Remove hook registrations
            for hook in PluginHook:
                self._hook_registry[hook] = [
                    (pid, cb) for pid, cb in self._hook_registry[hook]
                    if pid != plugin_id
                ]

            # Call plugin's unload handler
            await plugin.on_unload()
            plugin._set_state(PluginState.UNLOADED)

            # Remove from registry
            del self._plugins[plugin_id]

            logger.info("Plugin %s unloaded", plugin_id)
            return True

        except Exception as exc:
            logger.error("Error unloading plugin %s: %s", plugin_id, exc)
            return False

    async def unload_all(self) -> None:
        """Unload all loaded plugins."""
        plugin_ids = list(self._plugins.keys())
        for plugin_id in plugin_ids:
            await self.unload_plugin(plugin_id)

    # -------------------------------------------------------------------
    # Start / Stop
    # -------------------------------------------------------------------

    async def start_plugin(self, plugin_id: str) -> bool:
        """Start a loaded plugin (transition to RUNNING)."""
        plugin = self._plugins.get(plugin_id)
        if plugin is None:
            return False

        try:
            await plugin.on_start()
            plugin._set_state(PluginState.RUNNING)
            return True
        except Exception as exc:
            plugin._set_state(PluginState.ERROR)
            await plugin.on_error(exc)
            return False

    async def stop_plugin(self, plugin_id: str) -> bool:
        """Stop a running plugin (transition to PAUSED)."""
        plugin = self._plugins.get(plugin_id)
        if plugin is None:
            return False

        try:
            await plugin.on_stop()
            plugin._set_state(PluginState.PAUSED)
            return True
        except Exception as exc:
            plugin._set_state(PluginState.ERROR)
            await plugin.on_error(exc)
            return False

    # -------------------------------------------------------------------
    # Hook dispatch
    # -------------------------------------------------------------------

    async def dispatch(self, hook: PluginHook, data: dict[str, Any]) -> None:
        """Dispatch a hook event to all registered plugins.

        Callbacks are invoked in registration order. If a callback
        raises an exception, it is logged but does not prevent other
        callbacks from being called.

        Args:
            hook: The hook event type.
            data: Event data dict (hook-specific).
        """
        callbacks = self._hook_registry.get(hook, [])
        if not callbacks:
            return

        for plugin_id, callback in callbacks:
            try:
                await callback(hook, data)
            except Exception as exc:
                logger.error(
                    "Hook callback error in plugin %s for %s: %s",
                    plugin_id, hook.value, exc,
                )
                plugin = self._plugins.get(plugin_id)
                if plugin:
                    await plugin.on_error(exc)

    # -------------------------------------------------------------------
    # Plugin import
    # -------------------------------------------------------------------

    async def _import_plugin(
        self,
        plugin_dir: Path,
        manifest: PluginManifest,
    ) -> Optional[BasePlugin]:
        """Import a plugin module and instantiate the plugin class.

        Args:
            plugin_dir: Path to the plugin directory.
            manifest: The plugin's manifest.

        Returns:
            An instance of the plugin class, or None on failure.
        """
        entry_point = manifest.entry_point  # e.g. "main:Plugin"
        if ":" not in entry_point:
            logger.error(
                "Invalid entry point format '%s' for plugin %s. "
                "Expected 'module:ClassName'.",
                entry_point, manifest.name,
            )
            return None

        module_name, class_name = entry_point.split(":", 1)
        module_path = plugin_dir / f"{module_name}.py"

        if not module_path.exists():
            logger.error(
                "Module %s not found at %s for plugin %s",
                module_name, module_path, manifest.name,
            )
            return None

        try:
            # Add plugin dir to sys.path temporarily
            plugin_dir_str = str(plugin_dir)
            if plugin_dir_str not in sys.path:
                sys.path.insert(0, plugin_dir_str)

            spec = importlib.util.spec_from_file_location(
                f"presidium.plugins.user.{manifest.name}",
                str(module_path),
            )
            if spec is None or spec.loader is None:
                logger.error("Cannot create module spec for %s", module_path)
                return None

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            plugin_class = getattr(module, class_name, None)
            if plugin_class is None:
                logger.error(
                    "Class %s not found in module %s for plugin %s",
                    class_name, module_name, manifest.name,
                )
                return None

            if not issubclass(plugin_class, BasePlugin):
                logger.error(
                    "Class %s does not extend BasePlugin for plugin %s",
                    class_name, manifest.name,
                )
                return None

            return plugin_class()

        except Exception as exc:
            logger.error(
                "Failed to import plugin %s: %s", manifest.name, exc,
                exc_info=exc,
            )
            return None

    # -------------------------------------------------------------------
    # Storage helpers
    # -------------------------------------------------------------------

    def _make_store_get(
        self, plugin_id: str
    ) -> Callable[[str], Awaitable[Optional[str]]]:
        """Create a store_get function bound to a plugin's storage."""
        async def store_get(key: str) -> Optional[str]:
            return self._storage.get(plugin_id, {}).get(key)
        return store_get

    def _make_store_set(
        self, plugin_id: str
    ) -> Callable[[str, str], Awaitable[None]]:
        """Create a store_set function bound to a plugin's storage."""
        async def store_set(key: str, value: str) -> None:
            if plugin_id not in self._storage:
                self._storage[plugin_id] = {}
            self._storage[plugin_id][key] = value
        return store_set

    # -------------------------------------------------------------------
    # Plugin directory resolution
    # -------------------------------------------------------------------

    def _find_plugin_dir(self, plugin_id: str) -> Optional[Path]:
        """Find the directory for a plugin by its ID."""
        for plugin_dir in self._plugin_dirs:
            candidate = plugin_dir / plugin_id
            if candidate.is_dir():
                return candidate
        return None

    # -------------------------------------------------------------------
    # Query methods
    # -------------------------------------------------------------------

    def get_plugin(self, plugin_id: str) -> Optional[BasePlugin]:
        """Get a loaded plugin by its ID."""
        return self._plugins.get(plugin_id)

    def get_all_plugins(self) -> dict[str, BasePlugin]:
        """Get all loaded plugins."""
        return dict(self._plugins)

    def get_plugin_state(self, plugin_id: str) -> Optional[PluginState]:
        """Get the state of a plugin."""
        plugin = self._plugins.get(plugin_id)
        return plugin.state if plugin else None

    def is_plugin_loaded(self, plugin_id: str) -> bool:
        """Check if a plugin is loaded."""
        return plugin_id in self._plugins

    def list_discovered(self) -> list[str]:
        """List all discovered plugin IDs."""
        return list(self._manifests.keys())

    def list_loaded(self) -> list[str]:
        """List all loaded plugin IDs."""
        return list(self._plugins.keys())
