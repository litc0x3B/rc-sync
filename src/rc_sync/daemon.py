import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from rc_sync.config import Config
from rc_sync.logger import get_logger, set_trigger_mode
from rc_sync.paths import get_systemd_user_dir
from rc_sync.state import StateManager
from rc_sync.sync_engine import SyncEngine

SERVICE_TEMPLATE = """[Unit]
Description=rc-sync synchronization service
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
SyslogIdentifier=rc-sync
Environment="RC_SYNC_TRIGGER=systemd"
{env_lines}ExecStart={exec_cmd}
"""

TIMER_TEMPLATE = """[Unit]
Description=rc-sync synchronization timer

[Timer]
OnBootSec=1m
OnActiveSec=1m
OnUnitInactiveSec={freq}m
Persistent=true

[Install]
WantedBy=timers.target
"""


class DaemonManager:
    """Manages systemd user units for rc-sync."""

    def __init__(
        self,
        config: Config,
        state_manager: StateManager,
        systemd_dir: Path | None = None,
    ) -> None:
        self.config = config
        self.state_manager = state_manager
        self.systemd_dir = systemd_dir or get_systemd_user_dir()
        self._logger = get_logger()

    def render_service_unit(self, exec_path: str | None = None) -> str:
        """Render rc-sync.service unit content."""
        if exec_path:
            exec_cmd = f"{exec_path} sync all"
        elif binary := shutil.which("rc-sync"):
            exec_cmd = f"{binary} sync all"
        else:
            exec_cmd = f"{sys.executable} -m rc_sync.cli sync all"

        env_lines = ""
        if "RC_SYNC_CONFIG_PATH" in os.environ:
            env_lines += f'Environment="RC_SYNC_CONFIG_PATH={os.environ["RC_SYNC_CONFIG_PATH"]}"\n'
        if "RC_SYNC_STATE_PATH" in os.environ:
            env_lines += f'Environment="RC_SYNC_STATE_PATH={os.environ["RC_SYNC_STATE_PATH"]}"\n'

        return SERVICE_TEMPLATE.format(
            env_lines=env_lines,
            exec_cmd=exec_cmd,
        )

    def render_timer_unit(self) -> str:
        """Render rc-sync.timer unit content."""
        return TIMER_TEMPLATE.format(
            freq=self.config.sync_freq_minutes,
        )

    def generate_units(self, exec_path: str | None = None) -> tuple[Path, Path]:
        """Generate rc-sync.service and rc-sync.timer in systemd user directory."""
        self.systemd_dir.mkdir(parents=True, exist_ok=True)

        service_path = self.systemd_dir / "rc-sync.service"
        timer_path = self.systemd_dir / "rc-sync.timer"

        for p in (service_path, timer_path):
            if p.exists() and not os.access(p, os.W_OK):
                raise PermissionError(f"{p} is read-only")

        service_content = self.render_service_unit(exec_path=exec_path)
        timer_content = self.render_timer_unit()

        service_path.write_text(service_content, encoding="utf-8")
        timer_path.write_text(timer_content, encoding="utf-8")

        self._logger.info(
            f"Generated systemd user units in {self.systemd_dir}",
            extra={"context": "daemon"},
        )
        return service_path, timer_path

    def daemon_reload(self) -> int:
        """Run systemctl --user daemon-reload."""
        cmd = ["systemctl", "--user", "daemon-reload"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                self._logger.info("Reloaded systemd user daemon.", extra={"context": "daemon"})
            else:
                self._logger.error(
                    f"systemctl --user daemon-reload failed: {res.stderr.strip()}",
                    extra={"context": "daemon"},
                )
            return res.returncode
        except Exception as e:
            self._logger.error(f"Cannot execute systemctl: {e}", extra={"context": "daemon"})
            return 1

    def install(self, exec_path: str | None = None) -> int:
        """Generate units and reload systemd user daemon without starting timer."""
        try:
            self.generate_units(exec_path=exec_path)
        except (PermissionError, OSError) as e:
            self._logger.error(
                f"Cannot install systemd units: {e}",
                extra={"context": "daemon"},
            )
            return 1

        code = self.daemon_reload()
        if code == 0:
            self._logger.info("Installed systemd user units.", extra={"context": "daemon"})
        return code

    def remove(self) -> int:
        """Stop and disable timer, remove unit files, and reload systemd user daemon."""
        service_path = self.systemd_dir / "rc-sync.service"
        timer_path = self.systemd_dir / "rc-sync.timer"

        # Check permissions: if parent directory is not writable, removal will fail
        if self.systemd_dir.exists() and not os.access(self.systemd_dir, os.W_OK):
            self._logger.error(
                f"Cannot remove unit files: directory {self.systemd_dir} is read-only.",
                extra={"context": "daemon"},
            )
            return 1

        # Check permissions: files themselves must not be write-protected (read-only)
        for p in (service_path, timer_path):
            if p.exists() and not os.access(p, os.W_OK):
                self._logger.error(
                    f"Cannot remove unit file {p}: file is write-protected (read-only).",
                    extra={"context": "daemon"},
                )
                return 1

        # Stop and disable timer
        cmd = ["systemctl", "--user", "disable", "--now", "rc-sync.timer"]
        try:
            subprocess.run(cmd, capture_output=True, text=True)
        except Exception:
            pass

        # Stop service if running
        try:
            subprocess.run(
                ["systemctl", "--user", "stop", "rc-sync.service"],
                capture_output=True,
                text=True,
            )
        except Exception:
            pass

        try:
            service_path.unlink(missing_ok=True)
            timer_path.unlink(missing_ok=True)
        except (PermissionError, OSError) as e:
            self._logger.error(
                f"Failed to remove unit files: {e}",
                extra={"context": "daemon"},
            )
            return 1

        code = self.daemon_reload()
        try:
            subprocess.run(["systemctl", "--user", "reset-failed"], capture_output=True)
        except Exception:
            pass

        if code == 0:
            self._logger.info("Removed systemd user units.", extra={"context": "daemon"})
        return code

    def enable(self, exec_path: str | None = None) -> int:
        """Generate units, reload daemon, and enable timer."""
        try:
            self.generate_units(exec_path=exec_path)
        except (PermissionError, OSError) as e:
            self._logger.warning(
                f"Cannot overwrite systemd units ({e}): proceeding with existing units.",
                extra={"context": "daemon"},
            )

        code = self.daemon_reload()
        if code != 0:
            return code

        cmd = ["systemctl", "--user", "enable", "--now", "rc-sync.timer"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                self._logger.info("Enabled and started rc-sync.timer.", extra={"context": "daemon"})
            else:
                self._logger.error(
                    f"Failed to enable rc-sync.timer: {res.stderr.strip()}",
                    extra={"context": "daemon"},
                )
            return res.returncode
        except Exception as e:
            self._logger.error(f"Cannot execute systemctl: {e}", extra={"context": "daemon"})
            return 1

    def disable(self) -> int:
        """Disable and stop rc-sync.timer."""
        cmd = ["systemctl", "--user", "disable", "--now", "rc-sync.timer"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                self._logger.info(
                    "Disabled and stopped rc-sync.timer.", extra={"context": "daemon"}
                )
            else:
                self._logger.error(
                    f"Failed to disable rc-sync.timer: {res.stderr.strip()}",
                    extra={"context": "daemon"},
                )
            return res.returncode
        except Exception as e:
            self._logger.error(f"Cannot execute systemctl: {e}", extra={"context": "daemon"})
            return 1

    def sync_timer_frequency(self) -> bool:
        """Check if existing rc-sync.timer matches config.sync_freq_minutes and update if needed.

        Only acts if the timer file actually exists.
        If read-only (e.g. pointing to Nix store), logs a warning and skips modification.
        """
        timer_path = self.systemd_dir / "rc-sync.timer"
        if not timer_path.exists():
            return False

        try:
            content = timer_path.read_text(encoding="utf-8")
        except Exception as e:
            self._logger.warning(
                f"Failed to read existing timer at {timer_path}: {e}",
                extra={"context": "daemon"},
            )
            return False

        match = re.search(
            r"^\s*(OnUnitInactiveSec|OnUnitActiveSec)\s*=\s*(\S+)", content, re.MULTILINE
        )
        if not match:
            return False

        param_name = match.group(1)
        current_val = match.group(2).strip()
        target_val = f"{self.config.sync_freq_minutes}m"

        if current_val == target_val and param_name == "OnUnitInactiveSec":
            return False

        # If read-only, warn and skip
        if not os.access(timer_path, os.W_OK):
            self._logger.warning(
                f"Timer freq in config ({target_val}) differs from installed ({current_val}), "
                f"but {timer_path} is read-only. Skipping update.",
                extra={"context": "daemon"},
            )
            return False

        new_content = re.sub(
            r"^\s*(OnUnitInactiveSec|OnUnitActiveSec)\s*=.*$",
            f"OnUnitInactiveSec={target_val}",
            content,
            flags=re.MULTILINE,
        )
        if "OnActiveSec" not in new_content:
            new_content = re.sub(
                r"(\[Timer\]\s*)",
                r"\1OnBootSec=1m\nOnActiveSec=1m\n",
                new_content,
            )
        try:
            timer_path.write_text(new_content, encoding="utf-8")
        except (PermissionError, OSError) as e:
            self._logger.warning(
                f"Insufficient permissions to update {timer_path}: {e}. Skipping update.",
                extra={"context": "daemon"},
            )
            return False

        self.daemon_reload()

        # Restart timer if it is active
        try:
            check = subprocess.run(
                ["systemctl", "--user", "is-active", "--quiet", "rc-sync.timer"],
                capture_output=True,
            )
            if check.returncode == 0:
                subprocess.run(
                    ["systemctl", "--user", "restart", "rc-sync.timer"],
                    capture_output=True,
                )
        except Exception as e:
            self._logger.warning(
                f"Failed to restart rc-sync.timer: {e}",
                extra={"context": "daemon"},
            )

        self._logger.info(
            f"Updated timer frequency from {current_val} to {target_val}.",
            extra={"context": "daemon"},
        )
        return True

    def start_and_stream_service(self) -> int:
        """Start rc-sync.service and stream output in real time via journalctl."""
        journal_cmd = [
            "journalctl",
            "--user",
            "-u",
            "rc-sync.service",
            "-f",
            "-n",
            "0",
            "--output=cat",
        ]
        journal_proc = None
        try:
            journal_proc = subprocess.Popen(journal_cmd)
        except Exception as e:
            self._logger.warn(f"Could not attach journal stream: {e}", extra={"context": "daemon"})

        try:
            cmd = ["systemctl", "--user", "start", "--wait", "rc-sync.service"]
            res = subprocess.run(cmd)
            return res.returncode
        except Exception as e:
            self._logger.error(f"Failed to start rc-sync.service: {e}", extra={"context": "daemon"})
            return 1
        finally:
            if journal_proc:
                time.sleep(0.3)
                journal_proc.terminate()
                try:
                    journal_proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    journal_proc.kill()

    def up(self, exec_path: str | None = None) -> int:
        """Generate units, reload daemon, run initial sync with [up] trigger, then start timer."""
        set_trigger_mode("up")
        try:
            self.generate_units(exec_path=exec_path)
        except (PermissionError, OSError) as e:
            self._logger.warning(
                f"Cannot overwrite systemd units ({e}): proceeding with existing units.",
                extra={"context": "daemon"},
            )

        code = self.daemon_reload()
        if code != 0:
            return code

        # Enable timer without starting it yet
        cmd_enable = ["systemctl", "--user", "enable", "rc-sync.timer"]
        try:
            subprocess.run(cmd_enable, check=True, capture_output=True)
            self._logger.info("Enabled rc-sync.timer.", extra={"context": "daemon"})
        except Exception as e:
            self._logger.error(f"Failed to enable rc-sync.timer: {e}", extra={"context": "daemon"})
            return 1

        self._logger.info("Running initial synchronization...", extra={"context": "daemon"})
        engine = SyncEngine(config=self.config, state_manager=self.state_manager)
        exit_code = engine.sync(target_aliases=["all"])
        if exit_code != 0:
            self._logger.error(
                f"Initial synchronization exited with code {exit_code}.",
                extra={"context": "daemon"},
            )
            return exit_code

        # Start timer now that initial sync succeeded
        cmd_start = ["systemctl", "--user", "start", "rc-sync.timer"]
        try:
            subprocess.run(cmd_start, check=True, capture_output=True)
            self._logger.info("Started rc-sync.timer.", extra={"context": "daemon"})
            return 0
        except Exception as e:
            self._logger.error(f"Failed to start rc-sync.timer: {e}", extra={"context": "daemon"})
            return 1

    def logs(self, follow: bool = False) -> int:
        """View daemon logs via journalctl."""
        cmd = ["journalctl", "--user", "-t", "rc-sync"]
        if follow:
            cmd.append("-f")
        try:
            # Direct pass-through of journalctl
            return subprocess.run(cmd).returncode
        except Exception as e:
            self._logger.error(f"Cannot execute journalctl: {e}", extra={"context": "daemon"})
            return 1

    def get_systemd_status(self) -> str:
        """Get output of systemctl status for timer and service."""
        cmd = ["systemctl", "--user", "status", "rc-sync.timer", "rc-sync.service"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
            output = res.stdout.strip() or res.stderr.strip()
            return output if output else "No status output."
        except Exception as e:
            return f"Failed to retrieve systemctl status: {e}"

    def format_status(self) -> str:
        """Format full status output including daemon status and mappings status."""
        lines = [
            "=== Daemon Status (systemd) ===",
            self.get_systemd_status(),
            "",
            "=== Mappings Status ===",
        ]

        if not self.config.mappings:
            lines.append("No mappings configured.")
        else:
            for m in self.config.mappings.values():
                state = self.state_manager.get_state(m.path1, m.path2)
                has_val = hasattr(state.status, "value")
                status_str = state.status.value if has_val else str(state.status)
                last_sync = state.last_sync_time if state.last_sync_time else "null"
                enabled_str = "true" if m.enabled else "false"

                lines.append(f"- [{m.alias}] ({m.path1} <-> {m.path2})")
                lines.append(f"    Status:    {status_str}")
                lines.append(f"    Last sync: {last_sync}")
                lines.append(f"    Enabled:   {enabled_str}")

        return "\n".join(lines)
