import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from rc_sync.config import Config
from rc_sync.logger import get_logger, set_trigger_mode
from rc_sync.paths import get_config_path, get_systemd_user_dir
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

    def _is_service_content_equivalent(
        self,
        existing: str,
        exec_path: str | None = None,
    ) -> bool:
        """Check if existing rc-sync.service is functionally equivalent to rendered unit."""
        if "Type=oneshot" not in existing or "SyslogIdentifier=rc-sync" not in existing:
            return False
        if 'Environment="RC_SYNC_TRIGGER=systemd"' not in existing:
            return False

        exec_match = re.search(r"^\s*ExecStart=(.*?)\s*$", existing, re.MULTILINE)
        if not exec_match:
            return False

        exec_cmd = exec_match.group(1).strip()
        if not exec_cmd.endswith("sync all"):
            return False

        bin_part = exec_cmd[:-len("sync all")].strip()

        if exec_path:
            expected_bin = exec_path
        elif binary := shutil.which("rc-sync"):
            expected_bin = binary
        else:
            expected_bin = sys.executable

        try:
            if expected_bin == sys.executable:
                if not (bin_part.startswith(sys.executable) and "-m rc_sync.cli" in bin_part):
                    return False
            else:
                if Path(bin_part).resolve() != Path(expected_bin).resolve():
                    return False
        except Exception:
            return False

        cfg_match = re.search(
            r'^\s*Environment="RC_SYNC_CONFIG_PATH=(.*?)"\s*$', existing, re.MULTILINE
        )
        if cfg_match:
            cfg_path_in_unit = cfg_match.group(1).strip().strip('"')
            try:
                if Path(cfg_path_in_unit).resolve() != get_config_path().resolve():
                    return False
            except Exception:
                return False

        return True

    def _is_unit_up_to_date(
        self,
        path: Path,
        desired_content: str,
        unit_type: str,
        exec_path: str | None = None,
    ) -> bool:
        """Check if existing unit file already has the desired configuration."""
        if not path.exists():
            return False

        try:
            existing = path.read_text(encoding="utf-8")
        except Exception:
            return False

        if existing.strip() == desired_content.strip():
            return True

        if unit_type == "service":
            return self._is_service_content_equivalent(existing, exec_path=exec_path)

        return False

    def _write_unit_if_needed(
        self,
        path: Path,
        content: str,
        unit_type: str,
        exec_path: str | None = None,
    ) -> bool:
        """Write unit content if file does not exist or differs from desired content.

        Returns True if file was written/updated, False if already up-to-date.
        Raises OSError/PermissionError if writing is required but fails.
        """
        if self._is_unit_up_to_date(path, content, unit_type, exec_path=exec_path):
            self._logger.info(
                f"{path.name} already exists with desired configuration.",
                extra={"context": "daemon"},
            )
            return False

        path.write_text(content, encoding="utf-8")
        self._logger.info(
            f"Generated {path.name} in {self.systemd_dir}",
            extra={"context": "daemon"},
        )
        return True

    def generate_units(self, exec_path: str | None = None) -> tuple[Path, Path]:
        """Generate rc-sync.service and rc-sync.timer in systemd user directory."""
        self.systemd_dir.mkdir(parents=True, exist_ok=True)

        service_path = self.systemd_dir / "rc-sync.service"
        timer_path = self.systemd_dir / "rc-sync.timer"

        service_content = self.render_service_unit(exec_path=exec_path)
        timer_content = self.render_timer_unit()

        self._write_unit_if_needed(
            service_path, service_content, unit_type="service", exec_path=exec_path
        )
        self._write_unit_if_needed(
            timer_path, timer_content, unit_type="timer", exec_path=exec_path
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

    def enable(self, exec_path: str | None = None, now: bool = False) -> int:
        """Generate units, reload daemon, and enable timer (with optional --now)."""
        try:
            self.generate_units(exec_path=exec_path)
        except (PermissionError, OSError) as e:
            self._logger.error(
                f"Cannot install systemd units: {e}",
                extra={"context": "daemon"},
            )
            return 1

        code = self.daemon_reload()
        if code != 0:
            return code

        cmd = ["systemctl", "--user", "enable"]
        if now:
            cmd.append("--now")
        cmd.append("rc-sync.timer")

        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                action = "Enabled and started" if now else "Enabled"
                self._logger.info(f"{action} rc-sync.timer.", extra={"context": "daemon"})
            else:
                self._logger.error(
                    f"Failed to enable rc-sync.timer: {res.stderr.strip()}",
                    extra={"context": "daemon"},
                )
            return res.returncode
        except Exception as e:
            self._logger.error(f"Cannot execute systemctl: {e}", extra={"context": "daemon"})
            return 1

    def disable(self, now: bool = False) -> int:
        """Disable timer in systemd (with optional --now)."""
        cmd = ["systemctl", "--user", "disable"]
        if now:
            cmd.append("--now")
        cmd.append("rc-sync.timer")

        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                action = "Disabled and stopped" if now else "Disabled"
                self._logger.info(f"{action} rc-sync.timer.", extra={"context": "daemon"})
            else:
                self._logger.error(
                    f"Failed to disable rc-sync.timer: {res.stderr.strip()}",
                    extra={"context": "daemon"},
                )
            return res.returncode
        except Exception as e:
            self._logger.error(f"Cannot execute systemctl: {e}", extra={"context": "daemon"})
            return 1

    def start(self, exec_path: str | None = None) -> int:
        """Generate units if needed, reload daemon, and start rc-sync.timer."""
        try:
            self.generate_units(exec_path=exec_path)
        except (PermissionError, OSError) as e:
            self._logger.error(
                f"Cannot install systemd units: {e}",
                extra={"context": "daemon"},
            )
            return 1

        code = self.daemon_reload()
        if code != 0:
            return code

        cmd = ["systemctl", "--user", "start", "rc-sync.timer"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                self._logger.info("Started rc-sync.timer.", extra={"context": "daemon"})
            else:
                self._logger.error(
                    f"Failed to start rc-sync.timer: {res.stderr.strip()}",
                    extra={"context": "daemon"},
                )
            return res.returncode
        except Exception as e:
            self._logger.error(f"Cannot execute systemctl: {e}", extra={"context": "daemon"})
            return 1

    def stop(self) -> int:
        """Stop rc-sync.timer and rc-sync.service in systemd without disabling or removing units."""
        cmd = ["systemctl", "--user", "stop", "rc-sync.timer", "rc-sync.service"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                self._logger.info(
                    "Stopped rc-sync.timer and rc-sync.service.", extra={"context": "daemon"}
                )
                return 0

            err_msg = res.stderr.strip()
            err_lower = err_msg.lower()
            if "not loaded" in err_lower or "not found" in err_lower:
                self._logger.info(
                    "rc-sync units are not loaded or already stopped.", extra={"context": "daemon"}
                )
                return 0

            self._logger.error(
                f"Failed to stop systemd units: {err_msg}",
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
            self._logger.error(
                f"Cannot install systemd units: {e}",
                extra={"context": "daemon"},
            )
            return 1

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
