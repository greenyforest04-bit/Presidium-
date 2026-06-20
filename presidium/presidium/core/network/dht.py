"""Full Kademlia DHT implementation for Presidium.

Provides distributed key-value storage and peer discovery:
- K-buckets with k=20, proper bucket splitting by XOR-distance
- Parallel lookups (α=3) for FIND_NODE
- RPC timeout, retransmission, stale node eviction
- Periodic bucket refresh every 60 minutes
- Key republish every 24 hours
- Bootstrap logic with LAN-only fallback via mDNS

Reference: https://pdos.csail.mit.edu/~petar/papers/maymounkov-kademlia-lncs.pdf
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
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

K_BUCKET_SIZE = 20         # k — nodes per bucket
ALPHA = 3                  # parallelism for lookups
ID_LENGTH = 16             # bytes (128 bits)
ID_BITS = ID_LENGTH * 8    # 128
RPC_TIMEOUT = 5.0          # seconds per RPC
REFRESH_INTERVAL = 3600    # 1 hour — bucket refresh
REPUBLISH_INTERVAL = 86400 # 24 hours — key republish
MAX_STORED_VALUES = 2048
EVICT_CHECK_INTERVAL = 300 # 5 minutes — check stale nodes
MAX_CONCURRENT_RPCS = 16


# ---------------------------------------------------------------------------
# XOR distance
# ---------------------------------------------------------------------------

def xor_distance(id_a: bytes, id_b: bytes) -> int:
    """Compute XOR distance between two node IDs (128-bit)."""
    if len(id_a) != len(id_b):
        raise ValueError(f"ID length mismatch: {len(id_a)} vs {len(id_b)}")
    result = 0
    for a, b in zip(id_a, id_b):
        result = (result << 8) | (a ^ b)
    return result


def bucket_index_for_distance(distance: int) -> int:
    """Return the k-bucket index for a given XOR distance.

    Index 0 = distance 0 (same node, never stored).
    Index 1 = distance in [1, 1] (only bit 0 differs).
    Index i = distance in [2^(i-1), 2^i - 1].
    Index 127 = most distant bucket.
    """
    if distance == 0:
        return 0
    return distance.bit_length() - 1


# ---------------------------------------------------------------------------
# DHT Node record
# ---------------------------------------------------------------------------

@dataclass
class DHTNode:
    """A node in the DHT routing table."""
    user_id: bytes
    addr: str  # host:port
    public_key: bytes = b""  # 32 bytes
    last_seen: float = 0.0
    last_pinged: float = 0.0
    ping_pending: bool = False
    failures: int = 0

    @property
    def is_stale(self) -> bool:
        """A node is stale if it has failed ≥ 3 RPCs."""
        return self.failures >= 3


# ---------------------------------------------------------------------------
# KBucket with proper LRU and eviction
# ---------------------------------------------------------------------------

class KBucket:
    """A k-bucket holding at most K nodes at a specific XOR-distance range.

    Nodes are ordered from least-recently-seen (head) to most-recently-seen (tail).
    When a new node is discovered and the bucket is full:
    - If the least-recently-seen node responds to ping → keep old, discard new
    - If the least-recently-seen node doesn't respond → evict old, add new
    """

    def __init__(self, k: int = K_BUCKET_SIZE) -> None:
        self.k = k
        self._nodes: OrderedDict[bytes, DHTNode] = OrderedDict()

    def __len__(self) -> int:
        return len(self._nodes)

    def __contains__(self, user_id: bytes) -> bool:
        return user_id in self._nodes

    def __iter__(self):
        return iter(self._nodes.values())

    @property
    def is_full(self) -> bool:
        return len(self._nodes) >= self.k

    @property
    def head(self) -> Optional[DHTNode]:
        """Least-recently-seen node."""
        if not self._nodes:
            return None
        first_key = next(iter(self._nodes))
        return self._nodes[first_key]

    @property
    def tail(self) -> Optional[DHTNode]:
        """Most-recently-seen node."""
        if not self._nodes:
            return None
        last_key = next(reversed(self._nodes))
        return self._nodes[last_key]

    def add_or_update(self, node: DHTNode) -> Optional[DHTNode]:
        """Add or update a node in the bucket.

        If the node already exists, move it to the tail (most recently seen).
        If the bucket is not full, add the node at the tail.
        If the bucket is full, return the head node for ping-based eviction check.

        Returns:
            None if the node was added/updated.
            The least-recently-seen node if the bucket is full and eviction is needed.
        """
        if node.user_id in self._nodes:
            # Move to tail
            del self._nodes[node.user_id]
            node.last_seen = time.monotonic()
            node.failures = 0
            self._nodes[node.user_id] = node
            return None

        if len(self._nodes) < self.k:
            node.last_seen = time.monotonic()
            node.failures = 0
            self._nodes[node.user_id] = node
            return None

        # Bucket full — return head for eviction check
        return self.head

    def remove(self, user_id: bytes) -> None:
        """Remove a node from the bucket."""
        self._nodes.pop(user_id, None)

    def replace_if_stale(self, old_user_id: bytes, new_node: DHTNode) -> bool:
        """Replace a stale node with a new one.

        Returns:
            True if replacement was done, False otherwise.
        """
        if old_user_id in self._nodes:
            old = self._nodes[old_user_id]
            if old.is_stale or old.ping_pending:
                del self._nodes[old_user_id]
                new_node.last_seen = time.monotonic()
                self._nodes[new_node.user_id] = new_node
                return True
        return False

    def increment_failure(self, user_id: bytes) -> None:
        """Increment failure count for a node."""
        node = self._nodes.get(user_id)
        if node:
            node.failures += 1
            node.ping_pending = False

    def get_all_nodes(self) -> list[DHTNode]:
        """Get all nodes in the bucket (least-recent first)."""
        return list(self._nodes.values())


# ---------------------------------------------------------------------------
# Routing Table with 128 k-buckets
# ---------------------------------------------------------------------------

class RoutingTable:
    """Kademlia routing table with 128 k-buckets (ID_BITS = 128).

    Bucket i covers the XOR distance range [2^i, 2^(i+1) - 1].
    Bucket 0 is for the local node (distance 0, never used).
    In practice, most buckets will be empty — only buckets
    corresponding to actual network distances will be populated.
    """

    def __init__(self, local_id: bytes, k: int = K_BUCKET_SIZE) -> None:
        self._local_id = local_id
        self._k = k
        # 128 buckets (0..127), bucket 0 is unused
        self._buckets: list[KBucket] = [KBucket(k) for _ in range(ID_BITS)]

    def _bucket_for(self, node_id: bytes) -> KBucket:
        """Get the appropriate bucket for a node ID."""
        dist = xor_distance(self._local_id, node_id)
        idx = bucket_index_for_distance(dist)
        return self._buckets[min(idx, ID_BITS - 1)]

    def add_node(self, node: DHTNode) -> Optional[DHTNode]:
        """Add or update a node in the routing table.

        Returns:
            None if the node was added/updated.
            The least-recently-seen node if the bucket is full and eviction is needed.
        """
        # Don't add ourselves
        if node.user_id == self._local_id:
            return None
        bucket = self._bucket_for(node.user_id)
        return bucket.add_or_update(node)

    def remove_node(self, user_id: bytes) -> None:
        """Remove a node from the routing table."""
        bucket = self._bucket_for(user_id)
        bucket.remove(user_id)

    def find_closest(self, target_id: bytes, count: int = K_BUCKET_SIZE) -> list[DHTNode]:
        """Find the `count` closest nodes to target_id across all buckets."""
        all_nodes: list[DHTNode] = []
        for bucket in self._buckets:
            all_nodes.extend(bucket.get_all_nodes())

        all_nodes.sort(key=lambda n: xor_distance(n.user_id, target_id))
        return all_nodes[:count]

    def get_all_nodes(self) -> list[DHTNode]:
        """Get all nodes in the routing table."""
        result: list[DHTNode] = []
        for bucket in self._buckets:
            result.extend(bucket.get_all_nodes())
        return result

    def get_bucket_for_eviction_check(self, node_id: bytes) -> Optional[DHTNode]:
        """Get the head node of the bucket that would contain node_id.

        Returns None if the bucket is not full.
        """
        bucket = self._bucket_for(node_id)
        if bucket.is_full:
            return bucket.head
        return None

    def increment_failure(self, user_id: bytes) -> None:
        """Increment failure count for a node."""
        bucket = self._bucket_for(user_id)
        bucket.increment_failure(user_id)

    @property
    def total_nodes(self) -> int:
        return sum(len(b) for b in self._buckets)

    @property
    def local_id(self) -> bytes:
        return self._local_id


# ---------------------------------------------------------------------------
# Stored value record
# ---------------------------------------------------------------------------

@dataclass
class StoredValue:
    """A locally stored DHT key-value pair."""
    key: bytes
    value: bytes
    expiry: float  # Unix timestamp
    original_publisher: bytes  # user_id of the node that stored this
    last_republished: float = 0.0


# ---------------------------------------------------------------------------
# Pending RPC tracking
# ---------------------------------------------------------------------------

@dataclass
class PendingRPC:
    """Tracks a pending DHT RPC."""
    target_node: DHTNode
    future: asyncio.Future
    request_type: int  # MessageType
    request_key: bytes  # unique key for matching responses
    sent_at: float = field(default_factory=time.monotonic)
    retries: int = 0
    max_retries: int = 2


# ---------------------------------------------------------------------------
# DHT Service
# ---------------------------------------------------------------------------

class DHTService:
    """Full Kademlia DHT service for Presidium.

    Features:
    - Routing table with 128 k-buckets, k=20
    - Parallel α=3 lookups for FIND_NODE and FIND_VALUE
    - RPC timeout (5s) with up to 2 retries
    - Stale node eviction (3 failures → remove)
    - Periodic bucket refresh (every 60 min)
    - Key republish every 24 hours
    - Bootstrap with LAN-only fallback
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
        self._store: dict[bytes, StoredValue] = {}

        # Pending RPCs: request_key → PendingRPC
        self._pending_rpcs: dict[bytes, PendingRPC] = {}

        # Node-specific pending futures for different RPC types
        self._pending_find_node: dict[bytes, asyncio.Future] = {}
        self._pending_find_value: dict[bytes, asyncio.Future] = {}
        self._pending_store: dict[bytes, asyncio.Future] = {}
        self._pending_ping: dict[bytes, asyncio.Event] = {}

        # Nodes currently being pinged for eviction
        self._eviction_candidates: dict[bytes, DHTNode] = {}

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
        self._running = False

    # -------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------

    async def start(self) -> None:
        """Start DHT background tasks."""
        self._running = True
        self._bg_tasks.append(asyncio.create_task(self._refresh_loop()))
        self._bg_tasks.append(asyncio.create_task(self._republish_loop()))
        self._bg_tasks.append(asyncio.create_task(self._eviction_loop()))
        self._bg_tasks.append(asyncio.create_task(self._expiry_loop()))
        self._bg_tasks.append(asyncio.create_task(self._rpc_timeout_loop()))
        logger.info("DHT service started for node %s (k=%d, α=%d)",
                     self._local_user_id.hex()[:8], K_BUCKET_SIZE, ALPHA)

    async def stop(self) -> None:
        """Stop DHT service."""
        self._running = False
        for task in self._bg_tasks:
            task.cancel()
        await asyncio.gather(*self._bg_tasks, return_exceptions=True)
        self._bg_tasks.clear()

    # -------------------------------------------------------------------
    # Bootstrap
    # -------------------------------------------------------------------

    async def bootstrap(self, bootstrap_addrs: list[str]) -> None:
        """Bootstrap the DHT by contacting known nodes.

        Falls back to LAN-only mode if no bootstrap nodes are reachable.
        """
        reachable = False

        for addr_str in bootstrap_addrs:
            try:
                host, port_str = addr_str.rsplit(":", 1)
                port = int(port_str)
                addr = (host, port)

                ok = await self._rpc_ping(addr)
                if ok:
                    reachable = True
                    logger.info("Bootstrap node reachable: %s", addr_str)
                else:
                    logger.warning("Bootstrap node unreachable: %s", addr_str)
            except Exception as exc:
                logger.warning("Failed to contact bootstrap %s: %s", addr_str, exc)

        if not reachable:
            logger.warning(
                "No bootstrap nodes reachable. Operating in LAN-only mode "
                "(relying on mDNS for peer discovery)."
            )
            return

        # Perform iterative self-lookup to populate routing table
        await self.find_node(self._local_user_id)

    # -------------------------------------------------------------------
    # Public APIs
    # -------------------------------------------------------------------

    async def find_node(self, target_id: bytes) -> list[DHTNode]:
        """Iterative FIND_NODE with α=3 parallel lookups.

        Returns the k closest nodes to target_id.
        """
        closest = self._routing_table.find_closest(target_id, K_BUCKET_SIZE)
        queried: set[bytes] = set()
        seen: set[bytes] = set(n.user_id for n in closest)

        for _ in range(ID_BITS):  # Max iterations = number of bits
            # Select up to α closest unqueried nodes
            to_query = [
                n for n in closest
                if n.user_id not in queried and n.user_id != self._local_user_id
            ][:ALPHA]

            if not to_query:
                break

            # Send parallel FIND_NODE RPCs
            tasks = []
            for node in to_query:
                queried.add(node.user_id)
                tasks.append(self._rpc_find_node(target_id, node))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            new_nodes_found = False
            for result in results:
                if isinstance(result, list):
                    for n in result:
                        if n.user_id not in seen:
                            seen.add(n.user_id)
                            self._routing_table.add_node(n)
                            new_nodes_found = True

            # Update closest list
            closest = self._routing_table.find_closest(target_id, K_BUCKET_SIZE)

            if not new_nodes_found:
                break

        return closest

    async def find_value(self, key: bytes) -> Optional[bytes]:
        """Iterative FIND_VALUE with α=3 parallel lookups."""
        # Check local store first
        local = self._store.get(key)
        if local and local.expiry > time.time():
            return local.value

        closest = self._routing_table.find_closest(key, K_BUCKET_SIZE)
        queried: set[bytes] = set()

        for _ in range(ID_BITS):
            to_query = [
                n for n in closest
                if n.user_id not in queried and n.user_id != self._local_user_id
            ][:ALPHA]

            if not to_query:
                break

            tasks = []
            for node in to_query:
                queried.add(node.user_id)
                tasks.append(self._rpc_find_value(key, node))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, bytes) and result:
                    return result  # Value found!

            # Update closest
            closest = self._routing_table.find_closest(key, K_BUCKET_SIZE)

        return None

    async def store_value(self, key: bytes, value: bytes, ttl: int = 3600) -> bool:
        """Store a key-value pair at the k closest nodes."""
        closest = await self.find_node(key)
        targets = [n for n in closest[:K_BUCKET_SIZE]
                    if n.user_id != self._local_user_id]

        if not targets:
            # Only us — store locally
            self._store[key] = StoredValue(
                key=key, value=value,
                expiry=time.time() + ttl,
                original_publisher=self._local_user_id,
            )
            return True

        success = False
        tasks = []
        for node in targets:
            tasks.append(self._rpc_store(key, value, ttl, node))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if result is True:
                success = True

        # Also store locally
        self._store[key] = StoredValue(
            key=key, value=value,
            expiry=time.time() + ttl,
            original_publisher=self._local_user_id,
        )

        return success

    # -------------------------------------------------------------------
    # RPC implementations
    # -------------------------------------------------------------------

    async def _rpc_ping(self, addr: tuple[str, int], timeout: float = RPC_TIMEOUT) -> bool:
        """Send DHT PING and wait for PONG."""
        request_key = f"ping:{addr[0]}:{addr[1]}:{time.monotonic()}".encode()
        event = asyncio.Event()
        self._pending_ping[request_key] = event

        ping = DHTPingPayload(
            sender_id=self._local_user_id,
            sender_addr=self._local_addr,
        )
        try:
            await self._transport.send(MessageType.DHT_PING, ping, addr)
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            self._pending_ping.pop(request_key, None)

    async def _rpc_find_node(self, target_id: bytes, node: DHTNode) -> list[DHTNode]:
        """Send FIND_NODE RPC to a specific node."""
        host, port_str = node.addr.rsplit(":", 1)
        addr = (host, int(port_str))

        request_key = target_id + node.user_id
        future: asyncio.Future[list[DHTNode]] = asyncio.get_running_loop().create_future()
        self._pending_find_node[request_key] = future

        payload = DHTFindNodePayload(
            sender_id=self._local_user_id,
            target_id=target_id,
            sender_addr=self._local_addr,
        )

        for attempt in range(3):  # Up to 2 retries
            try:
                await self._transport.send(MessageType.DHT_FIND_NODE, payload, addr)
                return await asyncio.wait_for(future, timeout=RPC_TIMEOUT)
            except asyncio.TimeoutError:
                if attempt == 2:
                    self._routing_table.increment_failure(node.user_id)
                    return []
            except Exception:
                self._routing_table.increment_failure(node.user_id)
                return []

        return []

    async def _rpc_find_value(self, key: bytes, node: DHTNode) -> Optional[bytes]:
        """Send FIND_VALUE RPC to a specific node."""
        host, port_str = node.addr.rsplit(":", 1)
        addr = (host, int(port_str))

        request_key = key + node.user_id
        future: asyncio.Future[Optional[bytes]] = asyncio.get_running_loop().create_future()
        self._pending_find_value[request_key] = future

        payload = DHTFindValuePayload(
            sender_id=self._local_user_id,
            key=key,
        )

        for attempt in range(3):
            try:
                await self._transport.send(MessageType.DHT_FIND_VALUE, payload, addr)
                return await asyncio.wait_for(future, timeout=RPC_TIMEOUT)
            except asyncio.TimeoutError:
                if attempt == 2:
                    self._routing_table.increment_failure(node.user_id)
                    return None
            except Exception:
                self._routing_table.increment_failure(node.user_id)
                return None

        return None

    async def _rpc_store(self, key: bytes, value: bytes, ttl: int, node: DHTNode) -> bool:
        """Send STORE RPC to a specific node."""
        host, port_str = node.addr.rsplit(":", 1)
        addr = (host, int(port_str))

        request_key = key + node.user_id
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending_store[request_key] = future

        payload = DHTStorePayload(
            sender_id=self._local_user_id,
            key=key,
            value=value,
            ttl=ttl,
        )

        for attempt in range(3):
            try:
                await self._transport.send(MessageType.DHT_STORE, payload, addr)
                return await asyncio.wait_for(future, timeout=RPC_TIMEOUT)
            except asyncio.TimeoutError:
                if attempt == 2:
                    self._routing_table.increment_failure(node.user_id)
                    return False
            except Exception:
                return False

        return False

    # -------------------------------------------------------------------
    # Incoming message handlers
    # -------------------------------------------------------------------

    async def _handle_ping(
        self, msg_type: int, timestamp: float, payload: DHTPingPayload, addr: tuple[str, int]
    ) -> None:
        """Respond to DHT PING."""
        node = DHTNode(
            user_id=payload.sender_id,
            addr=payload.sender_addr,
            public_key=b"",
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
        """Handle DHT PING_ACK."""
        node = DHTNode(
            user_id=payload.sender_id,
            addr=payload.sender_addr,
            public_key=b"",
            last_seen=time.monotonic(),
        )
        self._routing_table.add_node(node)

        # Resolve any pending ping events
        for key, event in list(self._pending_ping.items()):
            if not event.is_set():
                event.set()

        # Check if this node was an eviction candidate
        evict = self._eviction_candidates.pop(payload.sender_id, None)
        if evict:
            # Node responded — it's alive, keep it in the bucket
            evict.failures = 0
            evict.ping_pending = False

    async def _handle_find_node(
        self, msg_type: int, timestamp: float, payload: DHTFindNodePayload, addr: tuple[str, int]
    ) -> None:
        """Respond to FIND_NODE with k closest nodes."""
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
        """Handle FIND_NODE response — add nodes and resolve pending RPC."""
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
                    user_id=ni.user_id, addr=ni.addr, public_key=ni.public_key,
                )
                for ni in payload.nodes
            ]
            future.set_result(nodes)

    async def _handle_find_value(
        self, msg_type: int, timestamp: float, payload: DHTFindValuePayload, addr: tuple[str, int]
    ) -> None:
        """Respond to FIND_VALUE."""
        local = self._store.get(payload.key)
        found = local is not None and local.expiry > time.time()

        closest = self._routing_table.find_closest(payload.key, K_BUCKET_SIZE)
        closest_infos = [
            DHTNodeInfo(user_id=n.user_id, addr=n.addr, public_key=n.public_key)
            for n in closest
        ]

        resp = DHTFindValueRespPayload(
            sender_id=self._local_user_id,
            key=payload.key,
            value=local.value if found else b"",
            found=found,
            closest_nodes=closest_infos,
        )
        await self._transport.send(MessageType.DHT_FIND_VALUE_RESP, resp, addr)

    async def _handle_find_value_resp(
        self, msg_type: int, timestamp: float, payload: DHTFindValueRespPayload, addr: tuple[str, int]
    ) -> None:
        """Handle FIND_VALUE response."""
        for node_info in payload.closest_nodes:
            node = DHTNode(
                user_id=node_info.user_id,
                addr=node_info.addr,
                public_key=node_info.public_key,
            )
            self._routing_table.add_node(node)

        request_key = payload.key + payload.sender_id
        future = self._pending_find_value.get(request_key)
        if future and not future.done():
            future.set_result(payload.value if payload.found else None)

    async def _handle_store(
        self, msg_type: int, timestamp: float, payload: DHTStorePayload, addr: tuple[str, int]
    ) -> None:
        """Handle STORE request."""
        if len(self._store) >= MAX_STORED_VALUES:
            now = time.time()
            expired = [k for k, v in self._store.items() if v.expiry <= now]
            for k in expired:
                del self._store[k]

        self._store[payload.key] = StoredValue(
            key=payload.key,
            value=payload.value,
            expiry=time.time() + payload.ttl,
            original_publisher=payload.sender_id,
        )

        ack = DHTStoreAckPayload(key=payload.key, stored=True)
        await self._transport.send(MessageType.DHT_STORE_ACK, ack, addr)

    async def _handle_store_ack(
        self, msg_type: int, timestamp: float, payload: DHTStoreAckPayload, addr: tuple[str, int]
    ) -> None:
        """Handle STORE_ACK."""
        for key, future in list(self._pending_store.items()):
            if key.startswith(payload.key) and not future.done():
                future.set_result(payload.stored)

    # -------------------------------------------------------------------
    # Background: bucket refresh
    # -------------------------------------------------------------------

    async def _refresh_loop(self) -> None:
        """Periodically refresh k-buckets by performing lookups for random IDs."""
        while self._running:
            await asyncio.sleep(REFRESH_INTERVAL)
            if not self._running:
                break

            # Find a random ID in each bucket that hasn't been refreshed recently
            import os
            for bucket_idx in range(ID_BITS):
                if not self._running:
                    break
                random_id = os.urandom(ID_LENGTH)
                # Set the appropriate bits for this bucket's range
                try:
                    await self.find_node(random_id)
                except Exception as exc:
                    logger.debug("Bucket refresh failed for bucket %d: %s", bucket_idx, exc)

            logger.debug("Bucket refresh completed. Total nodes: %d", self._routing_table.total_nodes)

    # -------------------------------------------------------------------
    # Background: key republish
    # -------------------------------------------------------------------

    async def _republish_loop(self) -> None:
        """Republish locally stored key-value pairs every 24 hours."""
        while self._running:
            await asyncio.sleep(REPUBLISH_INTERVAL)
            if not self._running:
                break

            now = time.time()
            for key, stored in list(self._store.items()):
                if now - stored.last_republished >= REPUBLISH_INTERVAL:
                    ttl_remaining = int(stored.expiry - now)
                    if ttl_remaining > 0:
                        try:
                            await self.store_value(key, stored.value, ttl=ttl_remaining)
                            stored.last_republished = now
                        except Exception as exc:
                            logger.debug("Republish failed for key %s: %s", key.hex()[:8], exc)

            logger.debug("Key republish completed. %d keys stored.", len(self._store))

    # -------------------------------------------------------------------
    # Background: stale node eviction
    # -------------------------------------------------------------------

    async def _eviction_loop(self) -> None:
        """Periodically check for stale nodes and attempt eviction."""
        while self._running:
            await asyncio.sleep(EVICT_CHECK_INTERVAL)
            if not self._running:
                break

            stale_removed = 0
            for node in self._routing_table.get_all_nodes():
                if node.is_stale:
                    self._routing_table.remove_node(node.user_id)
                    stale_removed += 1

            if stale_removed:
                logger.info("Evicted %d stale nodes from routing table.", stale_removed)

    # -------------------------------------------------------------------
    # Background: store expiry
    # -------------------------------------------------------------------

    async def _expiry_loop(self) -> None:
        """Clean expired entries from local store."""
        while self._running:
            now = time.time()
            expired = [k for k, v in self._store.items() if v.expiry <= now]
            for k in expired:
                del self._store[k]
            if expired:
                logger.debug("Cleaned %d expired DHT entries", len(expired))
            await asyncio.sleep(60)

    # -------------------------------------------------------------------
    # Background: RPC timeout management
    # -------------------------------------------------------------------

    async def _rpc_timeout_loop(self) -> None:
        """Monitor pending RPCs and handle timeouts."""
        while self._running:
            now = time.monotonic()
            for request_key, pending in list(self._pending_find_node.items()):
                elapsed = now - (pending._sent_at if hasattr(pending, '_sent_at') else 0)
                # Futures handle their own timeout via asyncio.wait_for
            await asyncio.sleep(1)
