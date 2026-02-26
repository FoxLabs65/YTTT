"""
Centralized logging configuration for end-to-end traceability and support.

- All components use consistent format: timestamp, level, logger, message
- errors.log captures all ERROR and CRITICAL for quick troubleshooting
- Pipeline and UI logs provide full flow traceability
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Format for all log handlers
LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Prevent duplicate handler attachment
_configured = False


def configure_logging(
    *,
    log_to_console: bool = True,
    log_to_file: bool = True,
    log_to_errors: bool = True,
    log_to_ui: bool = False,
    level: int = logging.INFO,
) -> None:
    """Configure root logger for end-to-end traceability.

    - Console: INFO and above (for subprocess output)
    - pipeline_YYYYMMDD.log: Full pipeline/agent logs
    - errors.log: ERROR and CRITICAL only (for troubleshooting)
    - ui.log: UI/frontend events (when log_to_ui=True)
    """
    global _configured
    if _configured:
        return

    root = logging.getLogger()
    root.setLevel(level)

    # Remove any existing handlers to avoid duplicates
    for h in root.handlers[:]:
        root.removeHandler(h)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    if log_to_console:
        console = logging.StreamHandler(sys.stdout)
        console.setLevel(level)
        console.setFormatter(formatter)
        root.addHandler(console)

    if log_to_file:
        pipeline_log = LOG_DIR / f"pipeline_{datetime.now().strftime('%Y%m%d')}.log"
        file_handler = logging.FileHandler(pipeline_log, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    if log_to_errors:
        error_log = LOG_DIR / "errors.log"
        error_handler = logging.FileHandler(error_log, encoding="utf-8")
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        root.addHandler(error_handler)

    if log_to_ui:
        ui_log = LOG_DIR / "ui.log"
        ui_handler = logging.FileHandler(ui_log, encoding="utf-8")
        ui_handler.setLevel(level)
        ui_handler.setFormatter(formatter)
        ui_logger = logging.getLogger("ui")
        ui_logger.addHandler(ui_handler)
        ui_logger.setLevel(level)

    _configured = True
    logging.getLogger("config.logging_config").debug("Logging configured")
    install_exception_hook()


def get_logger(name: str) -> logging.Logger:
    """Get a logger that inherits from root (uses centralized config)."""
    return logging.getLogger(name)


def log_error(logger: logging.Logger, message: str, exc_info: bool = True, **kwargs) -> None:
    """Log an error with optional exception info. Always written to errors.log."""
    logger.error(message, exc_info=exc_info, extra=kwargs)


def install_exception_hook() -> None:
    """Install global exception hook to log uncaught exceptions to errors.log."""
    import sys

    _original_hook = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        logging.getLogger("config.logging_config").exception(
            "Uncaught exception: %s", exc_value, exc_info=(exc_type, exc_value, exc_tb)
        )
        _original_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook
