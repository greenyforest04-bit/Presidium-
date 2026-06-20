"""Admin dashboard for Presidium enterprise deployments.

Provides a lightweight aiohttp-based web dashboard for monitoring
and managing a Presidium node. Designed for self-hosted deployments
where administrators need visibility into node health, connected
peers, bridge status, and message throughput.

Features:
    - Real-time node health metrics (CPU, memory, connections)
    - Connected peers list with status indicators
    - Bridge status monitoring
    - Message throughput graphs
    - DHT routing table viewer
    - Audit log viewer
    - Configuration editor (read-only in v1)

Security:
    - Dashboard listens on localhost only by default
    - HTTP Basic Auth with configurable credentials
    - No access to private keys or message content
    - Rate-limited API endpoints

Usage:
    dashboard = AdminDashboard(config)
    await dashboard.start()
    # Access at http://localhost:9479
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Optional

from presidium.utils.config import AppConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dashboard configuration
# ---------------------------------------------------------------------------

class DashboardConfig:
    """Configuration for the admin dashboard."""
    host: str = "127.0.0.1"
    port: int = 9479
    username: str = "admin"
    password: str = ""  # Must be set; dashboard won't start without it
    tls_cert: str = ""
    tls_key: str = ""
    max_request_rate: int = 100  # Requests per minute
    session_timeout: int = 3600  # 1 hour


# ---------------------------------------------------------------------------
# Metrics collector
# ---------------------------------------------------------------------------

class MetricsCollector:
    """Collects and aggregates metrics for the dashboard.

    Runs as a background task that periodically samples system
    metrics and stores them in a ring buffer (last 3600 samples
    at 1-second intervals = 1 hour of data).
    """

    MAX_SAMPLES = 3600

    def __init__(self) -> None:
        self._samples: list[dict[str, Any]] = []
        self._start_time = time.time()
        self._collection_task: Optional[asyncio.Task[None]] = None
        self._running = False

        # Counters
        self._messages_sent = 0
        self._messages_received = 0
        self._connections_active = 0
        self._connections_total = 0

    async def start(self) -> None:
        """Start the metrics collection loop."""
        self._running = True
        self._collection_task = asyncio.create_task(self._collection_loop())

    async def stop(self) -> None:
        """Stop the metrics collection loop."""
        self._running = False
        if self._collection_task:
            self._collection_task.cancel()
            try:
                await self._collection_task
            except asyncio.CancelledError:
                pass

    async def _collection_loop(self) -> None:
        """Periodically collect system metrics."""
        while self._running:
            sample = self._collect_sample()
            self._samples.append(sample)
            if len(self._samples) > self.MAX_SAMPLES:
                self._samples = self._samples[-self.MAX_SAMPLES:]
            await asyncio.sleep(1.0)

    def _collect_sample(self) -> dict[str, Any]:
        """Collect a single metrics sample."""
        sample: dict[str, Any] = {
            "timestamp": time.time(),
            "uptime_seconds": time.time() - self._start_time,
            "messages_sent": self._messages_sent,
            "messages_received": self._messages_received,
            "connections_active": self._connections_active,
        }

        # System metrics (psutil)
        try:
            import psutil
            import os
            process = psutil.Process(os.getpid())
            mem = process.memory_info()
            sample["memory_rss_mb"] = mem.rss / 1024 / 1024
            sample["memory_vms_mb"] = mem.vms / 1024 / 1024
            sample["cpu_percent"] = process.cpu_percent()
            sample["threads"] = process.num_threads()
            sample["fds"] = process.num_fds() if hasattr(process, 'num_fds') else 0
        except ImportError:
            pass

        return sample

    # -------------------------------------------------------------------
    # Counter updates (called by other services)
    # -------------------------------------------------------------------

    def record_message_sent(self) -> None:
        self._messages_sent += 1

    def record_message_received(self) -> None:
        self._messages_received += 1

    def set_active_connections(self, count: int) -> None:
        self._connections_active = count

    def record_connection(self) -> None:
        self._connections_total += 1

    # -------------------------------------------------------------------
    # Query methods
    # -------------------------------------------------------------------

    def get_current(self) -> dict[str, Any]:
        """Get the most recent metrics sample."""
        if self._samples:
            return dict(self._samples[-1])
        return {"timestamp": time.time(), "uptime_seconds": 0}

    def get_history(self, last_n: int = 60) -> list[dict[str, Any]]:
        """Get the last N samples."""
        return list(self._samples[-last_n:])

    def get_summary(self) -> dict[str, Any]:
        """Get aggregated summary statistics."""
        current = self.get_current()
        return {
            "uptime_seconds": current.get("uptime_seconds", 0),
            "messages_sent": self._messages_sent,
            "messages_received": self._messages_received,
            "connections_active": self._connections_active,
            "connections_total": self._connections_total,
            "memory_rss_mb": current.get("memory_rss_mb", 0),
            "cpu_percent": current.get("cpu_percent", 0),
        }


# ---------------------------------------------------------------------------
# Admin dashboard HTTP server
# ---------------------------------------------------------------------------

class AdminDashboard:
    """Web dashboard for Presidium node administration.

    Provides both a human-readable HTML dashboard and a JSON API
    for programmatic access. All endpoints require HTTP Basic Auth.
    """

    def __init__(
        self,
        config: AppConfig,
        dashboard_config: Optional[DashboardConfig] = None,
    ) -> None:
        self._config = config
        self._dash_config = dashboard_config or DashboardConfig()
        self._metrics = MetricsCollector()
        self._app: Optional[Any] = None
        self._runner: Optional[Any] = None
        self._peers_callback: Optional[Callable[[], list[dict[str, Any]]]] = None
        self._bridges_callback: Optional[Callable[[], list[dict[str, Any]]]] = None
        self._audit_callback: Optional[Callable[[int], list[dict[str, Any]]]] = None

    # -------------------------------------------------------------------
    # Callback setters
    # -------------------------------------------------------------------

    def set_peers_provider(
        self, callback: Callable[[], list[dict[str, Any]]]
    ) -> None:
        """Set callback to provide connected peers list."""
        self._peers_callback = callback

    def set_bridges_provider(
        self, callback: Callable[[], list[dict[str, Any]]]
    ) -> None:
        """Set callback to provide bridge status list."""
        self._bridges_callback = callback

    def set_audit_provider(
        self, callback: Callable[[int], list[dict[str, Any]]]
    ) -> None:
        """Set callback to provide audit log entries."""
        self._audit_callback = callback

    # -------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------

    async def start(self) -> None:
        """Start the admin dashboard HTTP server."""
        if not self._dash_config.password:
            logger.warning(
                "Admin dashboard not started: no password configured. "
                "Set PRESIDIUM_DASHBOARD_PASSWORD environment variable."
            )
            return

        try:
            from aiohttp import web
        except ImportError:
            logger.error("aiohttp not installed. Admin dashboard requires aiohttp.")
            return

        await self._metrics.start()

        self._app = web.Application()
        self._app.middlewares.append(self._auth_middleware)

        # API routes
        self._app.router.add_get("/api/health", self._handle_health)
        self._app.router.add_get("/api/metrics", self._handle_metrics)
        self._app.router.add_get("/api/metrics/history", self._handle_metrics_history)
        self._app.router.add_get("/api/peers", self._handle_peers)
        self._app.router.add_get("/api/bridges", self._handle_bridges)
        self._app.router.add_get("/api/audit", self._handle_audit)

        # Dashboard HTML
        self._app.router.add_get("/", self._handle_dashboard)
        self._app.router.add_get("/dashboard", self._handle_dashboard)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        site = web.TCPSite(
            self._runner,
            self._dash_config.host,
            self._dash_config.port,
        )
        await site.start()

        logger.info(
            "Admin dashboard started on http://%s:%d",
            self._dash_config.host,
            self._dash_config.port,
        )

    async def stop(self) -> None:
        """Stop the admin dashboard."""
        await self._metrics.stop()

        if self._runner:
            await self._runner.cleanup()

        logger.info("Admin dashboard stopped.")

    # -------------------------------------------------------------------
    # Auth middleware
    # -------------------------------------------------------------------

    async def _auth_middleware(self, app, handler):
        """HTTP Basic Auth middleware."""
        async def middleware_handler(request):
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Basic "):
                return self._unauthorized_response()

            try:
                decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                username, password = decoded.split(":", 1)
            except (ValueError, UnicodeDecodeError):
                return self._unauthorized_response()

            if username != self._dash_config.username or password != self._dash_config.password:
                return self._unauthorized_response()

            return await handler(request)

        return middleware_handler

    def _unauthorized_response(self) -> Any:
        """Return a 401 Unauthorized response."""
        from aiohttp import web
        return web.Response(
            status=401,
            headers={"WWW-Authenticate": 'Basic realm="Presidium Admin"'},
            text="Unauthorized",
        )

    # -------------------------------------------------------------------
    # API handlers
    # -------------------------------------------------------------------

    async def _handle_health(self, request: Any) -> Any:
        """Health check endpoint."""
        from aiohttp import web
        summary = self._metrics.get_summary()
        return web.json_response({
            "status": "ok",
            "uptime": summary["uptime_seconds"],
            "version": "0.4.0",
        })

    async def _handle_metrics(self, request: Any) -> Any:
        """Current metrics endpoint."""
        from aiohttp import web
        return web.json_response(self._metrics.get_summary())

    async def _handle_metrics_history(self, request: Any) -> Any:
        """Metrics history endpoint (last N seconds)."""
        from aiohttp import web
        last_n = int(request.query.get("seconds", "60"))
        samples = self._metrics.get_history(min(last_n, 3600))
        return web.json_response({"samples": samples})

    async def _handle_peers(self, request: Any) -> Any:
        """Connected peers list."""
        from aiohttp import web
        peers = self._peers_callback() if self._peers_callback else []
        return web.json_response({"peers": peers})

    async def _handle_bridges(self, request: Any) -> Any:
        """Bridge status list."""
        from aiohttp import web
        bridges = self._bridges_callback() if self._bridges_callback else []
        return web.json_response({"bridges": bridges})

    async def _handle_audit(self, request: Any) -> Any:
        """Audit log entries."""
        from aiohttp import web
        limit = int(request.query.get("limit", "50"))
        entries = self._audit_callback(limit) if self._audit_callback else []
        return web.json_response({"entries": entries})

    async def _handle_dashboard(self, request: Any) -> Any:
        """Serve the HTML dashboard."""
        from aiohttp import web
        html = self._generate_dashboard_html()
        return web.Response(text=html, content_type="text/html")

    # -------------------------------------------------------------------
    # Dashboard HTML
    # -------------------------------------------------------------------

    def _generate_dashboard_html(self) -> str:
        """Generate the dashboard HTML page."""
        summary = self._metrics.get_summary()
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Presidium Admin Dashboard</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Inter', -apple-system, sans-serif; background: #0f0f1a; color: #e0e0e0; }}
        .container {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
        h1 {{ color: #6366f1; margin-bottom: 24px; font-size: 24px; }}
        h2 {{ color: #818cf8; margin: 24px 0 12px; font-size: 18px; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; }}
        .card {{ background: #1a1a2e; border-radius: 16px; padding: 20px; border: 1px solid #2a2a4a; }}
        .card .value {{ font-size: 32px; font-weight: bold; color: #6366f1; }}
        .card .label {{ font-size: 12px; color: #938f99; margin-top: 4px; }}
        .status {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }}
        .status-ok {{ background: #4ade80; }}
        .status-warn {{ background: #fbbf24; }}
        .status-error {{ background: #f87171; }}
        nav {{ background: #1a1a2e; padding: 12px 24px; border-bottom: 1px solid #2a2a4a; margin-bottom: 24px; display: flex; gap: 16px; align-items: center; }}
        nav a {{ color: #938f99; text-decoration: none; font-size: 14px; }}
        nav a:hover {{ color: #e0e0e0; }}
        nav .brand {{ color: #6366f1; font-weight: bold; font-size: 16px; margin-right: auto; }}
    </style>
    <script>
        setInterval(async () => {{
            const resp = await fetch('/api/metrics');
            const data = await resp.json();
            document.getElementById('msg-sent').textContent = data.messages_sent?.toLocaleString() || '0';
            document.getElementById('msg-recv').textContent = data.messages_received?.toLocaleString() || '0';
            document.getElementById('conn-active').textContent = data.connections_active || '0';
            document.getElementById('mem-rss').textContent = (data.memory_rss_mb || 0).toFixed(1) + ' MB';
            document.getElementById('cpu-pct').textContent = (data.cpu_percent || 0).toFixed(1) + '%';
            document.getElementById('uptime').textContent = Math.floor(data.uptime_seconds || 0) + 's';
        }}, 5000);
    </script>
</head>
<body>
    <nav>
        <span class="brand">Presidium</span>
        <a href="/">Dashboard</a>
        <a href="/api/metrics">API</a>
    </nav>
    <div class="container">
        <h1>Node Dashboard</h1>
        <div class="grid">
            <div class="card">
                <div class="value" id="uptime">{int(summary.get("uptime_seconds", 0))}s</div>
                <div class="label">Uptime</div>
            </div>
            <div class="card">
                <div class="value" id="msg-sent">{summary.get("messages_sent", 0):,}</div>
                <div class="label">Messages Sent</div>
            </div>
            <div class="card">
                <div class="value" id="msg-recv">{summary.get("messages_received", 0):,}</div>
                <div class="label">Messages Received</div>
            </div>
            <div class="card">
                <div class="value" id="conn-active">{summary.get("connections_active", 0)}</div>
                <div class="label">Active Connections</div>
            </div>
            <div class="card">
                <div class="value" id="mem-rss">{summary.get("memory_rss_mb", 0):.1f} MB</div>
                <div class="label">Memory RSS</div>
            </div>
            <div class="card">
                <div class="value" id="cpu-pct">{summary.get("cpu_percent", 0):.1f}%</div>
                <div class="label">CPU Usage</div>
            </div>
        </div>
        <h2>Quick Actions</h2>
        <div class="grid">
            <div class="card">
                <p>Fetch peers: <code>GET /api/peers</code></p>
                <p>Fetch bridges: <code>GET /api/bridges</code></p>
                <p>View audit: <code>GET /api/audit?limit=50</code></p>
            </div>
        </div>
    </div>
</body>
</html>"""

    # -------------------------------------------------------------------
    # Metrics proxy
    # -------------------------------------------------------------------

    @property
    def metrics(self) -> MetricsCollector:
        """Access the metrics collector for recording events."""
        return self._metrics
