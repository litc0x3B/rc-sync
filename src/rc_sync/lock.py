"""Global interprocess lock implementation using fcntl.flock."""

import errno
import fcntl
import os
import sys
from pathlib import Path
from typing import Any

from rc_sync.logger import get_logger
from rc_sync.paths import get_lock_path


class ProcessLock:
    """Interprocess lock using fcntl.flock on a lockfile."""

    def __init__(self, lock_path: Path | None = None) -> None:
        self.lock_path = lock_path or get_lock_path()
        self._fd: int | None = None
        self._logger = get_logger()

    def acquire(self) -> None:
        """Acquire exclusive flock. Wait if held by another process."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)

        self._fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o644)

        try:
            # Try non-blocking first to detect contention
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as e:
            if isinstance(e, BlockingIOError) or e.errno in (errno.EACCES, errno.EAGAIN):
                # Lock is currently held by another process
                content = ""
                try:
                    os.lseek(self._fd, 0, os.SEEK_SET)
                    raw = os.read(self._fd, 4096)
                    content = raw.decode("utf-8", errors="replace").strip()
                except Exception:
                    pass

                pid_str = "unknown"
                cmd_str = "unknown command"
                if content:
                    lines = content.splitlines()
                    if lines:
                        pid_str = lines[0].strip()
                    if len(lines) > 1:
                        cmd_str = lines[1].strip()

                msg = f"Lock held by PID {pid_str} ({cmd_str}). Waiting for release..."
                self._logger.info(msg, extra={"context": "lock"})

                # Wait indefinitely until lock is acquired or interrupted
                fcntl.flock(self._fd, fcntl.LOCK_EX)
            else:
                raise

        # Once lock is acquired, write current PID and command
        try:
            os.ftruncate(self._fd, 0)
            os.lseek(self._fd, 0, os.SEEK_SET)
            info = f"{os.getpid()}\n{' '.join(sys.argv)}\n"
            os.write(self._fd, info.encode("utf-8"))
            os.fsync(self._fd)
        except Exception:
            pass

        self._logger.info(f"Lock acquired by PID {os.getpid()}.", extra={"context": "lock"})

    def release(self) -> None:
        """Release the acquired lock and close file descriptor."""
        if self._fd is not None:
            try:
                self._logger.info("Lock released.", extra={"context": "lock"})
                os.ftruncate(self._fd, 0)
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except Exception:
                pass
            finally:
                try:
                    os.close(self._fd)
                except Exception:
                    pass
                self._fd = None

    def __enter__(self) -> "ProcessLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()
