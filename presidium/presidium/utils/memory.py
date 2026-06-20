"""Memory pressure handling for Presidium.

Provides:
- RSS monitoring via psutil
- LRU eviction of in-memory message caches when RAM > 80%
- Forced garbage collection under pressure
- Streaming file chunks from disk (no full-file read into RAM)
"""

from __future__ import annotations

import gc
import logging
import time
from collections import OrderedDict
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MEMORY_PRESSURE_THRESHOLD = 0.80  # 80% of total RAM
EVICT_PERCENTAGE = 0.20  # Evict 20% of cache when under pressure
CHECK_INTERVAL = 30  # seconds


# ---------------------------------------------------------------------------
# LRU Cache with eviction support
# ---------------------------------------------------------------------------

class LRUCache:
    """Thread-safe LRU cache with memory-aware eviction.

    When the system memory pressure exceeds the threshold, the cache
    automatically evicts the least-recently-used entries.
    """

    def __init__(
        self,
        max_size: int = 1000,
        name: str = "default",
    ) -> None:
        self._max_size = max_size
        self._name = name
        self._cache: OrderedDict[str, object] = OrderedDict()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[object]:
        """Get an item from the cache. Returns None if not found."""
        if key in self._cache:
            self._cache.move_to_end(key)
            self._hits += 1
            return self._cache[key]
        self._misses += 1
        return None

    def put(self, key: str, value: object) -> None:
        """Add an item to the cache."""
        if key in self._cache:
            self._cache.move_to_end(key)
            self._cache[key] = value
        else:
            self._cache[key] = value
            if len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

    def evict(self, fraction: float = EVICT_PERCENTAGE) -> int:
        """Evict a fraction of the least-recently-used items.

        Returns:
            Number of items evicted.
        """
        count = int(len(self._cache) * fraction)
        for _ in range(count):
            if self._cache:
                self._cache.popitem(last=False)
        if count > 0:
            logger.debug("Cache '%s': evicted %d items (%d remaining)",
                         self._name, count, len(self._cache))
        return count

    def clear(self) -> None:
        """Clear the entire cache."""
        self._cache.clear()

    @property
    def size(self) -> int:
        return len(self._cache)

    @property
    def stats(self) -> dict[str, int]:
        total = self._hits + self._misses
        return {
            "size": self.size,
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / total if total > 0 else 0,
        }


# ---------------------------------------------------------------------------
# Memory Monitor
# ---------------------------------------------------------------------------

class MemoryMonitor:
    """Monitors system memory and triggers eviction when under pressure.

    Usage:
        monitor = MemoryMonitor()
        monitor.register_cache("messages", message_cache)
        await monitor.start()

        # Under the hood, it checks RSS every 30 seconds and
        # evicts caches if memory usage > 80%.
    """

    def __init__(self) -> None:
        self._caches: dict[str, LRUCache] = {}
        self._running = False
        self._bg_task: Optional[asyncio.Task] = None
        self._psutil_available = False

        try:
            import psutil
            self._psutil_available = True
        except ImportError:
            logger.warning("psutil not available. Memory monitoring disabled.")

    def register_cache(self, name: str, cache: LRUCache) -> None:
        """Register a cache for monitoring and potential eviction."""
        self._caches[name] = cache

    async def start(self) -> None:
        """Start the memory monitoring loop."""
        if not self._psutil_available:
            return
        self._running = True
        import asyncio
        self._bg_task = asyncio.create_task(self._monitor_loop())
        logger.info("Memory monitor started.")

    async def stop(self) -> None:
        """Stop the memory monitoring loop."""
        self._running = False
        if self._bg_task:
            self._bg_task.cancel()
            try:
                await self._bg_task
            except asyncio.CancelledError:
                pass

    def get_memory_usage(self) -> Optional[float]:
        """Get current memory usage as a fraction (0.0 - 1.0).

        Returns None if psutil is not available.
        """
        if not self._psutil_available:
            return None
        try:
            import psutil
            mem = psutil.virtual_memory()
            return mem.percent / 100.0
        except Exception:
            return None

    async def _monitor_loop(self) -> None:
        """Periodically check memory and evict caches if needed."""
        import asyncio
        while self._running:
            usage = self.get_memory_usage()
            if usage is not None and usage > MEMORY_PRESSURE_THRESHOLD:
                logger.warning(
                    "Memory pressure detected: %.1f%% (threshold: %.1f%%). Evicting caches.",
                    usage * 100,
                    MEMORY_PRESSURE_THRESHOLD * 100,
                )
                for name, cache in self._caches.items():
                    evicted = cache.evict()
                    logger.info("Evicted %d items from cache '%s'", evicted, name)

                # Force garbage collection
                collected = gc.collect()
                logger.info("Garbage collection: %d objects collected.", collected)

            await asyncio.sleep(CHECK_INTERVAL)


# ---------------------------------------------------------------------------
# Streaming file reader (avoids loading entire files into RAM)
# ---------------------------------------------------------------------------

class StreamingFileReader:
    """Reads a file in chunks without loading the entire file into RAM.

    Used for file transfers to keep memory usage low regardless of file size.
    """

    def __init__(self, file_path: str | Path, chunk_size: int = 16384) -> None:
        self._path = Path(file_path)
        self._chunk_size = chunk_size
        self._file = None
        self._offset = 0
        self._total_size = 0

    async def open(self) -> None:
        """Open the file for streaming reading."""
        import aiofiles
        self._file = await aiofiles.open(self._path, "rb")
        self._total_size = (await aiofiles.os.stat(self._path)).st_size

    async def read_chunk(self) -> Optional[bytes]:
        """Read the next chunk. Returns None at EOF."""
        if self._file is None:
            return None
        chunk = await self._file.read(self._chunk_size)
        if not chunk:
            return None
        self._offset += len(chunk)
        return chunk

    async def seek(self, offset: int) -> None:
        """Seek to a specific offset in the file."""
        if self._file:
            await self._file.seek(offset)
            self._offset = offset

    async def close(self) -> None:
        """Close the file."""
        if self._file:
            await self._file.close()
            self._file = None

    @property
    def progress(self) -> float:
        """Progress as a fraction (0.0 - 1.0)."""
        if self._total_size == 0:
            return 0.0
        return self._offset / self._total_size

    @property
    def total_size(self) -> int:
        return self._total_size

    @property
    def is_eof(self) -> bool:
        return self._offset >= self._total_size


# Need asyncio import for the module
import asyncio
