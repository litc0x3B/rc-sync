"""Path resolution functions for rc-sync following XDG specifications."""

import os
from pathlib import Path

import platformdirs

APP_NAME = "rc-sync"


def get_config_path() -> Path:
    """Get path to configuration YAML file."""
    env_path = os.environ.get("RC_SYNC_CONFIG_PATH")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return platformdirs.user_config_path(APP_NAME) / "config.yaml"


def get_state_path() -> Path:
    """Get path to state JSON file."""
    env_path = os.environ.get("RC_SYNC_STATE_PATH")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return platformdirs.user_state_path(APP_NAME) / "state.json"


def get_lock_path() -> Path:
    """Get path to global interprocess lock file."""
    env_path = os.environ.get("RC_SYNC_LOCK_PATH")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return platformdirs.user_runtime_path(APP_NAME) / "rc-sync.lock"


def get_systemd_user_dir() -> Path:
    """Get path to systemd user units directory."""
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        base = Path(xdg_config).expanduser()
    else:
        base = Path.home() / ".config"
    return base / "systemd" / "user"


def get_installed_schema_path() -> Path | None:
    """Find installed schema.json in share directories (Nix store, XDG_DATA_DIRS)."""
    import shutil
    import sys

    # 1. Check relative to binary / script location (e.g., in Nix store or virtualenv)
    for candidate in [sys.argv[0], shutil.which("rc-sync")]:
        if candidate:
            try:
                resolved = Path(candidate).resolve()
                # If binary is in .../bin/rc-sync, look for .../share/rc-sync/schema.json
                schema_path = resolved.parent.parent / "share" / APP_NAME / "schema.json"
                if schema_path.is_file():
                    return schema_path
            except Exception:
                pass

    # 2. Check XDG_DATA_DIRS
    data_dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
    for d in data_dirs:
        if d.strip():
            candidate_path = Path(d.strip()) / APP_NAME / "schema.json"
            if candidate_path.is_file():
                return candidate_path

    # 3. Check XDG user data dir (~/.local/share/rc-sync/schema.json)
    try:
        user_data_schema = platformdirs.user_data_path(APP_NAME) / "schema.json"
        if user_data_schema.is_file():
            return user_data_schema
    except Exception:
        pass

    return None

