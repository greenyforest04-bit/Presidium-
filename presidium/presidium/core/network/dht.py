"""Simplified Kademlia DHT implementation for Presidium.

Provides distributed key-value storage and peer discovery:
- FIND_NODE: find k closest nodes to a target ID
- FIND_VALUE: retrieve a value by key
- STORE: store a key-value pair at the k closest nodes
- PING/PONG: liveness checks

Simplifications vs. full Kademlia:
- k-bucket size = 8 (instead of 20)
- Uses User ID (16 bytes) as both DHT node ID and key namespace
- No parallel alpha lookups (sequential for simplicity)
- Replication factor = 3
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from presidium.core.network.protocol import (
    MessageType,
    DHTPingPayload,
    DHTPingAckPayload,
    DHTFindNodePayload,
    DHTFindNodeRespPayload,
    DHTFindValuePayload,
    DHTFindValueRespPayload,
    DHTStorePayload,
    DHTStoreAckPayload,
    DHTNodeInfo,
)
from presidium.core.network.transport import UDPTransport

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

K_BUCKET_SIZE = 8
REPLICATION_FACTOR = 3
ID_LENGTH = 16  # bytes (128 bits)
RPC_TIMEOUT = 5.0  # seconds
REFRESH_INTERVAL = 3600  # seconds
MAX_STORED_VALUES = 1024


# ---------------------------------------------------------------------------
# XOR distance
# ---------------------------------------------------------------------------

def xor_distance(id_a: bytes, id_b: bytes) -> int:
    """Compute XOR distance between two node IDs."""
    if len(id_a) != len(id_b):
        raise ValueError(f"ID length mismatch: {len(id_a)} vs {len(id_b)}")
    result = 0
    for a, b in zip(id_a, id_b):
        result = (result << 8) | (a ^ b)
    return result


def bucket_index(distance: int) -> int:
    """Return the k-bucket index for a given XOR distance (0 = closest)."""
    if distance == 0:
        return 0
    return distance.bit_length() - 1


# ---------------------------------------------------------------------------
# KBucket
# ---------------------------------------------------------------------------

@dataclass
class DHTNode:
    """A node in the DHT routing table."""
    user_id: bytes
    addr: str  # host:port
    public_key: bytes  # 32 bytes
    last_seen: float = 0.0


class KBucket:
    """A k-bucket holding up to K nodes at a certain distance range."""

    def __init__(self, k: int = K_BUCKET_SIZE) -> None:
        self.k = k
        self.nodes: list[DHTNode] = []

    def add_or_update(self, node: DHTNode) -> Optional[DHTNode]:
        """Add or update a node in the bucket.

        Returns:
            The least-recently-seen node if the bucket is full and this node
            should be pinged for eviction check, or None.
        """
        # Check if node already exists
        for i, existing in enumerate(self.nodes):
            if existing.user_id == node.user_id:
                # Move to tail (most recently seen)
                self.nodes.pop(i)
                node.last_seen = time.monotonic()
                self.nodes.append(node)
                return None

        if len(self.nodes) < self.k:
            node.last_seen = time.monotonic()
            self.nodes.append(node)
            return None

        # Bucket full — return least-recently-seen for eviction check
        return self.nodes[0]

    def remove(self, user_id: bytes) -> None:
        """Remove a node from the bucket."""
        self.nodes = [n for n in self.nodes if n.user_id != user_id]

    def find(self, user_id: bytes) -> Optional[DHTNode]:
        """Find a node by user_id."""
        for n in self.nodes:
            if n.user_id == user_id:
                return n
        return None


# ---------------------------------------------------------------------------
# Routing Table
# ---------------------------------------------------------------------------

class RoutingTable:
    """Kademlia routing table with 128 k-buckets (ID_LENGTH * 8 bits)."""

    def __init__(self, local_id: bytes, k: int = K_BUCKET_SIZE) -> None:
        self._local_id = local_id
        self._k = k
        self._buckets: list[KBucket] = [KBucket(k) for _ in range(ID_LENGTH * 8)]

    def _bucket_for(self, node_id: bytes) -> KBucket:
        dist = xor_distance(self._local_id, node_id)
        idx = bucket_index(dist)
        return self._buckets[min(idx, len(self._buckets) - 1)]

    def add_node(self, node: DHTNode) -> Optional[DHTNode]:
        """Add or update a node. Returns eviction candidate if bucket is full."""
        bucket = self._bucket_for(node.user_id)
        return bucket.add_or_update(node)

    def remove_node(self, user_id: bytes) -> None:
        """Remove a node from the routing table."""
        bucket = self._bucket_for(user_id)
        bucket.remove(user_id)

    def find_closest(self, target_id: bytes, count: int = K_BUCKET_SIZE) -> list[DHTNode]:
        """Find the `count` closest nodes to target_id."""
        all_nodes: list[DHTNode] = []
        for bucket in self._buckets:
            all_nodes.extend(bucket.nodes)

        # Sort by XOR distance to target
        all_nodes.sort(key=lambda n: xor_distance(n.user_id, target_id))
        return all_nodes[:count]

    def get_all_nodes(self) -> list[DHTNode]:
        """Get all nodes in the routing table."""
        result: list[DHTNode] = []
        for bucket in self._buckets:
            result.extend(bucket.nodes)
        return result


# ---------------------------------------------------------------------------
# DHT Service
# ---------------------------------------------------------------------------

class DHTService:
    """Kademlia DHT service for Presidium.

    Handles:
    - Routing table maintenance
    - FIND_NODE / FIND_VALUE / STORE RPCs
    - Local key-value storage
    """

    def __init__(
        self,
        transport: UDPTransport,
        local_user_id: bytes,
        local_addr: str,
        local_public_key: bytes,
    ) -> None:
        self._transport = transport
        self._local_user_id = local_user_id
        self._local_addr = local_addr
        self._local_public_key = local_public_key

        self._routing_table = RoutingTable(local_user_id)
        self._store: dict[bytes, tuple[bytes, float]] = {}  # key → (value, expiry)

        # Pending RPC lookups: request_key → asyncio.Future
        self._pending_find_node: dict[bytes, asyncio.Future] = {}
        self._pending_find_value: dict[bytes, asyncio.Future] = {}
        self._pending_store: dict[bytes, asyncio.Future] = {}

        # Register handlers
        self._transport.register_handler(MessageType.DHT_PING, self._handle_ping)
        self._transport.register_handler(MessageType.DHT_PING_ACK, self._handle_ping_ack)
        self._transport.register_handler(MessageType.DHT_FIND_NODE, self._handle_find_node)
        self._transport.register_handler(MessageType.DHT_FIND_NODE_RESP, self._handle_find_node_resp)
        self._transport.register_handler(MessageType.DHT_FIND_VALUE, self._handle_find_value)
        self._transport.register_handler(MessageType.DHT_FIND_VALUE_RESP, self._handle_find_value_resp)
        self._transport.register_handler(MessageType.DHT_STORE, self._handle_store)
        self._transport.register_handler(MessageType.DHT_STORE_ACK, self._handle_store_ack)

        self._bg_tasks: list[asyncio.Task] = []

    # -------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------

    async def start(self) -> None:
        """Start DHT background tasks."""
        self._bg_tasks.append(asyncio.create_task(self._expiry_loop()))
        logger.info("DHT service started for node %s", self._local_user_id.hex())

    async def stop(self) -> None:
        """Stop DHT service."""
        for task in self._bg_tasks:
            task.cancel()
        await asyncio.gather(*self._bg_tasks, return_exceptions=True)
        self._bg_tasks.clear()

    # -------------------------------------------------------------------
    # Bootstrap
    # -------------------------------------------------------------------

    async def bootstrap(self, bootstrap_addrs: list[str]) -> None:
        """Bootstrap the DHT by contacting known nodes.

        Args:
            bootstrap_addrs: List of "host:port" strings for bootstrap nodes.
        """
        for addr_str in bootstrap_addrs:
            try:
                host, port_str = addr_str.rsplit(":", 1)
                port = int(port_str)
                addr = (host, port)

                # Send DHT PING
                ping = DHTPingPayload(
                    sender_id=self._local_user_id,
                    sender_addr=self._local_addr,
                )
                await self._transport.send(MessageType.DHT_PING, ping, addr)
                logger.info("DHT bootstrap ping sent to %s", addr_str)
            except Exception as exc:
                logger.warning("Failed to contact bootstrap node %s: %s", addr_str, exc)

        # After pinging, initiate FIND_NODE for our own ID to populate routing table
        await asyncio.sleep(2.0)  # Wait for ping responses
        await self.find_node(self._local_user_id)

    # -------------------------------------------------------------------
    # Public RPCs
    # -------------------------------------------------------------------

    async def find_node(self, target_id: bytes) -> list[DHTNode]:
        """Find the k closest nodes to target_id via iterative lookup.

        This is a simplified sequential lookup. In full Kademlia, alpha
        parallel lookups would be used.
        """
        closest = self._routing_table.find_closest(target_id, K_BUCKET_SIZE)

        # If we have enough from local table, return
        if len(closest) >= K_BUCKET_SIZE:
            return closest

        # Query the closest nodes we know about
        queried: set[bytes] = set()
        for _ in range(3):  # Max 3 rounds of iterative lookup
            new_nodes = [n for n in closest if n.user_id not in queried]
            if not new_nodes:
                break

            for node in new_nodes[:3]:  # Query up to 3 per round
                queried.add(node.user_id)
                try:
                    results = await self._rpc_find_node(target_id, node)
                    for r in results:
                        self._routing_table.add_node(r)
                        # Add to closest if closer than current
                    closest = self._routing_table.find_closest(target_id, K_BUCKET_SIZE)
                except Exception as exc:
                    logger.debug("FIND_NODE RPC to %s failed: %s", node.addr, exc)

        return closest

    async def find_value(self, key: bytes) -> Optional[bytes]:
        """Find a value by key in the DHT."""
        # Check local store first
        local = self._store.get(key)
        if local and local[1] > time.time():
            return local[0]

        # Iterative lookup
        closest = self._routing_table.find_closest(key, K_BUCKET_SIZE)
        for node in closest[:3]:
            try:
                result = await self._rpc_find_value(key, node)
                if result is not None:
                    return result
            except Exception:
                continue
        return None

    async def store_value(self, key: bytes, value: bytes, ttl: int = 3600) -> bool:
        """Store a key-value pair at the k closest nodes."""
        closest = self._routing_table.find_closest(key, REPLICATION_FACTOR)
        if not closest:
            # Store locally if no other nodes known
            self._store[key] = (value, time.time() + ttl)
            return True

        success = False
        for node in closest:
            try:
                ok = await self._rpc_store(key, value, ttl, node)
                if ok:
                    success = True
            except Exception:
                continue

        # Also store locally
        self._store[key] = (value, time.time() + ttl)
        return success

    # -------------------------------------------------------------------
    # RPC implementations
    # -------------------------------------------------------------------

    async def _rpc_find_node(self, target_id: bytes, node: DHTNode) -> list[DHTNode]:
        """Send FIND_NODE RPC to a specific node."""
        host, port_str = node.addr.rsplit(":", 1)
        port = int(port_str)
        addr = (host, port)

        request_key = target_id + node.user_id
        future: asyncio.Future[list[DHTNode]] = asyncio.get_running_loop().create_future()
        self._pending_find_node[request_key] = future

        payload = DHTFindNodePayload(
            sender_id=self._local_user_id,
            target_id=target_id,
            sender_addr=self._local_addr,
        )
        await self._transport.send(MessageType.DHT_FIND_NODE, payload, addr)

        try:
            return await asyncio.wait_for(future, timeout=RPC_TIMEOUT)
        except asyncio.TimeoutError:
            return []
        finally:
            self._pending_find_node.pop(request_key, None)

    async def _rpc_find_value(self, key: bytes, node: DHTNode) -> Optional[bytes]:
        """Send FIND_VALUE RPC to a specific node."""
        host, port_str = node.addr.rsplit(":", 1)
        port = int(port_str)
        addr = (host, port)

        request_key = key + node.user_id
        future: asyncio.Future[Optional[bytes]] = asyncio.get_running_loop().create_future()
        self._pending_find_value[request_key] = future

        payload = DHTFindValuePayload(
            sender_id=self._local_user_id,
            key=key,
        )
        await self._transport.send(MessageType.DHT_FIND_VALUE, payload, addr)

        try:
            return await asyncio.wait_for(future, timeout=RPC_TIMEOUT)
        except asyncio.TimeoutError:
            return None
        finally:
            self._pending_find_value.pop(request_key, None)

    async def _rpc_store(self, key: bytes, value: bytes, ttl: int, node: DHTNode) -> bool:
        """Send STORE RPC to a specific node."""
        host, port_str = node.addr.rsplit(":", 1)
        port = int(port_str)
        addr = (host, port)

        request_key = key + node.user_id
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending_store[request_key] = future

        payload = DHTStorePayload(
            sender_id=self._local_user_id,
            key=key,
            value=value,
            ttl=ttl,
        )
        await self._transport.send(MessageType.DHT_STORE, payload, addr)

        try:
            return await asyncio.wait_for(future, timeout=RPC_TIMEOUT)
        except asyncio.TimeoutError:
            return False
        finally:
            self._pending_store.pop(request_key, None)

    # -------------------------------------------------------------------
    # Incoming message handlers
    # -------------------------------------------------------------------

    async def _handle_ping(
        self, msg_type: int, timestamp: float, payload: DHTPingPayload, addr: tuple[str, int]
    ) -> None:
        """Respond to DHT PING."""
        # Add sender to routing table
        node = DHTNode(
            user_id=payload.sender_id,
            addr=payload.sender_addr,
            public_key=b"",  # Public key not provided in ping
            last_seen=time.monotonic(),
        )
        self._routing_table.add_node(node)

        ack = DHTPingAckPayload(
            sender_id=self._local_user_id,
            sender_addr=self._local_addr,
        )
        await self._transport.send(MessageType.DHT_PING_ACK, ack, addr)

    async def _handle_ping_ack(
        self, msg_type: int, timestamp: float, payload: DHTPingAckPayload, addr: tuple[str, int]
    ) -> None:
        """Handle DHT PING_ACK — update routing table."""
        node = DHTNode(
            user_id=payload.sender_id,
            addr=payload.sender_addr,
            public_key=b"",
            last_seen=time.monotonic(),
        )
        self._routing_table.add_node(node)

    async def _handle_find_node(
        self, msg_type: int, timestamp: float, payload: DHTFindNodePayload, addr: tuple[str, int]
    ) -> None:
        """Respond to FIND_NODE with k closest nodes."""
        # Update routing table with sender
        sender = DHTNode(
            user_id=payload.sender_id,
            addr=payload.sender_addr,
            public_key=b"",
        )
        self._routing_table.add_node(sender)

        closest = self._routing_table.find_closest(payload.target_id, K_BUCKET_SIZE)
        nodes = [
            DHTNodeInfo(user_id=n.user_id, addr=n.addr, public_key=n.public_key)
            for n in closest
        ]

        resp = DHTFindNodeRespPayload(
            sender_id=self._local_user_id,
            target_id=payload.target_id,
            nodes=nodes,
        )
        await self._transport.send(MessageType.DHT_FIND_NODE_RESP, resp, addr)

    async def _handle_find_node_resp(
        self, msg_type: int, timestamp: float, payload: DHTFindNodeRespPayload, addr: tuple[str, int]
    ) -> None:
        """Handle FIND_NODE response."""
        # Add responding node and reported nodes to routing table
        for node_info in payload.nodes:
            node = DHTNode(
                user_id=node_info.user_id,
                addr=node_info.addr,
                public_key=node_info.public_key,
            )
            self._routing_table.add_node(node)

        # Resolve pending future
        request_key = payload.target_id + payload.sender_id
        future = self._pending_find_node.get(request_key)
        if future and not future.done():
            nodes = [
                DHTNode(
                    user_id=ni.user_id,
                    addr=ni.addr,
                    public_key=ni.public_key,
                )
                for ni in payload.nodes
            ]
            future.set_result(nodes)

    async def _handle_find_value(
        self, msg_type: int, timestamp: float, payload: DHTFindValuePayload, addr: tuple[str, int]
    ) -> None:
        """Respond to FIND_VALUE."""
        local = self._store.get(payload.key)
        found = local is not None and local[1] > time.time()

        closest = self._routing_table.find_closest(payload.key, K_BUCKET_SIZE)
        closest_infos = [
            DHTNodeInfo(user_id=n.user_id, addr=n.addr, public_key=n.public_key)
            for n in closest
        ]

        resp = DHTFindValueRespPayload(
            sender_id=self._local_user_id,
            key=payload.key,
            value=local[0] if found else b"",
            found=found,
            closest_nodes=closest_infos,
        )
        await self._transport.send(MessageType.DHT_FIND_VALUE_RESP, resp, addr)

    async def _handle_find_value_resp(
        self, msg_type: int, timestamp: float, payload: DHTFindValueRespPayload, addr: tuple[str, int]
    ) -> None:
        """Handle FIND_VALUE response."""
        # Update routing table
        for node_info in payload.closest_nodes:
            node = DHTNode(
                user_id=node_info.user_id,
                addr=node_info.addr,
                public_key=node_info.public_key,
            )
            self._routing_table.add_node(node)

        # Resolve pending future
        request_key = payload.key + payload.sender_id
        future = self._pending_find_value.get(request_key)
        if future and not future.done():
            future.set_result(payload.value if payload.found else None)

    async def _handle_store(
        self, msg_type: int, timestamp: float, payload: DHTStorePayload, addr: tuple[str, int]
    ) -> None:
        """Handle STORE request — store value locally."""
        if len(self._store) >= MAX_STORED_VALUES:
            # Evict oldest expired entry
            now = time.time()
            expired_keys = [k for k, (_, exp) in self._store.items() if exp <= now]
            for k in expired_keys:
                del self._store[k]

        self._store[payload.key] = (payload.value, time.time() + payload.ttl)

        ack = DHTStoreAckPayload(
            key=payload.key,
            stored=True,
        )
        await self._transport.send(MessageType.DHT_STORE_ACK, ack, addr)

    async def _handle_store_ack(
        self, msg_type: int, timestamp: float, payload: DHTStoreAckPayload, addr: tuple[str, int]
    ) -> None:
        """Handle STORE_ACK."""
        # Resolve any pending store future
        for key, future in list(self._pending_store.items()):
            if key.startswith(payload.key) and not future.done():
                future.set_result(payload.stored)

    # -------------------------------------------------------------------
    # Background: expiry
    # -------------------------------------------------------------------

    async def _expiry_loop(self) -> None:
        """Periodically clean expired entries from local store."""
        while True:
            now = time.time()
            expired = [k for k, (_, exp) in self._store.items() if exp <= now]
            for k in expired:
                del self._store[k]
            if expired:
                logger.debug("Cleaned %d expired DHT entries", len(expired))
            await asyncio.sleep(60)
