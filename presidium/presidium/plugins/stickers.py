"""Stickers plugin for Presidium.

Provides sticker pack management and sticker message sending/receiving.
Sticker packs are collections of sticker images that users can browse
and send in chats, similar to Telegram/Signal sticker functionality.

Features:
    - Install and manage multiple sticker packs
    - Browse stickers in a grid UI
    - Send stickers as special message type
    - Receive and display stickers from peers
    - Import sticker packs from URL (ZIP format)
    - Create custom sticker packs from local images
    - Animated sticker support (WebP/GIF)

Sticker pack format:
    Each pack is a directory containing:
    - pack.json: Metadata (name, author, stickers list)
    - Individual sticker image files (PNG/WebP)

    pack.json schema:
    {
        "name": "My Sticker Pack",
        "author": "artist",
        "version": "1.0",
        "stickers": [
            {"id": "01", "file": "01.webp", "emoji": "😀", "keywords": ["happy", "smile"]},
            ...
        ]
    }
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import zipfile
from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass, field

from presidium.plugins.base import (
    BasePlugin,
    PluginContext,
    PluginHook,
    PluginPermission,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sticker data models
# ---------------------------------------------------------------------------

@dataclass
class Sticker:
    """A single sticker within a pack."""
    sticker_id: str = ""
    file_path: str = ""       # Relative path within pack directory
    emoji: str = ""           # Primary emoji representation
    keywords: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Sticker:
        return cls(
            sticker_id=data.get("id", ""),
            file_path=data.get("file", ""),
            emoji=data.get("emoji", ""),
            keywords=data.get("keywords", []),
        )


@dataclass
class StickerPack:
    """A collection of stickers."""
    pack_id: str = ""
    name: str = ""
    author: str = ""
    version: str = "1.0"
    directory: str = ""       # Absolute path to pack directory
    stickers: list[Sticker] = field(default_factory=list)
    is_installed: bool = False
    is_animated: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any], directory: str = "") -> StickerPack:
        stickers = [Sticker.from_dict(s) for s in data.get("stickers", [])]
        return cls(
            pack_id=data.get("pack_id", data.get("name", "").lower().replace(" ", "_")),
            name=data.get("name", ""),
            author=data.get("author", ""),
            version=data.get("version", "1.0"),
            directory=directory,
            stickers=stickers,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack_id": self.pack_id,
            "name": self.name,
            "author": self.author,
            "version": self.version,
            "stickers": [
                {
                    "id": s.sticker_id,
                    "file": s.file_path,
                    "emoji": s.emoji,
                    "keywords": s.keywords,
                }
                for s in self.stickers
            ],
        }

    def get_sticker_path(self, sticker: Sticker) -> Path:
        """Get the absolute file path for a sticker."""
        return Path(self.directory) / sticker.file_path

    def find_by_emoji(self, emoji: str) -> list[Sticker]:
        """Find stickers matching an emoji."""
        return [s for s in self.stickers if s.emoji == emoji]

    def search(self, query: str) -> list[Sticker]:
        """Search stickers by keyword or emoji."""
        query_lower = query.lower()
        results: list[Sticker] = []
        for s in self.stickers:
            if query_lower in s.emoji.lower():
                results.append(s)
            elif any(query_lower in kw.lower() for kw in s.keywords):
                results.append(s)
        return results


# ---------------------------------------------------------------------------
# Sticker pack manager
# ---------------------------------------------------------------------------

class StickerPackManager:
    """Manages sticker pack installation, storage, and lookup."""

    def __init__(self, stickers_dir: Path) -> None:
        self._stickers_dir = stickers_dir
        self._packs: dict[str, StickerPack] = {}  # pack_id → StickerPack

    async def initialize(self) -> None:
        """Scan the stickers directory and load all installed packs."""
        self._stickers_dir.mkdir(parents=True, exist_ok=True)

        for entry in self._stickers_dir.iterdir():
            if not entry.is_dir():
                continue
            pack_file = entry / "pack.json"
            if pack_file.exists():
                try:
                    pack = await self._load_pack(pack_file, str(entry))
                    if pack:
                        self._packs[pack.pack_id] = pack
                        logger.info("Loaded sticker pack: %s (%d stickers)",
                                    pack.name, len(pack.stickers))
                except Exception as exc:
                    logger.error("Failed to load sticker pack from %s: %s",
                                 entry, exc)

    async def _load_pack(self, pack_file: Path, directory: str) -> Optional[StickerPack]:
        """Load a sticker pack from its pack.json file."""
        with open(pack_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        pack = StickerPack.from_dict(data, directory)
        pack.is_installed = True
        return pack

    async def install_from_directory(self, source_dir: Path) -> Optional[StickerPack]:
        """Install a sticker pack from a local directory.

        Copies the directory into the stickers storage area.
        """
        pack_file = source_dir / "pack.json"
        if not pack_file.exists():
            logger.error("No pack.json found in %s", source_dir)
            return None

        with open(pack_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        pack = StickerPack.from_dict(data)
        if not pack.pack_id:
            logger.error("Sticker pack missing pack_id or name")
            return None

        # Copy to stickers directory
        dest_dir = self._stickers_dir / pack.pack_id
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        shutil.copytree(str(source_dir), str(dest_dir))

        # Load from new location
        pack.directory = str(dest_dir)
        pack.is_installed = True
        self._packs[pack.pack_id] = pack

        logger.info("Installed sticker pack: %s → %s", pack.name, dest_dir)
        return pack

    async def install_from_zip(self, zip_path: Path) -> Optional[StickerPack]:
        """Install a sticker pack from a ZIP file.

        The ZIP must contain a pack.json at its root.
        """
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            with zipfile.ZipFile(str(zip_path), "r") as zf:
                zf.extractall(tmp_dir)

            # Find pack.json
            tmp_path = Path(tmp_dir)
            pack_file = tmp_path / "pack.json"
            if not pack_file.exists():
                # Check if it's in a subdirectory
                for sub in tmp_path.iterdir():
                    if sub.is_dir() and (sub / "pack.json").exists():
                        return await self.install_from_directory(sub)
                logger.error("No pack.json found in ZIP %s", zip_path)
                return None

            return await self.install_from_directory(tmp_path)

    async def install_from_url(self, url: str) -> Optional[StickerPack]:
        """Download and install a sticker pack from a URL."""
        import tempfile
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.error("Failed to download sticker pack from %s: HTTP %d",
                                 url, resp.status)
                    return None
                data = await resp.read()

        # Save to temp file and install
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)

        try:
            return await self.install_from_zip(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

    async def uninstall_pack(self, pack_id: str) -> bool:
        """Remove an installed sticker pack."""
        pack = self._packs.get(pack_id)
        if pack is None:
            return False

        # Remove directory
        if pack.directory and Path(pack.directory).exists():
            shutil.rmtree(pack.directory)

        del self._packs[pack_id]
        logger.info("Uninstalled sticker pack: %s", pack_id)
        return True

    # -------------------------------------------------------------------
    # Query methods
    # -------------------------------------------------------------------

    def get_pack(self, pack_id: str) -> Optional[StickerPack]:
        """Get a sticker pack by ID."""
        return self._packs.get(pack_id)

    def get_all_packs(self) -> list[StickerPack]:
        """Get all installed sticker packs."""
        return list(self._packs.values())

    def search_stickers(self, query: str) -> list[tuple[StickerPack, Sticker]]:
        """Search across all packs for stickers matching a query.

        Returns:
            List of (pack, sticker) tuples.
        """
        results: list[tuple[StickerPack, Sticker]] = []
        for pack in self._packs.values():
            for sticker in pack.search(query):
                results.append((pack, sticker))
        return results

    def get_recent_stickers(self, limit: int = 20) -> list[tuple[StickerPack, Sticker]]:
        """Get recently used stickers.

        Returns stickers from the most recently used packs.
        """
        # Simplified: just return first N stickers from first few packs
        results: list[tuple[StickerPack, Sticker]] = []
        for pack in self._packs.values():
            for sticker in pack.stickers[:5]:
                results.append((pack, sticker))
                if len(results) >= limit:
                    return results
        return results


# ---------------------------------------------------------------------------
# Stickers plugin
# ---------------------------------------------------------------------------

class StickersPlugin(BasePlugin):
    """Built-in stickers plugin for Presidium.

    Provides sticker pack management and sticker sending/receiving.
    Integrates with the chat UI to add a sticker picker panel.
    """

    def __init__(self) -> None:
        super().__init__()
        self._pack_manager: Optional[StickerPackManager] = None

    async def on_load(self, ctx: PluginContext) -> None:
        """Initialize the stickers plugin."""
        self._context = ctx  # type: ignore[assignment]

        # Set up sticker storage directory
        stickers_dir = Path(ctx.plugin_dir) / "stickers_data"
        self._pack_manager = StickerPackManager(stickers_dir)
        await self._pack_manager.initialize()

        # Register hooks
        self.register_hook(PluginHook.MESSAGE_RECEIVED, self._on_message)
        self.register_hook(PluginHook.CHAT_OPENED, self._on_chat_opened)

        logger.info("Stickers plugin loaded with %d packs",
                     len(self._pack_manager.get_all_packs()))

    async def on_unload(self) -> None:
        """Clean up the stickers plugin."""
        self._pack_manager = None

    async def _on_message(self, hook: PluginHook, data: dict[str, Any]) -> None:
        """Handle incoming messages that contain stickers."""
        # Check if message contains sticker data
        content = data.get("content", "")
        if content.startswith("[sticker:"):
            # Parse sticker reference: [sticker:pack_id:sticker_id]
            parts = content[9:].rstrip("]").split(":")
            if len(parts) == 2:
                pack_id, sticker_id = parts
                if self._pack_manager:
                    pack = self._pack_manager.get_pack(pack_id)
                    if pack:
                        # Found the sticker — UI will render it
                        logger.debug("Received sticker %s from pack %s",
                                     sticker_id, pack_id)

    async def _on_chat_opened(self, hook: PluginHook, data: dict[str, Any]) -> None:
        """When a chat is opened, the sticker picker becomes available."""
        # In a full implementation, this would add a sticker button
        # to the chat input bar
        pass

    # -------------------------------------------------------------------
    # Public API (for other plugins or UI)
    # -------------------------------------------------------------------

    async def send_sticker(self, peer_id: str, pack_id: str, sticker_id: str) -> None:
        """Send a sticker to a peer.

        Args:
            peer_id: The recipient's user ID.
            pack_id: The sticker pack ID.
            sticker_id: The sticker ID within the pack.
        """
        if not self._context:
            return
        await self._context.send_message(
            peer_id, f"[sticker:{pack_id}:{sticker_id}]"
        )

    def get_packs(self) -> list[StickerPack]:
        """Get all installed sticker packs."""
        if self._pack_manager:
            return self._pack_manager.get_all_packs()
        return []

    def search(self, query: str) -> list[tuple[StickerPack, Sticker]]:
        """Search for stickers by keyword or emoji."""
        if self._pack_manager:
            return self._pack_manager.search_stickers(query)
        return []
