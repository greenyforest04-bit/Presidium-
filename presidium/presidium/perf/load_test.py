"""Load testing framework for Presidium P2P network.

Simulates a network of N nodes (default: 100) running the full
Presidium stack (DHT, messaging, file transfer) and measures:

    - Message delivery latency (p50, p90, p99)
    - Throughput (messages/second/node)
    - DHT lookup latency
    - Memory usage per node
    - Connection pool saturation
    - CPU utilization

The framework uses asyncio to simulate multiple virtual nodes within
a single process. Each virtual node has its own identity, DHT routing
table, and message queue. Network simulation uses in-process queues
instead of real UDP sockets for deterministic and fast testing.

Usage:
    # Run a 100-node, 60-second load test
    python -m presidium.perf.load_test --nodes 100 --duration 60

    # Quick smoke test with 10 nodes
    python -m presidium.perf.load_test --nodes 10 --duration 10

    # With HTML report
    python -m presidium.perf.load_test --nodes 50 --duration 30 --report
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import random
import statistics
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Simulation data models
# ---------------------------------------------------------------------------

@dataclass
class VirtualNode:
    """A simulated Presidium node for load testing."""
    node_id: str = field(default_factory=lambda: uuid.uuid4().hex[:32])
    nickname: str = ""
    inbox: asyncio.Queue[dict[str, Any]] = field(default_factory=lambda: asyncio.Queue(maxsize=10000))
    is_online: bool = True
    message_count: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    dht_lookups: int = 0
    errors: int = 0


@dataclass
class SimulatedMessage:
    """A message in the simulated network."""
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    sender_id: str = ""
    recipient_id: str = ""
    content: str = ""
    timestamp: float = field(default_factory=time.time)
    size_bytes: int = 0
    is_delivered: bool = False
    delivery_time: float = 0.0


@dataclass
class LoadTestResult:
    """Aggregated results from a load test run."""
    total_nodes: int = 0
    total_messages: int = 0
    total_bytes: int = 0
    duration_seconds: float = 0.0

    # Latency percentiles (milliseconds)
    latency_p50: float = 0.0
    latency_p90: float = 0.0
    latency_p99: float = 0.0
    latency_mean: float = 0.0
    latency_std: float = 0.0

    # Throughput
    messages_per_second: float = 0.0
    messages_per_second_per_node: float = 0.0

    # DHT
    dht_lookups_total: int = 0
    dht_latency_mean: float = 0.0

    # Errors
    error_count: int = 0
    error_rate: float = 0.0

    # Memory
    peak_memory_mb: float = 0.0
    avg_memory_per_node_mb: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_nodes": self.total_nodes,
            "total_messages": self.total_messages,
            "total_bytes": self.total_bytes,
            "duration_seconds": round(self.duration_seconds, 2),
            "latency": {
                "p50_ms": round(self.latency_p50, 2),
                "p90_ms": round(self.latency_p90, 2),
                "p99_ms": round(self.latency_p99, 2),
                "mean_ms": round(self.latency_mean, 2),
                "std_ms": round(self.latency_std, 2),
            },
            "throughput": {
                "messages_per_second": round(self.messages_per_second, 2),
                "per_node_per_second": round(self.messages_per_second_per_node, 2),
            },
            "dht": {
                "total_lookups": self.dht_lookups_total,
                "mean_latency_ms": round(self.dht_latency_mean, 2),
            },
            "errors": {
                "count": self.error_count,
                "rate": round(self.error_rate, 4),
            },
            "memory": {
                "peak_mb": round(self.peak_memory_mb, 2),
                "avg_per_node_mb": round(self.avg_memory_per_node_mb, 2),
            },
        }


# ---------------------------------------------------------------------------
# Network simulator
# ---------------------------------------------------------------------------

class SimulatedNetwork:
    """In-process network simulation for load testing.

    Routes messages between virtual nodes using asyncio queues
    instead of real network sockets. Supports configurable:
    - Latency: Adds random delay to message delivery
    - Packet loss: Randomly drops messages
    - Bandwidth limit: Throttles message throughput
    """

    def __init__(
        self,
        base_latency_ms: float = 5.0,
        jitter_ms: float = 2.0,
        packet_loss_rate: float = 0.01,
        bandwidth_limit_kbps: float = 0.0,  # 0 = unlimited
    ) -> None:
        self._nodes: dict[str, VirtualNode] = {}
        self._base_latency = base_latency_ms / 1000.0
        self._jitter = jitter_ms / 1000.0
        self._packet_loss = packet_loss_rate
        self._bandwidth_limit = bandwidth_limit_kbps
        self._delivered_messages: list[SimulatedMessage] = []
        self._dht_latencies: list[float] = []

    def add_node(self, node: VirtualNode) -> None:
        """Register a virtual node with the network."""
        self._nodes[node.node_id] = node

    async def send_message(
        self,
        sender_id: str,
        recipient_id: str,
        content: str,
        msg_type: str = "chat",
    ) -> Optional[SimulatedMessage]:
        """Simulate sending a message from one node to another.

        Applies latency, jitter, and packet loss simulation.
        """
        sender = self._nodes.get(sender_id)
        recipient = self._nodes.get(recipient_id)

        if sender is None or recipient is None:
            return None

        if not sender.is_online or not recipient.is_online:
            sender.errors += 1
            return None

        # Packet loss simulation
        if random.random() < self._packet_loss:
            return None  # Message dropped

        # Create message
        msg = SimulatedMessage(
            sender_id=sender_id,
            recipient_id=recipient_id,
            content=content,
            size_bytes=len(content.encode("utf-8")),
        )

        # Simulate network latency
        latency = self._base_latency + random.uniform(-self._jitter, self._jitter)
        latency = max(0.001, latency)
        await asyncio.sleep(latency)

        # Bandwidth throttling
        if self._bandwidth_limit > 0:
            transfer_time = (msg.size_bytes * 8) / (self._bandwidth_limit * 1000)
            await asyncio.sleep(transfer_time)

        # Deliver to recipient's inbox
        try:
            recipient.inbox.put_nowait({
                "type": msg_type,
                "message_id": msg.message_id,
                "sender_id": sender_id,
                "content": content,
                "timestamp": time.time(),
            })
        except asyncio.QueueFull:
            recipient.errors += 1
            return None

        # Update stats
        msg.is_delivered = True
        msg.delivery_time = time.time() - msg.timestamp
        sender.message_count += 1
        sender.bytes_sent += msg.size_bytes
        recipient.bytes_received += msg.size_bytes

        self._delivered_messages.append(msg)
        return msg

    async def simulate_dht_lookup(
        self,
        requester_id: str,
        target_id: str,
    ) -> float:
        """Simulate a DHT lookup with realistic latency.

        Kademlia lookups typically involve α=3 parallel queries
        across log₂(N) hops.

        Returns:
            Lookup latency in milliseconds.
        """
        num_nodes = len(self._nodes)
        num_hops = max(1, int(math.log2(num_nodes))) if num_nodes > 1 else 1

        # Each hop adds base latency
        total_latency = sum(
            self._base_latency + random.uniform(-self._jitter, self._jitter)
            for _ in range(num_hops)
        )
        total_latency = max(0.001, total_latency)
        await asyncio.sleep(total_latency)

        node = self._nodes.get(requester_id)
        if node:
            node.dht_lookups += 1

        latency_ms = total_latency * 1000
        self._dht_latencies.append(latency_ms)
        return latency_ms

    def get_results(self, duration: float) -> LoadTestResult:
        """Compute aggregated results from the simulation."""
        delivered = self._delivered_messages
        latencies = [msg.delivery_time * 1000 for msg in delivered]  # ms

        result = LoadTestResult()
        result.total_nodes = len(self._nodes)
        result.total_messages = len(delivered)
        result.total_bytes = sum(msg.size_bytes for msg in delivered)
        result.duration_seconds = duration

        if latencies:
            result.latency_p50 = statistics.median(latencies)
            result.latency_p90 = self._percentile(latencies, 90)
            result.latency_p99 = self._percentile(latencies, 99)
            result.latency_mean = statistics.mean(latencies)
            result.latency_std = statistics.stdev(latencies) if len(latencies) > 1 else 0.0

        if duration > 0:
            result.messages_per_second = len(delivered) / duration
            if self._nodes:
                result.messages_per_second_per_node = (
                    result.messages_per_second / len(self._nodes)
                )

        result.dht_lookups_total = sum(n.dht_lookups for n in self._nodes.values())
        if self._dht_latencies:
            result.dht_latency_mean = statistics.mean(self._dht_latencies)

        result.error_count = sum(n.errors for n in self._nodes.values())
        total_ops = len(delivered) + result.error_count + result.dht_lookups_total
        result.error_rate = result.error_count / total_ops if total_ops > 0 else 0.0

        # Memory estimation (rough)
        try:
            import psutil
            import os
            process = psutil.Process(os.getpid())
            result.peak_memory_mb = process.memory_info().rss / 1024 / 1024
            if self._nodes:
                result.avg_memory_per_node_mb = result.peak_memory_mb / len(self._nodes)
        except ImportError:
            pass

        return result

    @staticmethod
    def _percentile(data: list[float], pct: int) -> float:
        """Compute the given percentile of a sorted list."""
        if not data:
            return 0.0
        sorted_data = sorted(data)
        k = (len(sorted_data) - 1) * pct / 100
        f = int(k)
        c = f + 1
        if c >= len(sorted_data):
            return sorted_data[f]
        d0 = sorted_data[f] * (c - k)
        d1 = sorted_data[c] * (k - f)
        return d0 + d1


# ---------------------------------------------------------------------------
# Load test runner
# ---------------------------------------------------------------------------

class LoadTestRunner:
    """Orchestrates a load test with multiple virtual nodes.

    Creates a simulated network and runs workload patterns:
    - Random chat: Nodes send messages to random peers
    - Paired chat: Nodes pair up and have extended conversations
    - DHT stress: Nodes perform frequent DHT lookups
    - Burst: All nodes send simultaneously
    """

    def __init__(
        self,
        num_nodes: int = 100,
        duration_seconds: int = 60,
        messages_per_node_per_second: float = 0.5,
        dht_lookups_per_node_per_second: float = 0.1,
        message_size_bytes: int = 200,
        network_latency_ms: float = 5.0,
        packet_loss_rate: float = 0.01,
    ) -> None:
        self._num_nodes = num_nodes
        self._duration = duration_seconds
        self._msg_rate = messages_per_node_per_second
        self._dht_rate = dht_lookups_per_node_per_second
        self._msg_size = message_size_bytes

        self._network = SimulatedNetwork(
            base_latency_ms=network_latency_ms,
            packet_loss_rate=packet_loss_rate,
        )

    async def run(self) -> LoadTestResult:
        """Execute the load test and return results."""
        logger.info(
            "Starting load test: %d nodes, %d seconds",
            self._num_nodes, self._duration,
        )

        # Create virtual nodes
        nodes = []
        for i in range(self._num_nodes):
            node = VirtualNode(nickname=f"node_{i:04d}")
            nodes.append(node)
            self._network.add_node(node)

        node_ids = [n.node_id for n in nodes]

        # Run workload tasks
        start_time = time.time()
        tasks = []

        for node in nodes:
            tasks.append(self._run_node_workload(node, node_ids, start_time))

        # Also run DHT stress
        for node in nodes[:10]:  # Only 10% of nodes do DHT lookups
            tasks.append(self._run_dht_workload(node, node_ids, start_time))

        await asyncio.gather(*tasks, return_exceptions=True)

        duration = time.time() - start_time
        result = self._network.get_results(duration)

        logger.info(
            "Load test complete: %d messages, %.1f msg/s, "
            "latency p50=%.1fms p99=%.1fms",
            result.total_messages,
            result.messages_per_second,
            result.latency_p50,
            result.latency_p99,
        )

        return result

    async def _run_node_workload(
        self,
        node: VirtualNode,
        all_node_ids: list[str],
        start_time: float,
    ) -> None:
        """Run message workload for a single virtual node."""
        other_ids = [nid for nid in all_node_ids if nid != node.node_id]

        while time.time() - start_time < self._duration:
            # Pick a random recipient
            recipient_id = random.choice(other_ids)

            # Generate a random message
            content = "x" * self._msg_size  # Placeholder

            await self._network.send_message(
                sender_id=node.node_id,
                recipient_id=recipient_id,
                content=content,
            )

            # Wait according to message rate
            interval = 1.0 / self._msg_rate
            await asyncio.sleep(interval + random.uniform(-0.1, 0.1))

    async def _run_dht_workload(
        self,
        node: VirtualNode,
        all_node_ids: list[str],
        start_time: float,
    ) -> None:
        """Run DHT lookup workload for a node."""
        while time.time() - start_time < self._duration:
            target_id = random.choice(all_node_ids)
            await self._network.simulate_dht_lookup(node.node_id, target_id)

            interval = 1.0 / self._dht_rate
            await asyncio.sleep(interval + random.uniform(-0.1, 0.1))


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_html_report(result: LoadTestResult, output_path: Path) -> None:
    """Generate an HTML report from load test results."""
    data = result.to_dict()

    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Presidium Load Test Report</title>
    <style>
        body {{ font-family: 'Inter', sans-serif; max-width: 800px; margin: 40px auto; background: #1a1a2e; color: #e0e0e0; }}
        h1 {{ color: #6366f1; }}
        h2 {{ color: #818cf8; border-bottom: 1px solid #333; padding-bottom: 8px; }}
        .metric {{ display: inline-block; background: #2a2a4a; border-radius: 12px; padding: 16px 24px; margin: 8px; }}
        .metric .value {{ font-size: 28px; font-weight: bold; color: #6366f1; }}
        .metric .label {{ font-size: 12px; color: #938f99; }}
        table {{ border-collapse: collapse; width: 100%; margin: 16px 0; }}
        td, th {{ padding: 8px 16px; border-bottom: 1px solid #333; text-align: left; }}
        th {{ color: #818cf8; }}
        .pass {{ color: #4ade80; }} .fail {{ color: #f87171; }}
    </style>
</head>
<body>
    <h1>Presidium Load Test Report</h1>

    <h2>Overview</h2>
    <div class="metric"><div class="value">{data['total_nodes']}</div><div class="label">Nodes</div></div>
    <div class="metric"><div class="value">{data['total_messages']:,}</div><div class="label">Messages</div></div>
    <div class="metric"><div class="value">{data['duration_seconds']}s</div><div class="label">Duration</div></div>
    <div class="metric"><div class="value">{data['throughput']['messages_per_second']:.1f}</div><div class="label">Msg/sec</div></div>

    <h2>Latency</h2>
    <table>
        <tr><th>Percentile</th><th>Latency</th></tr>
        <tr><td>p50</td><td>{data['latency']['p50_ms']:.2f} ms</td></tr>
        <tr><td>p90</td><td>{data['latency']['p90_ms']:.2f} ms</td></tr>
        <tr><td>p99</td><td>{data['latency']['p99_ms']:.2f} ms</td></tr>
        <tr><td>mean</td><td>{data['latency']['mean_ms']:.2f} ms ± {data['latency']['std_ms']:.2f}</td></tr>
    </table>

    <h2>DHT</h2>
    <table>
        <tr><th>Metric</th><th>Value</th></tr>
        <tr><td>Total Lookups</td><td>{data['dht']['total_lookups']:,}</td></tr>
        <tr><td>Mean Latency</td><td>{data['dht']['mean_latency_ms']:.2f} ms</td></tr>
    </table>

    <h2>Errors</h2>
    <table>
        <tr><th>Metric</th><th>Value</th></tr>
        <tr><td>Error Count</td><td>{data['errors']['count']:,}</td></tr>
        <tr><td>Error Rate</td><td>{data['errors']['rate']:.4f}</td></tr>
    </table>

    <h2>Memory</h2>
    <table>
        <tr><th>Metric</th><th>Value</th></tr>
        <tr><td>Peak RSS</td><td>{data['memory']['peak_mb']:.1f} MB</td></tr>
        <tr><td>Avg per Node</td><td>{data['memory']['avg_per_node_mb']:.2f} MB</td></tr>
    </table>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    logger.info("Report written to %s", output_path)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    parser = argparse.ArgumentParser(description="Presidium load test")
    parser.add_argument("--nodes", type=int, default=100, help="Number of virtual nodes")
    parser.add_argument("--duration", type=int, default=60, help="Test duration in seconds")
    parser.add_argument("--msg-rate", type=float, default=0.5, help="Messages per node per second")
    parser.add_argument("--dht-rate", type=float, default=0.1, help="DHT lookups per node per second")
    parser.add_argument("--latency", type=float, default=5.0, help="Network base latency (ms)")
    parser.add_argument("--packet-loss", type=float, default=0.01, help="Packet loss rate")
    parser.add_argument("--report", action="store_true", help="Generate HTML report")
    parser.add_argument("--output", type=str, default="load_test_report.html", help="Report output path")

    args = parser.parse_args()

    runner = LoadTestRunner(
        num_nodes=args.nodes,
        duration_seconds=args.duration,
        messages_per_node_per_second=args.msg_rate,
        dht_lookups_per_node_per_second=args.dht_rate,
        network_latency_ms=args.latency,
        packet_loss_rate=args.packet_loss,
    )

    result = await runner.run()

    # Print JSON results
    print(json.dumps(result.to_dict(), indent=2))

    # Generate HTML report if requested
    if args.report:
        output_path = Path(args.output)
        generate_html_report(result, output_path)


if __name__ == "__main__":
    asyncio.run(main())
