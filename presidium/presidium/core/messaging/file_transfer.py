"""Chunked file transfer for Presidium.

Provides:
- File offer/accept handshake
- Chunked transfer with configurable chunk size (default 16 KiB)
- SHA-256 checksum per chunk and for the complete file
- Resume support (start from any chunk index)
- Progress tracking
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional

from presidium.core.network.peer import PeerManager
from presidium.core.network.protocol import (
    MessageType,
    FileOfferPayload,
    FileAcceptPayload,
    FileChunkPayload,
    FileChunkAckPayload,
    FileDonePayload,
)
from presidium.core.network.transport import UDPTransport

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class FileTransferError(Exception):
    """Base file transfer error."""


class FileOfferRejected(FileTransferError):
    """Peer rejected the file offer."""


class FileTransferTimeout(FileTransferError):
    """Transfer timed out waiting for chunk ACK."""


class ChecksumMismatchError(FileTransferError):
    """Chunk or file checksum mismatch."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_CHUNK_SIZE = 16384  # 16 KiB
CHUNK_ACK_TIMEOUT = 10.0  # seconds
MAX_PARALLEL_CHUNKS = 4  # max chunks in-flight


# ---------------------------------------------------------------------------
# Transfer state
# ---------------------------------------------------------------------------

class TransferDirection:
    UPLOAD = "upload"
    DOWNLOAD = "download"


@dataclass
class FileTransferState:
    """Tracks the state of an active file transfer."""
    session_id: bytes  # 16 bytes
    direction: str  # "upload" or "download"
    peer_user_id: str
    file_name: str
    file_size: int
    chunk_size: int = DEFAULT_CHUNK_SIZE
    total_chunks: int = 0
    file_sha256: bytes = b""  # 32 bytes
    completed_chunks: set[int] = field(default_factory=set)
    file_path: Optional[str] = None  # local path (for download: destination, for upload: source)
    started_at: float = field(default_factory=time.monotonic)
    chunk_checksums: dict[int, bytes] = field(default_factory=dict)

    @property
    def progress(self) -> float:
        """Completion percentage (0.0 to 1.0)."""
        if self.total_chunks == 0:
            return 0.0
        return len(self.completed_chunks) / self.total_chunks

    @property
    def is_complete(self) -> bool:
        return len(self.completed_chunks) == self.total_chunks


# ---------------------------------------------------------------------------
# File Transfer Manager
# ---------------------------------------------------------------------------

class FileTransferManager:
    """Manages P2P file transfers with chunked delivery and resume.

    Usage:
        1. Create FileTransferManager(peer_manager, transport)
        2. For sending: await offer_file(peer_uid, path)
        3. For receiving: incoming offers are handled automatically
        4. Progress callbacks update the UI
    """

    def __init__(
        self,
        peer_manager: PeerManager,
        transport: UDPTransport,
    ) -> None:
        self._peer_manager = peer_manager
        self._transport = transport

        # Active transfers: session_id → FileTransferState
        self._transfers: dict[bytes, FileTransferState] = {}

        # Pending offers: session_id → asyncio.Event (waiting for accept)
        self._pending_offers: dict[bytes, asyncio.Event] = {}

        # Callbacks
        self._on_progress: Optional[Callable[[bytes, float], Awaitable[None]]] = None
        self._on_transfer_complete: Optional[Callable[[bytes, bool, str], Awaitable[None]]] = None
        self._on_incoming_offer: Optional[Callable[[str, str, int, bytes], Awaitable[bool]]] = None

        # Register handlers
        self._transport.register_handler(MessageType.FILE_OFFER, self._handle_file_offer)
        self._transport.register_handler(MessageType.FILE_ACCEPT, self._handle_file_accept)
        self._transport.register_handler(MessageType.FILE_CHUNK, self._handle_file_chunk)
        self._transport.register_handler(MessageType.FILE_CHUNK_ACK, self._handle_file_chunk_ack)
        self._transport.register_handler(MessageType.FILE_DONE, self._handle_file_done)

    # -------------------------------------------------------------------
    # Callback setters
    # -------------------------------------------------------------------

    def set_on_progress(self, callback: Callable[[bytes, float], Awaitable[None]]) -> None:
        self._on_progress = callback

    def set_on_transfer_complete(
        self, callback: Callable[[bytes, bool, str], Awaitable[None]]
    ) -> None:
        self._on_transfer_complete = callback

    def set_on_incoming_offer(
        self, callback: Callable[[str, str, int, bytes], Awaitable[bool]]
    ) -> None:
        """Set callback for incoming file offers: (peer_uid, filename, size, sha256) → accept?"""
        self._on_incoming_offer = callback

    # -------------------------------------------------------------------
    # Sending files
    # -------------------------------------------------------------------

    async def offer_file(
        self,
        peer_user_id: str,
        file_path: str | Path,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> bytes:
        """Offer a file for transfer to a peer.

        Args:
            peer_user_id: The recipient's user ID.
            file_path: Path to the file to send.
            chunk_size: Size of each chunk in bytes.

        Returns:
            The session_id for tracking the transfer.

        Raises:
            FileTransferError: If the file cannot be read or peer is unreachable.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileTransferError(f"File not found: {file_path}")
        if not path.is_file():
            raise FileTransferError(f"Not a file: {file_path}")

        file_size = path.stat().st_size
        total_chunks = (file_size + chunk_size - 1) // chunk_size
        session_id = os.urandom(16)

        # Compute file SHA-256
        sha256 = hashlib.sha256()
        async_chunk_checksums: dict[int, bytes] = {}

        import aiofiles
        async with aiofiles.open(path, "rb") as f:
            chunk_index = 0
            while True:
                chunk_data = await f.read(chunk_size)
                if not chunk_data:
                    break
                sha256.update(chunk_data)
                chunk_hash = hashlib.sha256(chunk_data).digest()
                async_chunk_checksums[chunk_index] = chunk_hash
                chunk_index += 1

        file_sha256 = sha256.digest()

        # Create transfer state
        state = FileTransferState(
            session_id=session_id,
            direction=TransferDirection.UPLOAD,
            peer_user_id=peer_user_id,
            file_name=path.name,
            file_size=file_size,
            chunk_size=chunk_size,
            total_chunks=total_chunks,
            file_sha256=file_sha256,
            file_path=str(path),
            chunk_checksums=async_chunk_checksums,
        )
        self._transfers[session_id] = state

        # Send offer
        peer = self._peer_manager.get_peer(peer_user_id)
        if peer is None or peer.addr is None:
            raise FileTransferError(f"Peer {peer_user_id} not found or not connected.")

        offer = FileOfferPayload(
            session_id=session_id,
            file_name=path.name,
            file_size=file_size,
            chunk_size=chunk_size,
            sha256=file_sha256,
            total_chunks=total_chunks,
        )

        # Wait for accept
        accept_event = asyncio.Event()
        self._pending_offers[session_id] = accept_event

        await self._transport.send_reliable(MessageType.FILE_OFFER, offer, peer.addr)

        try:
            await asyncio.wait_for(accept_event.wait(), timeout=60.0)
        except asyncio.TimeoutError:
            self._transfers.pop(session_id, None)
            self._pending_offers.pop(session_id, None)
            raise FileTransferTimeout("No response to file offer within 60 seconds.")

        return session_id

    async def _send_chunks(self, session_id: bytes) -> None:
        """Send all chunks for an upload transfer."""
        state = self._transfers.get(session_id)
        if state is None or state.file_path is None:
            return

        peer = self._peer_manager.get_peer(state.peer_user_id)
        if peer is None or peer.addr is None:
            return

        import aiofiles
        async with aiofiles.open(state.file_path, "rb") as f:
            for chunk_idx in range(state.total_chunks):
                # Skip already ACKed chunks (for resume)
                if chunk_idx in state.completed_chunks:
                    continue

                await f.seek(chunk_idx * state.chunk_size)
                chunk_data = await f.read(state.chunk_size)

                chunk_hash = hashlib.sha256(chunk_data).digest()
                payload = FileChunkPayload(
                    session_id=session_id,
                    chunk_index=chunk_idx,
                    data=chunk_data,
                    chunk_sha256=chunk_hash,
                )

                await self._transport.send_reliable(
                    MessageType.FILE_CHUNK, payload, peer.addr
                )

                if self._on_progress:
                    await self._on_progress(session_id, state.progress)

        # Send FILE_DONE after all chunks sent
        # (We'll rely on all chunk ACKs being received to confirm success)
        # SIMPLIFIED: in production, wait for all ACKs before sending DONE

    # -------------------------------------------------------------------
    # Incoming handlers
    # -------------------------------------------------------------------

    async def _handle_file_offer(
        self, msg_type: int, timestamp: float, payload: FileOfferPayload, addr: tuple[str, int]
    ) -> None:
        """Handle incoming file offer."""
        peer_uid = "unknown"
        # Find peer by address
        for peer in self._peer_manager.get_all_peers():
            if peer.addr == addr:
                peer_uid = peer.user_id
                break

        logger.info("File offer from %s: %s (%d bytes)", peer_uid, payload.file_name, payload.file_size)

        # Ask user whether to accept
        accept = True
        if self._on_incoming_offer:
            try:
                accept = await self._on_incoming_offer(
                    peer_uid, payload.file_name, payload.file_size, payload.sha256
                )
            except Exception:
                accept = False

        # Create download state
        if accept:
            # Determine download path
            download_dir = Path.home() / "Downloads" / "Presidium"
            download_dir.mkdir(parents=True, exist_ok=True)
            download_path = download_dir / payload.file_name

            state = FileTransferState(
                session_id=payload.session_id,
                direction=TransferDirection.DOWNLOAD,
                peer_user_id=peer_uid,
                file_name=payload.file_name,
                file_size=payload.file_size,
                chunk_size=payload.chunk_size,
                total_chunks=payload.total_chunks,
                file_sha256=payload.sha256,
                file_path=str(download_path),
            )
            self._transfers[payload.session_id] = state

            # Create empty file
            download_path.touch()

        # Send accept/reject
        resp = FileAcceptPayload(
            session_id=payload.session_id,
            start_chunk=0,
        )
        await self._transport.send(MessageType.FILE_ACCEPT, resp, addr)

    async def _handle_file_accept(
        self, msg_type: int, timestamp: float, payload: FileAcceptPayload, addr: tuple[str, int]
    ) -> None:
        """Handle file accept — start sending chunks."""
        event = self._pending_offers.pop(payload.session_id, None)
        if event:
            event.set()  # Unblock the offer_file() call

        # Start sending chunks
        asyncio.create_task(self._send_chunks(payload.session_id))

    async def _handle_file_chunk(
        self, msg_type: int, timestamp: float, payload: FileChunkPayload, addr: tuple[str, int]
    ) -> None:
        """Handle incoming file chunk."""
        state = self._transfers.get(payload.session_id)
        if state is None:
            logger.warning("File chunk for unknown session %s", payload.session_id.hex())
            return

        # Verify chunk checksum
        chunk_hash = hashlib.sha256(payload.data).digest()
        if chunk_hash != payload.chunk_sha256:
            logger.warning(
                "Chunk %d checksum mismatch for session %s",
                payload.chunk_index, payload.session_id.hex(),
            )
            return

        # Write chunk to file
        if state.file_path:
            import aiofiles
            async with aiofiles.open(state.file_path, "r+b") as f:
                await f.seek(payload.chunk_index * state.chunk_size)
                await f.write(payload.data)

        state.completed_chunks.add(payload.chunk_index)

        # Send ACK
        ack = FileChunkAckPayload(
            session_id=payload.session_id,
            chunk_index=payload.chunk_index,
        )
        await self._transport.send(MessageType.FILE_CHUNK_ACK, ack, addr)

        # Update progress
        if self._on_progress:
            await self._on_progress(payload.session_id, state.progress)

        # Check if transfer is complete
        if state.is_complete:
            # Verify full file SHA-256
            file_hash = hashlib.sha256()
            if state.file_path:
                import aiofiles
                async with aiofiles.open(state.file_path, "rb") as f:
                    while True:
                        data = await f.read(DEFAULT_CHUNK_SIZE)
                        if not data:
                            break
                        file_hash.update(data)

            success = file_hash.digest() == state.file_sha256

            done = FileDonePayload(
                session_id=payload.session_id,
                success=success,
                error_msg="" if success else "SHA-256 checksum mismatch",
            )
            await self._transport.send(MessageType.FILE_DONE, done, addr)

            if self._on_transfer_complete:
                await self._on_transfer_complete(
                    payload.session_id, success,
                    "" if success else "Checksum mismatch",
                )

            logger.info(
                "File transfer %s: %s (%s)",
                "complete" if success else "FAILED",
                state.file_name, payload.session_id.hex(),
            )

    async def _handle_file_chunk_ack(
        self, msg_type: int, timestamp: float, payload: FileChunkAckPayload, addr: tuple[str, int]
    ) -> None:
        """Handle chunk ACK — mark chunk as delivered."""
        state = self._transfers.get(payload.session_id)
        if state:
            state.completed_chunks.add(payload.chunk_index)
            if self._on_progress:
                await self._on_progress(payload.session_id, state.progress)

    async def _handle_file_done(
        self, msg_type: int, timestamp: float, payload: FileDonePayload, addr: tuple[str, int]
    ) -> None:
        """Handle file done notification."""
        state = self._transfers.pop(payload.session_id, None)
        if state and self._on_transfer_complete:
            await self._on_transfer_complete(
                payload.session_id, payload.success, payload.error_msg,
            )
