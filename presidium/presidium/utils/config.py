"""Configuration management for Presidium using pydantic-settings."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_data_dir() -> Path:
    """Return OS-appropriate data directory for Presidium."""
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "presidium"
    home = Path.home()
    if os.name == "nt":
        return home / "AppData" / "Local" / "Presidium"
    if os.name == "posix" and Path("/.dockerenv").exists():
        return Path("/data/presidium")
    return home / ".local" / "share" / "presidium"


class NetworkConfig(BaseSettings):
    """Network-related configuration."""

    model_config = SettingsConfigDict(env_prefix="PRESIDIUM_NET_")

    listen_port: int = Field(default=9478, ge=1024, le=65535)
    bootstrap_nodes: list[str] = Field(
        default=[
            "bootstrap1.presidium.chat:9478",
            "bootstrap2.presidium.chat:9478",
            "bootstrap3.presidium.chat:9478",
        ],
        description="List of bootstrap node addresses (host:port).",
    )
    mdns_enabled: bool = Field(default=True, description="Enable mDNS LAN discovery.")
    dht_enabled: bool = Field(default=True, description="Enable Kademlia DHT.")
    nat_traversal_timeout: float = Field(
        default=10.0, description="Seconds to wait for NAT hole punching."
    )
    relay_fallback: bool = Field(
        default=True, description="Fall back to relay if direct connection fails."
    )
    stun_servers: list[str] = Field(
        default=["stun.l.google.com:19302", "stun1.l.google.com:19302"],
        description="STUN servers for NAT type detection.",
    )

    @field_validator("bootstrap_nodes", mode="before")
    @classmethod
    def parse_bootstrap_nodes(cls, v: object) -> list[str]:
        if isinstance(v, str):
            return [n.strip() for n in v.split(",") if n.strip()]
        return v  # type: ignore[return-value]


class CryptoConfig(BaseSettings):
    """Cryptography-related configuration."""

    model_config = SettingsConfigDict(env_prefix="PRESIDIUM_CRYPTO_")

    argon2_time_cost: int = Field(default=3, ge=1)
    argon2_memory_cost: int = Field(default=65536, ge=8192, description="In KiB.")
    argon2_parallelism: int = Field(default=4, ge=1)
    key_file_name: str = Field(default="identity.key")
    db_file_name: str = Field(default="presidium.db")


class UIConfig(BaseSettings):
    """User interface configuration."""

    model_config = SettingsConfigDict(env_prefix="PRESIDIUM_UI_")

    theme: str = Field(default="dark", description="dark or light")
    accent_color: str = Field(default="#6366F1")
    min_width: int = Field(default=360, ge=300)
    default_width: int = Field(default=1200, ge=600)
    default_height: int = Field(default=800, ge=400)
    font_family: str = Field(default="Inter")
    corner_radius: int = Field(default=16, ge=0)
    animations_enabled: bool = Field(default=True)
    auto_lock_timeout: int = Field(
        default=300, ge=0, description="Seconds of inactivity before lock. 0 = disabled."
    )


class AppConfig(BaseSettings):
    """Root application configuration aggregating all sub-configs."""

    model_config = SettingsConfigDict(
        env_prefix="PRESIDIUM_",
        env_nested_delimiter="__",
    )

    data_dir: Path = Field(default_factory=_default_data_dir)
    log_level: str = Field(default="INFO")
    log_file: Optional[str] = Field(default=None, description="Path to log file or None for stderr.")

    network: NetworkConfig = Field(default_factory=NetworkConfig)
    crypto: CryptoConfig = Field(default_factory=CryptoConfig)
    ui: UIConfig = Field(default_factory=UIConfig)

    @field_validator("log_level", mode="before")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}, got {v!r}")
        return upper

    def ensure_data_dir(self) -> None:
        """Create data directory if it doesn't exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)

    @property
    def key_file_path(self) -> Path:
        return self.data_dir / self.crypto.key_file_name

    @property
    def db_file_path(self) -> Path:
        return self.data_dir / self.crypto.db_file_name
