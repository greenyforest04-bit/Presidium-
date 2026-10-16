"""Release updater with pinned Ed25519 trust and full ZIP packages.

Configure the trusted public key in the constructor or via
PRESIDIUM_UPDATE_PUBLIC_KEY (base64). Unsigned or untrusted packages
are rejected. Installation keeps a backup for rollback; delta packages
are not supported.
"""

from __future__ import annotations

import asyncio
import enum
import hashlib
import json
import logging
import os
import platform
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Update channel
# ---------------------------------------------------------------------------


class UpdateChannel(enum.Enum):
    """Update release channel."""

    STABLE = "stable"
    BETA = "beta"
    NIGHTLY = "nightly"


# ---------------------------------------------------------------------------
# Update manifest
# ---------------------------------------------------------------------------


@dataclass
class UpdateManifest:
    """Metadata about an available update."""

    version: str = ""
    channel: UpdateChannel = UpdateChannel.STABLE
    release_notes: str = ""
    release_date: str = ""

    # Package URLs
    full_package_url: str = ""
    full_package_size: int = 0
    full_package_sha256: str = ""
    full_package_signature: str = ""

    # Delta package (optional)
    delta_from_version: str = ""
    delta_package_url: str = ""
    delta_package_size: int = 0
    delta_package_sha256: str = ""
    delta_package_signature: str = ""

    # Platform-specific
    platform: str = ""
    architecture: str = ""

    # Mandatory update flag
    is_mandatory: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UpdateManifest:
        return cls(
            version=data.get("version", ""),
            channel=UpdateChannel(data.get("channel", "stable")),
            release_notes=data.get("release_notes", ""),
            release_date=data.get("release_date", ""),
            full_package_url=data.get("full_package_url", ""),
            full_package_size=data.get("full_package_size", 0),
            full_package_sha256=data.get("full_package_sha256", ""),
            full_package_signature=data.get("full_package_signature", ""),
            delta_from_version=data.get("delta_from_version", ""),
            delta_package_url=data.get("delta_package_url", ""),
            delta_package_size=data.get("delta_package_size", 0),
            delta_package_sha256=data.get("delta_package_sha256", ""),
            delta_package_signature=data.get("delta_package_signature", ""),
            platform=data.get("platform", ""),
            architecture=data.get("architecture", ""),
            is_mandatory=data.get("is_mandatory", False),
        )


# ---------------------------------------------------------------------------
# Auto-updater
# ---------------------------------------------------------------------------


class AutoUpdater:
    """Handles automatic update checking and installation.

    Usage:
        updater = AutoUpdater(
            current_version="0.4.0",
            channel=UpdateChannel.STABLE,
            update_url="https://updates.presidium.chat",
        )
        updater.set_on_update_available(callback)
        await updater.start_background_check(interval_hours=24)
    """

    UPDATE_CHECK_URL = "https://updates.presidium.chat/v1/check"
    MANIFEST_URL_TEMPLATE = "https://updates.presidium.chat/v1/manifest/{channel}/{platform}"

    def __init__(
        self,
        current_version: str = "0.0.0",
        channel: UpdateChannel = UpdateChannel.STABLE,
        update_url: str = "",
        install_dir: Optional[Path] = None,
        release_public_key: bytes | None = None,
    ) -> None:
        self._release_public_key = release_public_key
        if self._release_public_key is None and os.environ.get("PRESIDIUM_UPDATE_PUBLIC_KEY"):
            import base64

            self._release_public_key = base64.b64decode(
                os.environ["PRESIDIUM_UPDATE_PUBLIC_KEY"], validate=True
            )
        self._current_version = current_version
        self._channel = channel
        self._update_url = update_url or self.UPDATE_CHECK_URL
        self._install_dir = install_dir or self._detect_install_dir()

        self._background_task: Optional[asyncio.Task[None]] = None
        self._running = False

        # Callbacks
        self._on_update_available: Optional[Any] = None
        self._on_download_progress: Optional[Any] = None
        self._on_update_installed: Optional[Any] = None
        self._on_update_error: Optional[Any] = None

        # State
        self._latest_manifest: Optional[UpdateManifest] = None
        self._downloaded_package: Optional[Path] = None

    # -------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------

    @property
    def current_version(self) -> str:
        return self._current_version

    @property
    def latest_version(self) -> Optional[str]:
        if self._latest_manifest:
            return self._latest_manifest.version
        return None

    @property
    def update_available(self) -> bool:
        if self._latest_manifest is None:
            return False
        return self._compare_versions(self._latest_manifest.version, self._current_version) > 0

    # -------------------------------------------------------------------
    # Callback setters
    # -------------------------------------------------------------------

    def set_on_update_available(self, callback: Any) -> None:
        """Set callback: (manifest: UpdateManifest) -> None"""
        self._on_update_available = callback

    def set_on_download_progress(self, callback: Any) -> None:
        """Set callback: (progress_pct: float, bytes_downloaded: int) -> None"""
        self._on_download_progress = callback

    def set_on_update_installed(self, callback: Any) -> None:
        """Set callback: (new_version: str) -> None"""
        self._on_update_installed = callback

    def set_on_update_error(self, callback: Any) -> None:
        """Set callback: (error: Exception) -> None"""
        self._on_update_error = callback

    # -------------------------------------------------------------------
    # Background checking
    # -------------------------------------------------------------------

    async def start_background_check(self, interval_hours: int = 24) -> None:
        """Start periodic background update checks."""
        self._running = True
        self._background_task = asyncio.create_task(self._check_loop(interval_hours * 3600))

    async def stop_background_check(self) -> None:
        """Stop background update checking."""
        self._running = False
        if self._background_task:
            self._background_task.cancel()
            try:
                await self._background_task
            except asyncio.CancelledError:
                pass

    async def _check_loop(self, interval_seconds: int) -> None:
        """Periodically check for updates."""
        while self._running:
            try:
                await self.check_for_updates()
            except Exception as exc:
                logger.error("Update check failed: %s", exc)
                if self._on_update_error:
                    self._on_update_error(exc)

            await asyncio.sleep(interval_seconds)

    # -------------------------------------------------------------------
    # Update checking
    # -------------------------------------------------------------------

    async def check_for_updates(self) -> Optional[UpdateManifest]:
        """Check for available updates.

        Returns:
            UpdateManifest if an update is available, None otherwise.
        """
        try:
            manifest = await self._fetch_manifest()
            if manifest is None:
                return None

            self._latest_manifest = manifest

            if self.update_available:
                logger.info(
                    "Update available: %s → %s",
                    self._current_version,
                    manifest.version,
                )
                if self._on_update_available:
                    self._on_update_available(manifest)
                return manifest

            return None

        except Exception as exc:
            logger.error("Failed to check for updates: %s", exc)
            return None

    async def _fetch_manifest(self) -> Optional[UpdateManifest]:
        """Fetch the latest update manifest from the update server."""
        try:
            import aiohttp

            system = platform.system().lower()
            arch = platform.machine().lower()

            url = self.MANIFEST_URL_TEMPLATE.format(
                channel=self._channel.value,
                platform=f"{system}-{arch}",
            )

            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return UpdateManifest.from_dict(data)
                    elif resp.status == 204:
                        return None  # No update available
                    else:
                        logger.warning("Update server returned HTTP %d", resp.status)
                        return None

        except ImportError:
            logger.debug("aiohttp not available, skipping update check")
            return None
        except Exception as exc:
            logger.debug("Update check network error: %s", exc)
            return None

    # -------------------------------------------------------------------
    # Update installation
    # -------------------------------------------------------------------

    async def download_and_install(self) -> bool:
        """Download and install the latest available update.

        Returns:
            True if the update was installed successfully.
        """
        if self._latest_manifest is None:
            logger.error("No update manifest available.")
            return False

        manifest = self._latest_manifest

        try:
            # Download
            package_path = await self._download_package(manifest)
            if package_path is None:
                return False

            # Verify
            if not await self._verify_package(package_path, manifest):
                logger.error("Package verification failed!")
                return False

            # Install
            if await self._install_package(package_path, manifest):
                logger.info("Update to %s installed successfully", manifest.version)
                if self._on_update_installed:
                    self._on_update_installed(manifest.version)
                self._current_version = manifest.version
                return True
            else:
                logger.error("Installation failed")
                return False

        except Exception as exc:
            logger.error("Update installation failed: %s", exc)
            if self._on_update_error:
                self._on_update_error(exc)
            return False

    async def _download_package(self, manifest: UpdateManifest) -> Optional[Path]:
        """Download the update package."""
        # Only full packages are supported
        url = manifest.full_package_url
        expected_size = manifest.full_package_size
        expected_sha256 = manifest.full_package_sha256

        # Delta application is not implemented; download only complete packages.
        try:
            import aiohttp

            tmp_dir = tempfile.mkdtemp(prefix="presidium_update_")
            package_path = Path(tmp_dir) / "presidium-update.zip"

            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        logger.error("Download failed: HTTP %d", resp.status)
                        return None

                    total = int(resp.headers.get("Content-Length", str(expected_size)))
                    downloaded = 0

                    with open(package_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(65536):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if self._on_download_progress:
                                pct = (downloaded / total * 100) if total > 0 else 0
                                self._on_download_progress(pct, downloaded)

            # Verify SHA-256
            actual_sha256 = self._compute_file_sha256(package_path)
            if actual_sha256 != expected_sha256:
                logger.error(
                    "SHA-256 mismatch: expected %s, got %s",
                    expected_sha256[:16],
                    actual_sha256[:16],
                )
                return None

            self._downloaded_package = package_path
            return package_path

        except Exception as exc:
            logger.error("Download failed: %s", exc)
            return None

    async def _verify_package(self, package_path: Path, manifest: UpdateManifest) -> bool:
        """Authenticate release metadata and package digest using a pinned Ed25519 key.

        The release signer signs signature_payload(manifest); the key comes from
        trusted installation configuration, never from the downloaded manifest.
        """
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        import base64

        try:
            if not self._release_public_key or not manifest.full_package_signature:
                logger.error("Update rejected: release key or signature is missing")
                return False
            if self._compute_file_sha256(package_path) != manifest.full_package_sha256:
                return False
            if package_path.stat().st_size != manifest.full_package_size:
                return False
            if self._compare_versions(manifest.version, self._current_version) <= 0:
                return False
            signature = base64.b64decode(manifest.full_package_signature, validate=True)
            Ed25519PublicKey.from_public_bytes(self._release_public_key).verify(
                signature, self.signature_payload(manifest)
            )
            return True
        except Exception:
            logger.error("Update rejected: invalid release signature or metadata")
            return False

    @staticmethod
    def signature_payload(manifest: UpdateManifest) -> bytes:
        """Canonical release-signing format, version 1."""
        return json.dumps(
            {
                "format": "presidium-release-v1",
                "version": manifest.version,
                "channel": manifest.channel.value,
                "platform": manifest.platform,
                "architecture": manifest.architecture,
                "sha256": manifest.full_package_sha256,
                "size": manifest.full_package_size,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    async def _install_package(self, package_path: Path, manifest: UpdateManifest) -> bool:
        """Install the update package.

        Applies the update atomically by:
        1. Creating a backup of the current installation
        2. Extracting the new version to a staging directory
        3. Swapping the directories
        4. Removing the old backup on success
        """
        if not await self._verify_package(package_path, manifest):
            return False
        install_dir = self._install_dir
        if not install_dir or not install_dir.exists():
            logger.error("Install directory not found: %s", install_dir)
            return False

        # Create backup
        backup_dir = Path(str(install_dir) + ".backup")
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        shutil.copytree(str(install_dir), str(backup_dir))

        try:
            # Extract package (simplified — real implementation would
            # handle .pkg, .dmg, .msi, .AppImage formats)
            staging_dir = Path(tempfile.mkdtemp(prefix="presidium_staging_"))

            # For PyInstaller bundles, we replace the entire directory
            if package_path.suffix == ".zip":
                import zipfile

                with zipfile.ZipFile(str(package_path), "r") as zf:
                    for entry in zf.infolist():
                        target = (staging_dir / entry.filename).resolve()
                        if (
                            not target.is_relative_to(staging_dir.resolve())
                            or (entry.external_attr >> 16) & 0o170000 == 0o120000
                        ):
                            raise ValueError("Unsafe path in update archive")
                    zf.extractall(str(staging_dir))
            else:
                # Assume it's a complete replacement
                shutil.copy2(str(package_path), str(staging_dir / "presidium"))

            # Swap directories
            old_dir = Path(str(install_dir) + ".old")
            if old_dir.exists():
                shutil.rmtree(old_dir)
            os.rename(str(install_dir), str(old_dir))
            os.rename(str(staging_dir), str(install_dir))

            # Cleanup
            shutil.rmtree(str(old_dir), ignore_errors=True)
            shutil.rmtree(str(backup_dir), ignore_errors=True)

            return True

        except Exception as exc:
            logger.error("Installation failed, rolling back: %s", exc)
            # Rollback
            if backup_dir.exists():
                if install_dir.exists():
                    shutil.rmtree(str(install_dir))
                shutil.copytree(str(backup_dir), str(install_dir))
            return False

    # -------------------------------------------------------------------
    # Utility methods
    # -------------------------------------------------------------------

    @staticmethod
    def _compare_versions(v1: str, v2: str) -> int:
        """Compare two semantic version strings.

        Returns:
            Positive if v1 > v2, negative if v1 < v2, zero if equal.
        """

        def parse(v: str) -> list[int]:
            parts = v.lstrip("v").split(".")
            result = []
            for p in parts:
                # Handle pre-release suffixes
                base = p.split("-")[0].split("+")[0]
                try:
                    result.append(int(base))
                except ValueError:
                    result.append(0)
            return result

        p1, p2 = parse(v1), parse(v2)
        for a, b in zip(p1, p2):
            if a != b:
                return a - b
        return len(p1) - len(p2)

    @staticmethod
    def _compute_file_sha256(path: Path) -> str:
        """Compute SHA-256 hash of a file."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _detect_install_dir() -> Path:
        """Detect the installation directory."""
        # Check if running from a PyInstaller bundle
        if getattr(sys, "frozen", False):
            return Path(sys.executable).parent
        return Path(__file__).parent.parent.parent


# Import sys for _detect_install_dir
import sys
