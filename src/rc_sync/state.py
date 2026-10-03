"""State management for sync mappings."""

import json
import os
import tempfile
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from rc_sync.paths import get_state_path


class MappingStatus(str, Enum):
    INIT_PENDING = "INIT_PENDING"
    INIT_FAILED = "INIT_FAILED"
    INIT_SUCCESS = "INIT_SUCCESS"
    SYNC_SUCCESS = "SYNC_SUCCESS"
    SYNC_FAILED = "SYNC_FAILED"


class MappingState(BaseModel):
    """Persistent state of a single mapping."""

    model_config = ConfigDict(
        use_enum_values=True,
    )

    path1: str
    path2: str
    init_success: bool = False
    last_sync_time: str | None = None
    status: MappingStatus = MappingStatus.INIT_PENDING


def _normalize_key(path1: str, path2: str) -> tuple[str, str]:
    """Normalize paths for stable mapping identity."""

    def clean(p: str) -> str:
        s = p.strip()
        if len(s) > 1 and s.endswith("/"):
            return s.rstrip("/")
        return s

    return (clean(path1), clean(path2))


class StateManager:
    """Encapsulates state storage and updates for all mappings."""

    def __init__(self, state_path: Path | None = None) -> None:
        self.state_path = state_path or get_state_path()
        self._states: dict[tuple[str, str], MappingState] = {}
        self.load()

    def load(self) -> None:
        """Load state from disk if exists."""
        if not self.state_path.is_file():
            return
        self._states.clear()

        try:
            with open(self.state_path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            # If state file is corrupted or unreadable, start fresh
            return

        if not isinstance(data, dict):
            return

        raw_mappings = data.get("mappings")
        if not isinstance(raw_mappings, list):
            return

        for item in raw_mappings:
            if isinstance(item, dict):
                try:
                    state = MappingState.model_validate(item)
                    key = _normalize_key(state.path1, state.path2)
                    self._states[key] = state
                except Exception:
                    continue

    def get_state(self, path1: str, path2: str) -> MappingState:
        """Get state for (path1, path2), creating an initial entry if not found."""
        key = _normalize_key(path1, path2)
        if key not in self._states:
            self._states[key] = MappingState(
                path1=path1,
                path2=path2,
                init_success=False,
                last_sync_time=None,
                status=MappingStatus.INIT_PENDING,
            )
        return self._states[key]

    def update_status(
        self,
        path1: str,
        path2: str,
        status: MappingStatus | str,
        init_success: bool | None = None,
        sync_time: str | None = None,
    ) -> MappingState:
        """Update state for (path1, path2)."""
        state = self.get_state(path1, path2)
        if isinstance(status, str):
            status = MappingStatus(status)
        state.status = status

        if init_success is not None:
            state.init_success = init_success

        if sync_time is not None:
            state.last_sync_time = sync_time
        elif status != MappingStatus.INIT_PENDING:
            # Set to current ISO 8601 timestamp
            state.last_sync_time = datetime.now().isoformat(timespec="seconds")

        return state

    def all_states(self) -> list[MappingState]:
        """Return list of all recorded mapping states."""
        return list(self._states.values())

    def save(self) -> None:
        """Atomically persist state to disk."""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

        data = {"mappings": [state.model_dump() for state in self._states.values()]}

        # Atomic write: write to temp file in same directory, then rename
        temp_file = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=self.state_path.parent,
                prefix="state_",
                suffix=".tmp",
                delete=False,
                encoding="utf-8",
            ) as f:
                temp_file = Path(f.name)
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())

            temp_file.replace(self.state_path)
        except Exception:
            if temp_file and temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass
            raise
