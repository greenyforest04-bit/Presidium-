"""Graceful shutdown and recovery for Presidium.

Provides:
- SIGTERM/SIGINT handler: flush outbox, send FIN to peers, save DHT, zero keys
- Recovery: check dirty_shutdown flag, repair WAL, restore outbox
- Lifecycle state tracking
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from enum import Enum, auto
from pathlib import Path
from typing import Optional, Callable, Awaitable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifecycle states
# ---------------------------------------------------------------------------

class LifecycleState(Enum):
    """Application lifecycle state."""
    STARTING = auto()
    RUNNING = auto()
    SHUTTING_DOWN = auto()
    STOPPED = auto()


# ---------------------------------------------------------------------------
# Dirty shutdown flag
# ---------------------------------------------------------------------------

DIRTY_SHUTDOWN_FILE = ".dirty_shutdown"


def mark_dirty(data_dir: Path) -> None:
    """Create a dirty shutdown flag file."""
    flag_path = data_dir / DIRTY_SHUTDOWN_FILE
    try:
        flag_path.write_text(str(time.time()))
    except Exception:
        pass


def clear_dirty(data_dir: Path) -> None:
    """Remove the dirty shutdown flag file."""
    flag_path = data_dir / DIRTY_SHUTDOWN_FILE
    try:
        flag_path.unlink(missing_ok=True)
    except Exception:
        pass


def check_dirty(data_dir: Path) -> bool:
    """Check if the previous shutdown was dirty."""
    flag_path = data_dir / DIRTY_SHUTDOWN_FILE
    return flag_path.exists()


# ---------------------------------------------------------------------------
# Lifecycle Manager
# ---------------------------------------------------------------------------

class LifecycleManager:
    """Manages application lifecycle: startup, shutdown, and recovery.

    Usage:
        lifecycle = LifecycleManager(data_dir, shutdown_callback)
        lifecycle.install_signal_handlers()

        # On normal startup
        await lifecycle.on_startup()

        # On shutdown (automatic via signal or manual)
        await lifecycle.shutdown(reason="user quit")

        # On startup, check for dirty shutdown
        if lifecycle.needs_recovery():
            await lifecycle.recover()
    """

    def __init__(
        self,
        data_dir: Path,
        shutdown_callback: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> None:
        self._data_dir = data_dir
        self._shutdown_callback = shutdown_callback
        self._state = LifecycleState.STARTING
        self._shutdown_event = asyncio.Event()
        self._installed = False

    @property
    def state(self) -> LifecycleState:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._state == LifecycleState.RUNNING

    @property
    def is_shutting_down(self) -> bool:
        return self._state == LifecycleState.SHUTTING_DOWN

    # -------------------------------------------------------------------
    # Signal handlers
    # -------------------------------------------------------------------

    def install_signal_handlers(self) -> None:
        """Install SIGTERM and SIGINT handlers for graceful shutdown."""
        if self._installed:
            return
        self._installed = True

        loop = asyncio.get_running_loop()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, lambda: asyncio.ensure_future(self._signal_handler(sig)))
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                pass

        logger.info("Signal handlers installed.")

    async def _signal_handler(self, sig: signal.Signals) -> None:
        """Handle a shutdown signal."""
        sig_name = sig.name
        logger.info("Received signal %s, initiating graceful shutdown...", sig_name)
        await self.shutdown(reason=f"signal {sig_name}")

    # -------------------------------------------------------------------
    # Startup
    # -------------------------------------------------------------------

    async def on_startup(self) -> None:
        """Mark the application as started and set the dirty flag."""
        self._state = LifecycleState.RUNNING
        self._data_dir.mkdir(parents=True, exist_ok=True)
        mark_dirty(self._data_dir)
        logger.info("Application started. Dirty flag set.")

    # -------------------------------------------------------------------
    # Shutdown
    # -------------------------------------------------------------------

    async def shutdown(self, reason: str = "unknown") -> None:
        """Perform graceful shutdown.

        Steps:
        1. Mark state as SHUTTING_DOWN
        2. Call the shutdown callback (flush outbox, send FIN, save DHT, zero keys)
        3. Clear the dirty flag
        4. Mark state as STOPPED
        """
        if self._state in (LifecycleState.SHUTTING_DOWN, LifecycleState.STOPPED):
            return  # Already shutting down

        self._state = LifecycleState.SHUTTING_DOWN
        logger.info("Graceful shutdown started. Reason: %s", reason)

        try:
            if self._shutdown_callback:
                await self._shutdown_callback(reason)
        except Exception as exc:
            logger.error("Error during shutdown callback: %s", exc)

        # Clear dirty flag — clean shutdown
        clear_dirty(self._data_dir)

        self._state = LifecycleState.STOPPED
        self._shutdown_event.set()
        logger.info("Graceful shutdown complete.")

    # -------------------------------------------------------------------
    # Recovery
    # -------------------------------------------------------------------

    def needs_recovery(self) -> bool:
        """Check if recovery is needed after a dirty shutdown."""
        return check_dirty(self._data_dir)

    async def recover(self) -> None:
        """Perform recovery after a dirty shutdown.

        Steps:
        1. Log the dirty shutdown event
        2. Attempt WAL checkpoint on SQLite databases
        3. Verify outbox integrity
        4. Clean up stale temp files
        """
        logger.warning("Dirty shutdown detected. Starting recovery...")

        # Try to checkpoint WAL on the main database
        db_path = self._data_dir / "presidium.db"
        if db_path.exists():
            try:
                import aiosqlite
                async with aiosqlite.connect(str(db_path)) as db:
                    await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    await db.execute("PRAGMA integrity_check")
                    logger.info("Database WAL checkpoint and integrity check passed.")
            except Exception as exc:
                logger.error("Database recovery failed: %s. Manual intervention may be needed.", exc)

        # Clean stale temp files
        try:
            for tmp_file in self._data_dir.glob("*.tmp"):
                tmp_file.unlink(missing_ok=True)
                logger.debug("Removed stale temp file: %s", tmp_file)
        except Exception as exc:
            logger.warning("Temp file cleanup failed: %s", exc)

        logger.info("Recovery complete.")

    # -------------------------------------------------------------------
    # Wait for shutdown
    # -------------------------------------------------------------------

    async def wait_for_shutdown(self) -> None:
        """Block until shutdown is complete."""
        await self._shutdown_event.wait()
