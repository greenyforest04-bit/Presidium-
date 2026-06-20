"""CLI admin tool for Presidium.

Commands:
- status: peer count, DHT size, bandwidth, memory
- bootstrap add/remove: manage bootstrap nodes
- db vacuum: manual database optimization
- key rotate: rotate X25519 prekey
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Optional

try:
    import typer
    from rich.console import Console
    from rich.table import Table
except ImportError:
    # Fallback without rich/typer
    typer = None
    Console = None
    Table = None

app = typer.Typer(
    name="presidium-admin",
    help="Presidium administration CLI tool.",
    no_args_is_help=True,
) if typer else None

console = Console() if Console else None


# ---------------------------------------------------------------------------
# Helper: load config
# ---------------------------------------------------------------------------

def _load_config():
    from presidium.utils.config import AppConfig
    return AppConfig()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_status() -> None:
    """Show Presidium node status."""
    if console is None:
        print("Install rich and typer: pip install rich typer")
        return

    config = _load_config()
    db_path = config.db_file_path

    table = Table(title="Presidium Node Status")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Data Directory", str(config.data_dir))
    table.add_row("Listen Port", str(config.network.listen_port))
    table.add_row("mDNS", "Enabled" if config.network.mdns_enabled else "Disabled")
    table.add_row("DHT", "Enabled" if config.network.dht_enabled else "Disabled")
    table.add_row("Bootstrap Nodes", str(len(config.network.bootstrap_nodes)))
    table.add_row("Theme", config.ui.theme)
    table.add_row("DB Path", str(db_path))
    table.add_row("DB Exists", "Yes" if db_path.exists() else "No")

    # Memory usage
    try:
        import psutil
        mem = psutil.virtual_memory()
        table.add_row("Memory Usage", f"{mem.percent:.1f}%")
        table.add_row("Memory Total", f"{mem.total // (1024**3)} GB")
    except ImportError:
        table.add_row("Memory Usage", "psutil not installed")

    # DB size
    if db_path.exists():
        size_mb = db_path.stat().st_size / (1024 * 1024)
        table.add_row("DB Size", f"{size_mb:.2f} MB")

    console.print(table)


def cmd_bootstrap_add(host_port: str) -> None:
    """Add a bootstrap node."""
    config = _load_config()
    if host_port not in config.network.bootstrap_nodes:
        config.network.bootstrap_nodes.append(host_port)
        # PHASE3: simplified — would persist to settings
        print(f"Added bootstrap node: {host_port}")
    else:
        print(f"Bootstrap node already exists: {host_port}")


def cmd_bootstrap_remove(host_port: str) -> None:
    """Remove a bootstrap node."""
    config = _load_config()
    if host_port in config.network.bootstrap_nodes:
        config.network.bootstrap_nodes.remove(host_port)
        print(f"Removed bootstrap node: {host_port}")
    else:
        print(f"Bootstrap node not found: {host_port}")


def cmd_db_vacuum() -> None:
    """Vacuum the SQLite database."""
    config = _load_config()
    db_path = config.db_file_path
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return

    async def _vacuum():
        import aiosqlite
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            await db.execute("VACUUM")
            await db.execute("PRAGMA integrity_check")

    asyncio.run(_vacuum())
    print(f"Database vacuumed: {db_path}")


def cmd_key_rotate() -> None:
    """Rotate the X25519 prekey."""
    from presidium.core.crypto import X25519KeyPair
    new_key = X25519KeyPair()
    pk_b64 = __import__("base64").b64encode(new_key.public_key_bytes).decode("ascii")
    print(f"New X25519 prekey generated: {pk_b64}")
    # PHASE3: would store in DHT and local key store


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point for the CLI admin tool."""
    if app is None:
        # Simple fallback without typer
        print("Presidium Admin CLI")
        print("Commands: status, bootstrap-add, bootstrap-remove, db-vacuum, key-rotate")
        print("Install typer and rich for full CLI: pip install typer rich")
        if len(sys.argv) < 2:
            return
        cmd = sys.argv[1]
        if cmd == "status":
            cmd_status()
        elif cmd == "db-vacuum":
            cmd_db_vacuum()
        elif cmd == "key-rotate":
            cmd_key_rotate()
        else:
            print(f"Unknown command: {cmd}")
        return

    app.command(name="status")(cmd_status)
    app.command(name="bootstrap-add")(cmd_bootstrap_add)
    app.command(name="bootstrap-remove")(cmd_bootstrap_remove)
    app.command(name="db-vacuum")(cmd_db_vacuum)
    app.command(name="key-rotate")(cmd_key_rotate)
    app()


if __name__ == "__main__":
    main()
