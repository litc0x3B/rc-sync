from unittest.mock import patch

from rc_sync.config import Config, MappingConfig
from rc_sync.rclone import LsfResult
from rc_sync.state import MappingStatus, StateManager
from rc_sync.sync_engine import SyncEngine


def test_sync_engine_unknown_alias(tmp_path):
    cfg = Config(
        mappings=[MappingConfig(alias="docs", path1="/p1", path2="/p2")]
    )
    sm = StateManager(tmp_path / "state.json")
    engine = SyncEngine(cfg, sm, lock_path=tmp_path / "test.lock")

    code = engine.sync(["unknown"])
    assert code == 1


def test_sync_engine_empty_precondition_failure(tmp_path):
    # Both paths non-empty and allow_resync_non_empty=False
    cfg = Config(
        mappings=[
            MappingConfig(
                alias="docs",
                path1="/p1",
                path2="/p2",
                allow_resync_non_empty=False,
            )
        ]
    )
    sm = StateManager(tmp_path / "state.json")
    engine = SyncEngine(cfg, sm, lock_path=tmp_path / "test.lock")

    with patch.object(
        engine.runner,
        "check_path_lsf",
        side_effect=[
            LsfResult(exists=True, is_empty=False),
            LsfResult(exists=True, is_empty=False),
        ],
    ):
        code = engine.sync(["docs"])

    assert code == 1
    state = sm.get_state("/p1", "/p2")
    assert state.status == MappingStatus.INIT_FAILED
    assert state.init_success is False


def test_sync_engine_initial_resync_success(tmp_path):
    cfg = Config(
        mappings=[
            MappingConfig(
                alias="docs",
                path1="/p1",
                path2="/p2",
                allow_resync_non_empty=False,
            )
        ]
    )
    sm = StateManager(tmp_path / "state.json")
    engine = SyncEngine(cfg, sm, lock_path=tmp_path / "test.lock")

    # path1 exists with files, path2 does not exist (code 3 -> exists=False, is_empty=True)
    with patch.object(
        engine.runner,
        "check_path_lsf",
        side_effect=[
            LsfResult(exists=True, is_empty=False),
            LsfResult(exists=False, is_empty=True),
        ],
    ), patch.object(
        engine.runner, "mkdir", return_value=(0, "")
    ) as mock_mkdir, patch.object(
        engine.runner, "run_bisync", return_value=0
    ) as mock_bisync:
        code = engine.sync(["docs"])

    assert code == 0
    mock_mkdir.assert_called_once_with("/p2")
    mock_bisync.assert_called_once()
    assert mock_bisync.call_args[1]["force_resync"] is True

    state = sm.get_state("/p1", "/p2")
    assert state.status == MappingStatus.INIT_SUCCESS
    assert state.init_success is True


def test_sync_engine_sequential_batch(tmp_path):
    cfg = Config(
        mappings=[
            MappingConfig(alias="m1", path1="/p1", path2="/p2"),
            MappingConfig(alias="m2", path1="/p3", path2="/p4"),
        ]
    )
    sm = StateManager(tmp_path / "state.json")
    # Mark m1 as already initialized
    sm.update_status("/p1", "/p2", MappingStatus.INIT_SUCCESS, init_success=True)
    # Mark m2 as already initialized
    sm.update_status("/p3", "/p4", MappingStatus.INIT_SUCCESS, init_success=True)

    engine = SyncEngine(cfg, sm, lock_path=tmp_path / "test.lock")

    # m1 fails, m2 succeeds
    with patch.object(
        engine.runner, "run_bisync", side_effect=[1, 0]
    ):
        code = engine.sync(["all"])

    assert code == 1  # 1 because one failed
    state1 = sm.get_state("/p1", "/p2")
    assert state1.status == MappingStatus.SYNC_FAILED
    assert state1.init_success is True  # Remains True!

    state2 = sm.get_state("/p3", "/p4")
    assert state2.status == MappingStatus.SYNC_SUCCESS
    assert state2.init_success is True
