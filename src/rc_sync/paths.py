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
    env_dir = os.environ.get("RC_SYNC_SYSTEMD_DIR")
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        base = Path(xdg_config).expanduser()
    else:
        base = Path.home() / ".config"
    return base / "systemd" / "user"


def _is_safe_installed_schema(path: Path) -> bool:
    """Return True if path is an existing file and not an ephemeral /nix/store path."""
    try:
        if str(path).startswith("/nix/store/"):
            return False
        return path.is_file()
    except Exception:
        return False


def get_installed_schema_path() -> Path | None:
    """Find installed schema.json in stable directories (Nix profiles, FHS, XDG).

    Excludes ephemeral /nix/store/... paths so they are never hardcoded into configs.
    """
    import shutil
    import sys

    def _find_in_share(share_dir: Path) -> Path | None:
        for sub in [Path("doc") / APP_NAME / "schema.json", Path(APP_NAME) / "schema.json"]:
            cand = share_dir / sub
            if _is_safe_installed_schema(cand):
                return cand
        return None

    # 1. Nix profile binary location (WITHOUT resolving symlinks into /nix/store)
    for candidate in [shutil.which("rc-sync"), sys.argv[0]]:
        if candidate:
            try:
                p = Path(candidate)
                found = _find_in_share(p.parent.parent / "share")
                if found:
                    return found
            except Exception:
                pass

    # 2. Stable Nix profile locations
    nix_profile_dirs = [
        Path.home() / ".nix-profile" / "share",
        Path("/run/current-system/sw/share"),
        Path("/nix/var/nix/profiles/default/share"),
    ]
    for p_dir in nix_profile_dirs:
        found = _find_in_share(p_dir)
        if found:
            return found

    # 3. User XDG data directory ($XDG_DATA_HOME, default ~/.local/share)
    xdg_data_home_raw = os.environ.get("XDG_DATA_HOME")
    user_share_dir = (
        Path(xdg_data_home_raw).expanduser()
        if xdg_data_home_raw
        else Path.home() / ".local" / "share"
    )
    found = _find_in_share(user_share_dir)
    if found:
        return found
    try:
        user_data_schema = platformdirs.user_data_path(APP_NAME) / "schema.json"
        if _is_safe_installed_schema(user_data_schema):
            return user_data_schema
    except Exception:
        pass

    # 4. System-wide Linux XDG data dirs ($XDG_DATA_DIRS, default /usr/local/share:/usr/share)
    data_dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
    for d in data_dirs:
        d_str = d.strip()
        if d_str and not d_str.startswith("/nix/store/"):
            found = _find_in_share(Path(d_str))
            if found:
                return found

    return None
