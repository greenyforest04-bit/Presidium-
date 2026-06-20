"""Peer discovery service for Presidium.

Implements hybrid discovery:
- mDNS (multicast DNS) for LAN peer discovery
- Bootstrap node contact for WAN
- DHT-based lookup for global peer resolution
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from presidium.core.network.dht import DHTService
from presidium.core.network.peer import PeerManager, PeerInfo
from presidium.core.network.transport import UDPTransport
from presidium.utils.config import NetworkConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class DiscoveryError(Exception):
    """Base discovery error."""


# ---------------------------------------------------------------------------
# mDNS constants
# ---------------------------------------------------------------------------

MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353
MDNS_TTL = 60  # seconds
PRESIDIUM_SERVICE_TYPE = "_presidium._udp.local."
MDNS_ANNOUNCE_INTERVAL = 60  # seconds


# ---------------------------------------------------------------------------
# mDNS announcement packet builder (simplified)
# ---------------------------------------------------------------------------

def build_mdns_announce(instance_name: str, port: int) -> bytes:
    """Build a minimal mDNS announcement packet for Presidium service.

    This is a simplified mDNS implementation that broadcasts a service
    presence on the local network. It creates a DNS-SD response packet
    advertising the Presidium P2P service.

    SIMPLIFIED: A production implementation would use a proper DNS library
    like zeroconf/dnslib for full mDNS compliance.
    """
    # DNS header: ID=0, QR=1 (response), AA=1, rest=0
    header = struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 0)

    # PTR record: _presidium._udp.local → instance._presidium._udp.local
    ptr_name = _encode_dns_name(PRESIDIUM_SERVICE_TYPE)
    ptr_target = _encode_dns_name(f"{instance_name}.{PRESIDIUM_SERVICE_TYPE}")
    ptr_rdata = ptr_target
    ptr_record = struct.pack(
        "!HHIH", 0x0C, 0x8001, MDNS_TTL, len(ptr_rdata)
    ) + ptr_rdata

    return header + ptr_name + ptr_record


def _encode_dns_name(name: str) -> bytes:
    """Encode a DNS name into wire format (label lengths + labels + null)."""
    parts = name.rstrip(".").split(".")
    result = b""
    for part in parts:
        encoded = part.encode("ascii")
        result += bytes([len(encoded)]) + encoded
    result += b"\x00"
    return result


# ---------------------------------------------------------------------------
# mDNS Listener
# ---------------------------------------------------------------------------

class MDNSListener:
    """Listens for mDNS announcements from other Presidium nodes on the LAN.

    Uses a multicast UDP socket bound to the mDNS port. When a Presidium
    service announcement is detected, it triggers a callback with the
    discovered peer's address.
    """

    def __init__(
        self,
        local_user_id: str,
        local_port: int,
        on_peer_discovered: Callable[[str, int], Awaitable[None]],
    ) -> None:
        self._local_user_id = local_user_id
        self._local_port = local_port
        self._on_peer_discovered = on_peer_discovered
        self._running = False
        self._socket: Optional[socket.socket] = None
        self._bg_task: Optional[asyncio.Task] = None
        self._announce_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start mDNS listening and periodic announcements."""
        self._running = True
        loop = asyncio.get_running_loop()

        # Create multicast socket
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            self._socket.bind(("", MDNS_PORT))
        except OSError as exc:
            logger.warning("Cannot bind mDNS port %d: %s. LAN discovery disabled.", MDNS_PORT, exc)
            return

        # Join multicast group
        try:
            mreq = struct.pack("4s4s", socket.inet_aton(MDNS_GROUP), socket.inet_aton("0.0.0.0"))
            self._socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except OSError as exc:
            logger.warning("Cannot join mDNS group: %s. LAN discovery limited.", exc)

        self._socket.setblocking(False)

        # Start listener and announcer
        self._bg_task = asyncio.create_task(self._listen_loop())
        self._announce_task = asyncio.create_task(self._announce_loop())
        logger.info("mDNS listener started on port %d", MDNS_PORT)

    async def stop(self) -> None:
        """Stop mDNS listener."""
        self._running = False
        if self._bg_task:
            self._bg_task.cancel()
        if self._announce_task:
            self._announce_task.cancel()
        if self._socket:
            self._socket.close()
        logger.info("mDNS listener stopped.")

    async def _listen_loop(self) -> None:
        """Background loop: read mDNS packets."""
        loop = asyncio.get_running_loop()
        while self._running:
            try:
                data, addr = await loop.sock_recvfrom(self._socket, 4096)  # type: ignore
                await self._process_packet(data, addr)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("mDNS read error: %s", exc)
                await asyncio.sleep(0.1)

    async def _process_packet(self, data: bytes, addr: tuple[str, int]) -> None:
        """Process a received mDNS packet."""
        host, port = addr
        # Skip our own announcements
        # SIMPLIFIED: In production, parse DNS packets properly with a DNS library.
        # Here we just check if the data contains our service type string.
        if PRESIDIUM_SERVICE_TYPE.encode() in data:
            logger.debug("mDNS: Presidium service found from %s:%d", host, port)
            # The port in the mDNS response may differ from the sender's port.
            # We use the sender's IP and the known Presidium port.
            try:
                await self._on_peer_discovered(host, 9478)  # Default port
            except Exception as exc:
                logger.debug("Error processing discovered peer: %s", exc)

    async def _announce_loop(self) -> None:
        """Periodically announce our presence via mDNS."""
        while self._running:
            try:
                packet = build_mdns_announce(self._local_user_id[:8], self._local_port)
                if self._socket:
                    self._socket.sendto(
                        packet,
                        (MDNS_GROUP, MDNS_PORT),
                    )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("mDNS announce error: %s", exc)
            await asyncio.sleep(MDNS_ANNOUNCE_INTERVAL)


# ---------------------------------------------------------------------------
# Bootstrap connector
# ---------------------------------------------------------------------------

async def contact_bootstrap_nodes(
    transport: UDPTransport,
    dht: DHTService,
    config: NetworkConfig,
) -> None:
    """Contact bootstrap nodes and populate the DHT routing table.

    Args:
        transport: Active UDP transport.
        dht: DHT service instance.
        config: Network configuration with bootstrap node addresses.
    """
    from presidium.core.network.protocol import (
        MessageType,
        HelloPayload,
    )

    for addr_str in config.bootstrap_nodes:
        try:
            host, port_str = addr_str.rsplit(":", 1)
            port = int(port_str)
            addr = (host, port)

            # Send HELLO to bootstrap node
            hello = HelloPayload(
                user_id=bytes.fromhex(dht._local_user_id.hex()),
                public_key=dht._local_public_key,
                listen_port=config.listen_port,
            )
            await transport.send(MessageType.HELLO, hello, addr)
            logger.info("Contacted bootstrap node: %s", addr_str)
        except Exception as exc:
            logger.warning("Failed to contact bootstrap %s: %s", addr_str, exc)

    # Bootstrap the DHT
    await dht.bootstrap(config.bootstrap_nodes)


# ---------------------------------------------------------------------------
# Discovery Service
# ---------------------------------------------------------------------------

class DiscoveryService:
    """Unified peer discovery service combining mDNS, DHT, and bootstrap.

    Usage:
        1. Create DiscoveryService(config, transport, dht, peer_manager)
        2. await start() — starts mDNS listener and contacts bootstrap nodes
        3. Discovered peers are automatically added to PeerManager
        4. await stop() — clean shutdown
    """

    def __init__(
        self,
        config: NetworkConfig,
        transport: UDPTransport,
        dht: DHTService,
        peer_manager: PeerManager,
        local_user_id: str,
        local_public_key: bytes,
    ) -> None:
        self._config = config
        self._transport = transport
        self._dht = dht
        self._peer_manager = peer_manager
        self._local_user_id = local_user_id
        self._local_public_key = local_public_key

        self._mdns: Optional[MDNSListener] = None
        self._running = False

    async def start(self) -> None:
        """Start all discovery mechanisms."""
        self._running = True

        # mDNS LAN discovery
        if self._config.mdns_enabled:
            self._mdns = MDNSListener(
                local_user_id=self._local_user_id,
                local_port=self._config.listen_port,
                on_peer_discovered=self._on_lan_peer_discovered,
            )
            await self._mdns.start()

        # Bootstrap nodes
        if self._config.bootstrap_nodes:
            asyncio.create_task(
                contact_bootstrap_nodes(self._transport, self._dht, self._config)
            )

        logger.info("Discovery service started (mDNS=%s, DHT=%s)",
                     self._config.mdns_enabled, self._config.dht_enabled)

    async def stop(self) -> None:
        """Stop all discovery mechanisms."""
        self._running = False
        if self._mdns:
            await self._mdns.stop()

    async def find_peer(self, user_id: str) -> Optional[PeerInfo]:
        """Look up a peer by user ID using all available discovery methods.

        1. Check PeerManager's known peers
        2. DHT lookup
        3. Return PeerInfo if found, None otherwise
        """
        # Check local table first
        existing = self._peer_manager.get_peer(user_id)
        if existing and existing.addr:
            return existing

        # DHT lookup
        if self._config.dht_enabled:
            try:
                uid_bytes = bytes.fromhex(user_id)
                # Try to find the peer's address in the DHT
                value = await self._dht.find_value(uid_bytes)
                if value:
                    # Value format: host:port\0public_key (null-separated)
                    parts = value.split(b"\x00", 1)
                    addr_str = parts[0].decode("ascii")
                    pk = parts[1] if len(parts) > 1 else b""

                    host, port_str = addr_str.rsplit(":", 1)
                    port = int(port_str)

                    peer = self._peer_manager.add_known_peer(
                        user_id=user_id,
                        public_key_bytes=pk,
                        addr=(host, port),
                    )
                    return peer
            except Exception as exc:
                logger.warning("DHT lookup for %s failed: %s", user_id, exc)

        return None

    async def register_presence(self) -> None:
        """Register our presence in the DHT so others can find us."""
        if not self._config.dht_enabled:
            return

        uid_bytes = bytes.fromhex(self._local_user_id)
        # Store: user_id → "host:port\0public_key"
        addr_str = f"0.0.0.0:{self._config.listen_port}"
        value = f"{addr_str}\x00".encode("ascii") + self._local_public_key

        await self._dht.store_value(uid_bytes, value, ttl=3600)
        logger.info("Registered presence in DHT for user %s", self._local_user_id[:8])

    # -------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------

    async def _on_lan_peer_discovered(self, host: str, port: int) -> None:
        """Callback when a peer is discovered via mDNS."""
        logger.info("LAN peer discovered at %s:%d", host, port)
        # Initiate handshake with discovered peer
        from presidium.core.network.protocol import (
            MessageType,
            HelloPayload,
        )
        addr = (host, port)
        hello = HelloPayload(
            user_id=bytes.fromhex(self._local_user_id),
            public_key=self._local_public_key,
            listen_port=self._config.listen_port,
        )
        await self._transport.send(MessageType.HELLO, hello, addr)
