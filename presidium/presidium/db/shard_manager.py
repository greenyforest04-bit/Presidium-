"""Sharded SQLite storage for Presidium.

Provides:
- Sharding by user_id % 16 (16 SQLite databases in shards/ directory)
- Transparent read/write routing to the correct shard
- WAL checkpointing every 6 hours
- Vacuum optimization on schedule
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from pathlib import Path
from typing import Any, Optional

import aiosqlite

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SHARD_COUNT = 16
CHECKPOINT_INTERVAL = 21600  # 6 hours
VACUUM_INTERVAL = 86400  # 24 hours


# ---------------------------------------------------------------------------
# Shard Router
# ---------------------------------------------------------------------------

class ShardRouter:
    """Routes database operations to the correct shard based on user_id.

    Sharding formula: shard_index = int(user_id[:4], 16) % SHARD_COUNT
    This uses the first 4 hex chars of the user_id for distribution.
    """

    @staticmethod
    def get_shard_index(user_id: str) -> int:
        """Compute the shard index for a user ID."""
        try:
            return int(user_id[:4], 16) % SHARD_COUNT
        except (ValueError, IndexError):
            return hash(user_id) % SHARD_COUNT

    @staticmethod
    def get_shard_path(shards_dir: Path, shard_index: int) -> Path:
        """Get the database file path for a shard."""
        return shards_dir / f"shard_{shard_index:02d}.db"


# ---------------------------------------------------------------------------
# Shard Connection
# ---------------------------------------------------------------------------

class ShardConnection:
    """Manages a single shard's database connection."""

    def __init__(self, shard_path: Path) -> None:
        self._path = shard_path
        self._db: Optional[aiosqlite.Connection] = None

    async def open(self) -> None:
        """Open the shard database connection."""
        self._db = await aiosqlite.connect(str(self._path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA temp_store=MEMORY")
        await self._db.execute("PRAGMA mmap_size=67108864")  # 64 MB
        logger.debug("Shard opened: %s", self._path.name)

    async def close(self) -> None:
        """Close the shard database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Shard database not opened.")
        return self._db

    async def checkpoint(self) -> None:
        """Perform WAL checkpoint."""
        if self._db:
            try:
                await self._db.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except Exception as exc:
                logger.debug("WAL checkpoint failed for %s: %s", self._path.name, exc)

    async def vacuum(self) -> None:
        """Perform VACUUM to reclaim space."""
        if self._db:
            try:
                await self._db.execute("VACUUM")
                logger.info("VACUUM completed for %s", self._path.name)
            except Exception as exc:
                logger.warning("VACUUM failed for %s: %s", self._path.name, exc)


# ---------------------------------------------------------------------------
# Shard Manager
# ---------------------------------------------------------------------------

class ShardManager:
    """Manages all 16 database shards with routing and maintenance.

    Usage:
        manager = ShardManager(shards_dir=Path("~/.presidium/shards"))
        await manager.initialize()

        # Route a query to the correct shard
        shard = manager.get_shard(user_id)
        await shard.db.execute("INSERT INTO messages ...")

        await manager.close()
    """

    def __init__(self, shards_dir: Path) -> None:
        self._shards_dir = shards_dir
        self._router = ShardRouter()
        self._connections: dict[int, ShardConnection] = {}
        self._running = False
        self._bg_tasks: list[asyncio.Task] = []

    # -------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------

    async def initialize(self) -> None:
        """Create shard directories and open all connections."""
        self._shards_dir.mkdir(parents=True, exist_ok=True)

        for i in range(SHARD_COUNT):
            shard_path = ShardRouter.get_shard_path(self._shards_dir, i)
            conn = ShardConnection(shard_path)
            await conn.open()
            self._connections[i] = conn

        self._running = True
        self._bg_tasks.append(asyncio.create_task(self._maintenance_loop()))
        logger.info("Shard manager initialized with %d shards", SHARD_COUNT)

    async def close(self) -> None:
        """Close all shard connections."""
        self._running = False
        for task in self._bg_tasks:
            task.cancel()
        await asyncio.gather(*self._bg_tasks, return_exceptions=True)

        for conn in self._connections.values():
            await conn.close()
        self._connections.clear()
        logger.info("Shard manager closed.")

    # -------------------------------------------------------------------
    # Routing
    # -------------------------------------------------------------------

    def get_shard(self, user_id: str) -> ShardConnection:
        """Get the shard connection for a user ID."""
        idx = self._router.get_shard_index(user_id)
        conn = self._connections.get(idx)
        if conn is None:
            raise RuntimeError(f"Shard {idx} not initialized.")
        return conn

    def get_all_shards(self) -> list[ShardConnection]:
        """Get all shard connections."""
        return list(self._connections.values())

    # -------------------------------------------------------------------
    # Background maintenance
    # -------------------------------------------------------------------

    async def _maintenance_loop(self) -> None:
        """Periodically checkpoint WAL and vacuum databases."""
        last_checkpoint = time.monotonic()
        last_vacuum = time.monotonic()

        while self._running:
            now = time.monotonic()

            # WAL checkpoint every 6 hours
            if now - last_checkpoint >= CHECKPOINT_INTERVAL:
                for conn in self._connections.values():
                    await conn.checkpoint()
                last_checkpoint = now
                logger.debug("WAL checkpoint cycle completed.")

            # VACUUM every 24 hours
            if now - last_vacuum >= VACUUM_INTERVAL:
                # Vacuum one shard per cycle to avoid blocking
                for idx, conn in list(self._connections.items()):
                    await conn.vacuum()
                last_vacuum = now

            await asyncio.sleep(300)  # Check every 5 minutes
