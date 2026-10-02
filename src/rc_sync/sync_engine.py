"""Core synchronization engine for executing sequential mapping syncs."""

from collections.abc import Sequence
from pathlib import Path

from rc_sync.config import Config, MappingConfig
from rc_sync.lock import ProcessLock
from rc_sync.logger import get_logger
from rc_sync.rclone import RcloneRunner, combine_flags, expand_path
from rc_sync.state import MappingStatus, StateManager


class SyncEngine:
    """Executes synchronization according to configuration and state."""

    def __init__(
        self,
        config: Config,
        state_manager: StateManager,
        lock_path: Path | None = None,
    ) -> None:
        self.config = config
        self.state_manager = state_manager
        self.lock = ProcessLock(lock_path)
        self.runner = RcloneRunner(config.rclone_path)
        self._logger = get_logger()

    def sync(
        self,
        target_aliases: Sequence[str],
        force_resync: bool = False,
        cli_override_flags: bool = False,
        cli_extra_flags: str = "",
    ) -> int:
        """Run synchronization on target aliases (or all active mappings)."""
        # Resolve target mappings
        mappings_to_sync: list[MappingConfig] = []
        is_all = len(target_aliases) == 1 and target_aliases[0].lower() == "all"

        if is_all:
            mappings_to_sync = [m for m in self.config.mappings if m.enabled]
            if not mappings_to_sync:
                self._logger.info(
                    "No active mappings found to sync.", extra={"context": "sync"}
                )
                return 0
        else:
            alias_map = {m.alias: m for m in self.config.mappings}
            unknown = [a for a in target_aliases if a not in alias_map]
            if unknown:
                for a in unknown:
                    self._logger.error(
                        f"Mapping alias '{a}' not found in configuration.",
                        extra={"context": "sync"},
                    )
                return 1

            mappings_to_sync = [alias_map[a] for a in target_aliases]

        # Acquire global lock
        with self.lock:
            self._logger.info(
                f"Starting sync for {len(mappings_to_sync)} mapping(s)...",
                extra={"context": "sync"},
            )

            succeeded_count = 0
            failed_count = 0

            for m in mappings_to_sync:
                success = self._sync_single_mapping(
                    mapping=m,
                    cli_resync=force_resync,
                    cli_override_flags=cli_override_flags,
                    cli_extra_flags=cli_extra_flags,
                )
                if success:
                    succeeded_count += 1
                else:
                    failed_count += 1

            summary_msg = (
                f"Synchronization batch finished ({succeeded_count} succeeded, "
                f"{failed_count} failed)."
            )
            self._logger.success(
                summary_msg,
                extra={"context": "sync"},
            )

            return 0 if failed_count == 0 else 1

    def _sync_single_mapping(
        self,
        mapping: MappingConfig,
        cli_resync: bool,
        cli_override_flags: bool,
        cli_extra_flags: str,
    ) -> bool:
        """Execute sync for one mapping and update its state."""
        state = self.state_manager.get_state(mapping.path1, mapping.path2)
        is_init = not state.init_success
        force_resync = cli_resync or is_init

        exp_path1 = expand_path(mapping.path1)
        exp_path2 = expand_path(mapping.path2)

        if is_init:
            # Check initial resync preconditions
            res1 = self.runner.check_path_lsf(exp_path1)
            if res1.error:
                err_msg = (
                    f"Initial resync check failed for '{mapping.alias}': "
                    f"path1 '{mapping.path1}' error: {res1.error}"
                )
                self._logger.error(
                    err_msg,
                    extra={"context": "init"},
                )
                self.state_manager.update_status(
                    mapping.path1,
                    mapping.path2,
                    MappingStatus.INIT_FAILED,
                    init_success=False,
                )
                self.state_manager.save()
                return False

            res2 = self.runner.check_path_lsf(exp_path2)
            if res2.error:
                err_msg = (
                    f"Initial resync check failed for '{mapping.alias}': "
                    f"path2 '{mapping.path2}' error: {res2.error}"
                )
                self._logger.error(
                    err_msg,
                    extra={"context": "init"},
                )
                self.state_manager.update_status(
                    mapping.path1,
                    mapping.path2,
                    MappingStatus.INIT_FAILED,
                    init_success=False,
                )
                self.state_manager.save()
                return False

            # Mandatory condition: at least one path must be empty (unless allow_resync_non_empty)
            if (not res1.is_empty) and (not res2.is_empty) and (not mapping.allow_resync_non_empty):
                precond_msg = (
                    f"Initial resync precondition failed for alias '{mapping.alias}': "
                    "both paths are non-empty and AllowResyncNonEmpty is false."
                )
                self._logger.error(
                    precond_msg,
                    extra={"context": "init"},
                )
                self.state_manager.update_status(
                    mapping.path1,
                    mapping.path2,
                    MappingStatus.INIT_FAILED,
                    init_success=False,
                )
                self.state_manager.save()
                return False

            # Create paths if they do not exist
            if not res1.exists:
                code, err = self.runner.mkdir(exp_path1)
                if code != 0:
                    self._logger.error(
                        f"Failed to create path1 '{mapping.path1}': {err}",
                        extra={"context": "init"},
                    )
                    self.state_manager.update_status(
                        mapping.path1,
                        mapping.path2,
                        MappingStatus.INIT_FAILED,
                        init_success=False,
                    )
                    self.state_manager.save()
                    return False

            if not res2.exists:
                code, err = self.runner.mkdir(exp_path2)
                if code != 0:
                    self._logger.error(
                        f"Failed to create path2 '{mapping.path2}': {err}",
                        extra={"context": "init"},
                    )
                    self.state_manager.update_status(
                        mapping.path1,
                        mapping.path2,
                        MappingStatus.INIT_FAILED,
                        init_success=False,
                    )
                    self.state_manager.save()
                    return False

        # Combine flags
        flags = combine_flags(
            cli_override=cli_override_flags,
            cli_extra_flags=cli_extra_flags,
            is_init=is_init,
            global_extra_flags=self.config.extra_flags,
            mapping_extra_flags=mapping.extra_flags,
            mapping_override_flags=mapping.override_flags,
            mapping_extra_flags_init=mapping.extra_flags_init,
            mapping_override_flags_init=mapping.override_flags_init,
            sync_freq_minutes=self.config.sync_freq_minutes,
        )

        exit_code = self.runner.run_bisync(
            path1=exp_path1,
            path2=exp_path2,
            flags=flags,
            alias=mapping.alias,
            force_resync=force_resync,
        )

        if exit_code == 0:
            new_status = MappingStatus.INIT_SUCCESS if is_init else MappingStatus.SYNC_SUCCESS
            self.state_manager.update_status(
                mapping.path1,
                mapping.path2,
                status=new_status,
                init_success=True,
            )
            self.state_manager.save()
            return True
        else:
            new_status = MappingStatus.INIT_FAILED if is_init else MappingStatus.SYNC_FAILED
            self.state_manager.update_status(
                mapping.path1,
                mapping.path2,
                status=new_status,
                # If it was already init_success=True, keep it True on subsequent sync failure
                init_success=False if is_init else state.init_success,
            )
            self.state_manager.save()
            return False
