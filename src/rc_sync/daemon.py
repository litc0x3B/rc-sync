"""Daemon management for systemd user units and journalctl logs."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from rc_sync.config import Config
from rc_sync.logger import get_logger
from rc_sync.paths import get_systemd_user_dir
from rc_sync.state import StateManager

SERVICE_TEMPLATE = """[Unit]
Description=rc-sync synchronization service
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
{env_lines}ExecStart={exec_cmd}
"""

TIMER_TEMPLATE = """[Unit]
Description=rc-sync synchronization timer

[Timer]
OnBootSec=1m
OnUnitActiveSec={freq}m
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

    def generate_units(self) -> tuple[Path, Path]:
        """Generate rc-sync.service and rc-sync.timer in systemd user directory."""
        self.systemd_dir.mkdir(parents=True, exist_ok=True)

        # Determine ExecStart command
        binary = shutil.which("rc-sync")
        if binary:
            exec_cmd = f"{binary} sync all"
        else:
            exec_cmd = f"{sys.executable} -m rc_sync.cli sync all"

        env_lines = ""
        if "RC_SYNC_CONFIG_PATH" in os.environ:
            env_lines += f'Environment="RC_SYNC_CONFIG_PATH={os.environ["RC_SYNC_CONFIG_PATH"]}"\n'
        if "RC_SYNC_STATE_PATH" in os.environ:
            env_lines += f'Environment="RC_SYNC_STATE_PATH={os.environ["RC_SYNC_STATE_PATH"]}"\n'

        service_content = SERVICE_TEMPLATE.format(
            env_lines=env_lines,
            exec_cmd=exec_cmd,
        )
        timer_content = TIMER_TEMPLATE.format(
            freq=self.config.sync_freq_minutes,
        )

        service_path = self.systemd_dir / "rc-sync.service"
        timer_path = self.systemd_dir / "rc-sync.timer"

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

    def enable(self) -> int:
        """Generate units, reload daemon, and enable timer."""
        self.generate_units()
        code = self.daemon_reload()
        if code != 0:
            return code

        cmd = ["systemctl", "--user", "enable", "--now", "rc-sync.timer"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                self._logger.info(
                    "Enabled and started rc-sync.timer.", extra={"context": "daemon"}
                )
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

    def logs(self, follow: bool = False) -> int:
        """View daemon logs via journalctl."""
        cmd = ["journalctl", "--user", "-u", "rc-sync.service"]
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
            for m in self.config.mappings:
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
