"""Release and packaging tools for Presidium.

Provides:
    - PyInstaller spec and packaging logic
    - Auto-updater with delta updates
    - Code signing utilities (placeholder)
"""

from presidium.release.updater import AutoUpdater, UpdateChannel
from presidium.release.installer import InstallerBuilder

__all__ = ["AutoUpdater", "UpdateChannel", "InstallerBuilder"]
