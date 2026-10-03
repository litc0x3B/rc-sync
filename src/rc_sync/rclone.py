"""rclone interaction: execution, streaming, lsf checks, mkdir, and flag manipulation."""

import os
import re
import shlex
import subprocess
from dataclasses import dataclass

from rc_sync.logger import get_logger, stream_rclone_line


@dataclass
class LsfResult:
    exists: bool
    is_empty: bool
    error: str | None = None


def expand_path(path: str) -> str:
    """Expand user tilde ~ for local paths while preserving remote prefixes."""
    clean = path.strip()
    # If path starts with ~ and has no colon before slash, expand user
    colon_idx = clean.find(":")
    slash_idx = clean.find("/")

    if colon_idx != -1 and (slash_idx == -1 or colon_idx < slash_idx):
        # Likely a remote path like remote:path or drive:/folder
        return clean
    return os.path.expanduser(clean)


def interpolate_flags(flag_str: str, sync_freq_minutes: int) -> str:
    """Replace %t with sync_freq_minutes, %% with %."""
    if not flag_str:
        return ""

    def _replace(match: re.Match[str]) -> str:
        token = match.group(0)
        if token == "%%":
            return "%"
        if token == "%t":
            return str(sync_freq_minutes)
        return token

    return re.sub(r"%(%|t)?", _replace, flag_str)


def combine_flags(
    cli_override: bool,
    cli_extra_flags: str,
    is_init: bool,
    global_flags: str,
    mapping_extra_flags: str,
    mapping_override_flags: bool,
    mapping_extra_flags_init: str,
    mapping_override_flags_init: bool,
    sync_freq_minutes: int,
    global_flags_init: str = "",
) -> list[str]:
    """Combine global, mapping, and CLI flags according to spec rules.

    Semantics:
    - If cli_override is True: ONLY CLI extra flags are used.
    - Otherwise:
      1. init_flags = (override_flags_init ? "" : global_flags_init) + extra_flags_init
      2. flags = (override_flags ? "" : global_flags) + extra_flags
      3. final_flags = (init_flags + flags + "--resync") if is_init else flags
      CLI extra flags are appended to final_flags.
    """
    if cli_override:
        parts = [cli_extra_flags]
    else:
        init_parts: list[str] = []
        if not mapping_override_flags_init and global_flags_init:
            init_parts.append(global_flags_init)
        if mapping_extra_flags_init:
            init_parts.append(mapping_extra_flags_init)

        reg_parts: list[str] = []
        if not mapping_override_flags and global_flags:
            reg_parts.append(global_flags)
        if mapping_extra_flags:
            reg_parts.append(mapping_extra_flags)

        if is_init:
            parts = init_parts + reg_parts + ["--resync"]
        else:
            parts = reg_parts

        if cli_extra_flags:
            parts.append(cli_extra_flags)

    interpolated = [
        interpolate_flags(p, sync_freq_minutes).strip() for p in parts if p and p.strip()
    ]
    combined_str = " ".join(interpolated)
    raw_tokens = shlex.split(combined_str)
    tokens: list[str] = []
    has_resync = False
    for tok in raw_tokens:
        if tok == "--resync":
            if not has_resync:
                tokens.append(tok)
                has_resync = True
        else:
            tokens.append(tok)
    return tokens


class RcloneRunner:
    """Handles running rclone commands: lsf, mkdir, and bisync."""

    def __init__(self, rclone_path: str = "rclone") -> None:
        self.rclone_path = rclone_path
        self._logger = get_logger()

    def check_path_lsf(self, path: str) -> LsfResult:
        """Check path existence and whether it is empty using rclone lsf."""
        cmd = [self.rclone_path, "lsf", path]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
        except Exception as e:
            return LsfResult(exists=False, is_empty=False, error=str(e))

        if res.returncode == 0:
            lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            return LsfResult(exists=True, is_empty=(len(lines) == 0), error=None)
        elif res.returncode == 3:
            # Code 3: Directory not found -> considered empty and will be created
            return LsfResult(exists=False, is_empty=True, error=None)
        else:
            err = res.stderr.strip() or f"rclone lsf exited with code {res.returncode}"
            return LsfResult(exists=False, is_empty=False, error=err)

    def mkdir(self, path: str) -> tuple[int, str]:
        """Create directory recursively using rclone mkdir."""
        cmd = [self.rclone_path, "mkdir", path]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
            output = res.stderr.strip() or res.stdout.strip()
            return res.returncode, output
        except Exception as e:
            return -1, str(e)

    def run_bisync(
        self,
        path1: str,
        path2: str,
        flags: list[str],
        alias: str,
        force_resync: bool = False,
        context: str | None = None,
    ) -> int:
        """Run rclone bisync with real-time streaming output."""
        cmd = [self.rclone_path, "bisync", path1, path2]

        final_flags = list(flags)
        if force_resync and "--resync" not in final_flags:
            final_flags.insert(0, "--resync")

        cmd.extend(final_flags)

        log_context = context or (f"init:{alias}" if force_resync else f"sync:{alias}")

        # Log full command
        cmd_str = " ".join(shlex.quote(c) if (" " in c or not c) else c for c in cmd)
        self._logger.info(f"Running: {cmd_str}", extra={"context": log_context})

        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            if proc.stdout:
                for line in proc.stdout:
                    stream_rclone_line(line)

            proc.wait()
            code = proc.returncode

            if code == 0:
                self._logger.success(
                    "Mapping completed successfully.",
                    extra={"context": log_context},
                )
            else:
                self._logger.error(
                    f"rclone exited with code {code}.",
                    extra={"context": log_context},
                )
            return code

        except KeyboardInterrupt:
            if proc:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
            raise
        except Exception as e:
            self._logger.error(
                f"Failed to execute rclone: {e}",
                extra={"context": log_context},
            )
            return 1
