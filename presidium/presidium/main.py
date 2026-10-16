"""Entry point for Presidium application.

Initializes:
- Logging with rotation
- Qt Application
- Application configuration
- Main window
- qasync event loop integration (if available)
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from presidium.utils.config import AppConfig


def setup_logging(config: AppConfig) -> None:
    """Configure application logging."""
    from logging.handlers import RotatingFileHandler

    root_logger = logging.getLogger("presidium")
    root_logger.setLevel(getattr(logging, config.log_level, logging.INFO))

    # Console handler
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.DEBUG)
    console_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    console_handler.setFormatter(console_fmt)
    root_logger.addHandler(console_handler)

    # File handler (optional)
    if config.log_file:
        try:
            file_handler = RotatingFileHandler(
                config.log_file,
                maxBytes=5 * 1024 * 1024,  # 5 MB
                backupCount=3,
            )
            file_handler.setLevel(logging.DEBUG)
            file_fmt = logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            file_handler.setFormatter(file_fmt)
            root_logger.addHandler(file_handler)
        except Exception as exc:
            root_logger.warning("Cannot create log file: %s", exc)


def main() -> int:
    """Application entry point."""
    # Load configuration
    config = AppConfig()
    config.ensure_data_dir()

    # Setup logging
    setup_logging(config)
    logger = logging.getLogger(__name__)
    logger.info("Presidium starting...")

    # Import Qt
    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import Qt
    except ImportError:
        print("ERROR: PySide6 is required. Install with: pip install PySide6", file=sys.stderr)
        return 1

    # High DPI support
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough,
    )

    # Create Qt application
    app = QApplication(sys.argv)
    app.setApplicationName("Presidium")
    app.setApplicationVersion("0.1.0")
    app.setOrganizationName("Presidium")

    # Set application-wide font
    from PySide6.QtGui import QFont
    font = QFont(config.ui.font_family)
    font.setPointSize(15)
    app.setFont(font)

    # Async networking and cleanup require the Qt/asyncio integration.
    try:
        from qasync import QEventLoop
    except ImportError:
        logger.error("qasync is required. Install the locked project dependencies.")
        return 1

    from presidium.ui.app import MainWindow

    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)
    window = MainWindow(config)
    window.show()
    with loop:
        app.aboutToQuit.connect(loop.stop)
        loop.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
