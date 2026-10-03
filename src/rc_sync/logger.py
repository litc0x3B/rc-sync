import logging
import os
import sys
from pathlib import Path
from typing import Any, cast

SUCCESS_LEVEL = 25
logging.addLevelName(SUCCESS_LEVEL, "SUCCESS")


def get_trigger_mode() -> str:
    """Return 'up', 'systemd', or 'manual' based on environment."""
    val = os.environ.get("RC_SYNC_TRIGGER")
    if val in ("up", "systemd", "manual"):
        return val
    return "manual"


def set_trigger_mode(mode: str) -> None:
    """Set global trigger mode ('manual', 'up', 'systemd')."""
    os.environ["RC_SYNC_TRIGGER"] = mode


class RcSyncLogger(logging.Logger):
    """Logger supporting the SUCCESS log level."""

    def success(self, message: str, *args: Any, **kwargs: Any) -> None:
        if self.isEnabledFor(SUCCESS_LEVEL):
            self._log(SUCCESS_LEVEL, message, args, **kwargs)


logging.setLoggerClass(RcSyncLogger)


class RcSyncFormatter(logging.Formatter):
    """Custom formatter: [<LEVEL>] [<TRIGGER>] [<CONTEXT>] <MESSAGE>"""

    def __init__(self, trigger: str | None = None) -> None:
        super().__init__()
        self._trigger = trigger

    def format(self, record: logging.LogRecord) -> str:
        # Determine level name
        level = record.levelname
        if level == "WARNING":
            level = "WARN"

        # Determine trigger mode: manual, up, or systemd
        trigger = getattr(record, "trigger", None) or self._trigger or get_trigger_mode()

        # Determine context
        context = getattr(record, "context", None)
        if not context:
            context = "rc-sync"

        # Ensure context is enclosed in single brackets
        context_str = str(context).strip()
        if not (context_str.startswith("[") and context_str.endswith("]")):
            context_str = f"[{context_str}]"

        msg = record.getMessage()
        return f"[{level}] [{trigger}] {context_str} {msg}"


class DynamicStreamHandler(logging.Handler):
    """Handler that dynamically references sys.stdout or sys.stderr."""

    def __init__(self, stream_type: str = "stdout") -> None:
        super().__init__()
        self.stream_type = stream_type

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            stream = sys.stdout if self.stream_type == "stdout" else sys.stderr
            stream.write(msg + "\n")
            stream.flush()
        except Exception:
            self.handleError(record)


class StdoutFilter(logging.Filter):
    """Allow INFO and SUCCESS levels only (sys.stdout)."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < logging.WARNING


class StderrFilter(logging.Filter):
    """Allow WARN, ERROR, CRITICAL levels only (sys.stderr)."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.WARNING


_configured = False


def setup_logger(level: int = logging.INFO) -> RcSyncLogger:
    """Configure root logger with custom formatter and split stdout/stderr handlers."""
    global _configured
    logger = cast(RcSyncLogger, logging.getLogger("rc_sync"))
    logger.setLevel(level)

    if not _configured:
        formatter = RcSyncFormatter()

        # stdout handler (INFO, SUCCESS)
        stdout_handler = DynamicStreamHandler(stream_type="stdout")
        stdout_handler.setLevel(logging.DEBUG)
        stdout_handler.addFilter(StdoutFilter())
        stdout_handler.setFormatter(formatter)

        # stderr handler (WARN, ERROR)
        stderr_handler = DynamicStreamHandler(stream_type="stderr")
        stderr_handler.setLevel(logging.WARNING)
        stderr_handler.addFilter(StderrFilter())
        stderr_handler.setFormatter(formatter)

        logger.handlers.clear()
        logger.addHandler(stdout_handler)
        logger.addHandler(stderr_handler)

        if get_trigger_mode() != "systemd" and Path("/dev/log").exists():
            try:
                from logging.handlers import SysLogHandler

                syslog_handler = SysLogHandler(address="/dev/log")
                syslog_handler.ident = "rc-sync: "
                syslog_handler.setLevel(logging.DEBUG)
                syslog_handler.setFormatter(formatter)
                logger.addHandler(syslog_handler)
            except Exception:
                pass

        logger.propagate = False

        _configured = True

    return logger


def get_logger() -> RcSyncLogger:
    """Get the application logger."""
    return setup_logger()


def stream_rclone_line(line: str) -> None:
    """Stream a single line from rclone output with the '  │ ' prefix."""
    clean_line = line.rstrip("\r\n")
    prefix_line = f"  │ {clean_line}"
    sys.stdout.write(f"{prefix_line}\n")
    sys.stdout.flush()

    if get_trigger_mode() != "systemd" and Path("/dev/log").exists():
        try:
            import syslog

            syslog.openlog(ident="rc-sync", facility=syslog.LOG_USER)
            syslog.syslog(syslog.LOG_INFO, prefix_line)
        except Exception:
            pass
