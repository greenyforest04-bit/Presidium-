"""Tests for the Kademlia DHT implementation.

Tests:
- XOR distance computation
- K-bucket management (add, update, eviction)
- Routing table closest-nodes query
- Stale node eviction
"""

from __future__ import annotations

import os
import pytest
import time

from presidium.core.network.dht import (
    xor_distance,
    bucket_index_for_distance,
    KBucket,
    K_BUCKET_SIZE,
    DHTNode,
    RoutingTable,
    StoredValue,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def random_node_id() -> bytes:
    return os.urandom(16)


def make_node(uid: bytes = b"", addr: str = "") -> DHTNode:
    return DHTNode(
        user_id=uid or random_node_id(),
        addr=addr or "127.0.0.1:9478",
        public_key=os.urandom(32),
        last_seen=time.monotonic(),
    )


# ---------------------------------------------------------------------------
# Tests: XOR distance
# ---------------------------------------------------------------------------

class TestXORDistance:
    def test_same_id_zero_distance(self) -> None:
        uid = random_node_id()
        assert xor_distance(uid, uid) == 0

    def test_commutative(self) -> None:
        a, b = random_node_id(), random_node_id()
        assert xor_distance(a, b) == xor_distance(b, a)

    def test_all_bits_different_max(self) -> None:
        a = b"\x00" * 16
        b = b"\xff" * 16
        dist = xor_distance(a, b)
        assert dist == (1 << 128) - 1

    def test_length_mismatch_raises(self) -> None:
        with pytest.raises(ValueError, match="mismatch"):
            xor_distance(b"\x00" * 16, b"\x00" * 8)


class TestBucketIndex:
    def test_zero_distance(self) -> None:
        assert bucket_index_for_distance(0) == 0

    def test_distance_one(self) -> None:
        assert bucket_index_for_distance(1) == 1

    def test_distance_256(self) -> None:
        assert bucket_index_for_distance(256) == 9  # 2^8 = 256


# ---------------------------------------------------------------------------
# Tests: KBucket
# ---------------------------------------------------------------------------

class TestKBucket:
    def test_add_and_contains(self) -> None:
        bucket = KBucket(k=4)
        node = make_node()
        bucket.add_or_update(node)
        assert node.user_id in bucket
        assert len(bucket) == 1

    def test_add_moves_to_tail(self) -> None:
        bucket = KBucket(k=4)
        n1 = make_node()
        n2 = make_node()
        bucket.add_or_update(n1)
        bucket.add_or_update(n2)
        # Re-add n1 — should move to tail
        bucket.add_or_update(n1)
        assert bucket.head == n2
        assert bucket.tail == n1

    def test_full_bucket_returns_head(self) -> None:
        bucket = KBucket(k=2)
        n1 = make_node()
        n2 = make_node()
        bucket.add_or_update(n1)
        bucket.add_or_update(n2)
        n3 = make_node()
        result = bucket.add_or_update(n3)
        # Bucket full — return head (n1) for eviction check
        assert result == n1

    def test_remove(self) -> None:
        bucket = KBucket(k=4)
        node = make_node()
        bucket.add_or_update(node)
        bucket.remove(node.user_id)
        assert node.user_id not in bucket
        assert len(bucket) == 0

    def test_stale_node_detection(self) -> None:
        node = make_node()
        node.failures = 3
        assert node.is_stale

    def test_replace_stale(self) -> None:
        bucket = KBucket(k=2)
        old = make_node()
        bucket.add_or_update(old)
        new = make_node()
        # Mark old as stale
        old.failures = 3
        replaced = bucket.replace_if_stale(old.user_id, new)
        assert replaced
        assert new.user_id in bucket
        assert old.user_id not in bucket


# ---------------------------------------------------------------------------
# Tests: Routing Table
# ---------------------------------------------------------------------------

class TestRoutingTable:
    def test_add_and_find_closest(self) -> None:
        local_id = random_node_id()
        rt = RoutingTable(local_id)
        nodes = [make_node() for _ in range(5)]
        for n in nodes:
            rt.add_node(n)

        # Find closest to target
        target = random_node_id()
        closest = rt.find_closest(target, 3)
        assert len(closest) <= 3
        assert len(closest) > 0

    def test_dont_add_self(self) -> None:
        local_id = random_node_id()
        rt = RoutingTable(local_id)
        node = DHTNode(
            user_id=local_id,
            addr="127.0.0.1:9478",
            public_key=b"",
        )
        result = rt.add_node(node)
        assert result is None
        assert rt.total_nodes == 0

    def test_remove_node(self) -> None:
        local_id = random_node_id()
        rt = RoutingTable(local_id)
        node = make_node()
        rt.add_node(node)
        assert rt.total_nodes == 1
        rt.remove_node(node.user_id)
        assert rt.total_nodes == 0

    def test_increment_failure(self) -> None:
        local_id = random_node_id()
        rt = RoutingTable(local_id)
        node = make_node()
        rt.add_node(node)
        rt.increment_failure(node.user_id)
        rt.increment_failure(node.user_id)
        rt.increment_failure(node.user_id)
        # After 3 failures, node should be stale
        all_nodes = rt.get_all_nodes()
        assert any(n.user_id == node.user_id and n.is_stale for n in all_nodes)

    def test_many_nodes_across_buckets(self) -> None:
        local_id = random_node_id()
        rt = RoutingTable(local_id)
        # Add 50 nodes — they should distribute across buckets
        for _ in range(50):
            rt.add_node(make_node())
        assert rt.total_nodes == 50
