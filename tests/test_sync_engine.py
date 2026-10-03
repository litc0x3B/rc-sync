from unittest.mock import patch

from rc_sync.config import Config, MappingConfig
from rc_sync.rclone import LsfResult
from rc_sync.state import MappingStatus, StateManager
from rc_sync.sync_engine import SyncEngine


def test_sync_engine_unknown_alias(tmp_path):
    cfg = Config(mappings={"docs": MappingConfig(path1="/p1", path2="/p2")})
    sm = StateManager(tmp_path / "state.json")
    engine = SyncEngine(cfg, sm, lock_path=tmp_path / "test.lock")

    code = engine.sync(["unknown"])
    assert code == 1


def test_sync_engine_empty_precondition_failure(tmp_path):
    # Both paths non-empty and allow_init_non_empty=False
    cfg = Config(
        mappings={
            "docs": MappingConfig(
                path1="/p1",
                path2="/p2",
                allow_init_non_empty=False,
            )
        }
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
        mappings={
            "docs": MappingConfig(
                path1="/p1",
                path2="/p2",
                allow_init_non_empty=False,
            )
        }
    )
    sm = StateManager(tmp_path / "state.json")
    engine = SyncEngine(cfg, sm, lock_path=tmp_path / "test.lock")

    # path1 exists with files, path2 does not exist (code 3 -> exists=False, is_empty=True)
    with (
        patch.object(
            engine.runner,
            "check_path_lsf",
            side_effect=[
                LsfResult(exists=True, is_empty=False),
                LsfResult(exists=False, is_empty=True),
            ],
        ),
        patch.object(engine.runner, "mkdir", return_value=(0, "")) as mock_mkdir,
        patch.object(engine.runner, "run_bisync", return_value=0) as mock_bisync,
    ):
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
        mappings={
            "m1": MappingConfig(path1="/p1", path2="/p2"),
            "m2": MappingConfig(path1="/p3", path2="/p4"),
        }
    )
    sm = StateManager(tmp_path / "state.json")
    # Mark m1 as already initialized
    sm.update_status("/p1", "/p2", MappingStatus.INIT_SUCCESS, init_success=True)
    # Mark m2 as already initialized
    sm.update_status("/p3", "/p4", MappingStatus.INIT_SUCCESS, init_success=True)
    sm.save()

    engine = SyncEngine(cfg, sm, lock_path=tmp_path / "test.lock")

    # m1 fails, m2 succeeds
    with patch.object(engine.runner, "run_bisync", side_effect=[1, 0]):
        code = engine.sync(["all"])

    assert code == 1  # 1 because one failed
    state1 = sm.get_state("/p1", "/p2")
    assert state1.status == MappingStatus.SYNC_FAILED
    assert state1.init_success is True  # Remains True!

    state2 = sm.get_state("/p3", "/p4")
    assert state2.status == MappingStatus.SYNC_SUCCESS
    assert state2.init_success is True


def test_sync_engine_initial_resync_uses_init_flags(tmp_path):
    cfg = Config(
        global_flags="--regular-only",
        global_flags_init="--global-init",
        mappings={
            "docs": MappingConfig(
                path1="/p1",
                path2="/p2",
                extra_flags_init="--mapping-init",
                allow_init_non_empty=True,
            )
        },
    )
    sm = StateManager(tmp_path / "state.json")
    engine = SyncEngine(cfg, sm, lock_path=tmp_path / "test.lock")

    with (
        patch.object(
            engine.runner,
            "check_path_lsf",
            side_effect=[
                LsfResult(exists=True, is_empty=False),
                LsfResult(exists=True, is_empty=False),
            ],
        ),
        patch.object(engine.runner, "run_bisync", return_value=0) as mock_bisync,
    ):
        code = engine.sync(["docs"])

    assert code == 0
    mock_bisync.assert_called_once()
    flags = mock_bisync.call_args[1]["flags"]
    assert flags == ["--global-init", "--mapping-init", "--regular-only", "--resync"]


def test_sync_engine_override_flags(tmp_path):
    cfg = Config(
        global_flags="--regular-global",
        global_flags_init="--global-init",
        mappings={
            "docs": MappingConfig(
                path1="/p1",
                path2="/p2",
                extra_flags="--only-mapping",
                override_flags=True,
                allow_init_non_empty=True,
            )
        },
    )
    sm = StateManager(tmp_path / "state.json")
    sm.update_status("/p1", "/p2", MappingStatus.SYNC_SUCCESS, init_success=True)
    sm.save()
    engine = SyncEngine(cfg, sm, lock_path=tmp_path / "test.lock")

    with patch.object(engine.runner, "run_bisync", return_value=0) as mock_bisync:
        code = engine.sync(["docs"])

    assert code == 0
    flags = mock_bisync.call_args[1]["flags"]
    assert flags == ["--only-mapping"]


def test_sync_engine_reloads_state_after_lock(tmp_path):
    cfg = Config(
        mappings={
            "docs": MappingConfig(
                path1="/p1",
                path2="/p2",
                allow_init_non_empty=False,
            )
        }
    )
    state_file = tmp_path / "state.json"
    sm = StateManager(state_file)
    engine = SyncEngine(cfg, sm, lock_path=tmp_path / "test.lock")

    # Initially in-memory sm has init_success=False
    assert sm.get_state("/p1", "/p2").init_success is False

    # Simulate another process writing init_success=True to disk before or during lock wait
    other_sm = StateManager(state_file)
    other_sm.update_status("/p1", "/p2", MappingStatus.INIT_SUCCESS, init_success=True)
    other_sm.save()

    with patch.object(engine.runner, "run_bisync", return_value=0) as mock_bisync:
        code = engine.sync(["docs"])

    assert code == 0
    # Because it reloaded state under lock, it saw init_success=True and ran regular sync
    assert mock_bisync.call_args[1]["force_resync"] is False
    assert mock_bisync.call_args[1]["context"] == "sync:docs"


def test_sync_engine_force_resync(tmp_path):
    cfg = Config(
        mappings={
            "docs": MappingConfig(
                path1="/p1",
                path2="/p2",
                allow_init_non_empty=False,
            )
        }
    )
    sm = StateManager(tmp_path / "state.json")
    sm.update_status("/p1", "/p2", MappingStatus.SYNC_SUCCESS, init_success=True)
    sm.save()

    engine = SyncEngine(cfg, sm, lock_path=tmp_path / "test.lock")

    with (
        patch.object(
            engine.runner,
            "check_path_lsf",
            return_value=LsfResult(exists=True, is_empty=False),
        ),
        patch.object(engine.runner, "run_bisync", return_value=0) as mock_bisync,
    ):
        code = engine.sync(["docs"], force_resync=True)

    assert code == 0
    assert mock_bisync.call_args[1]["force_resync"] is True
    assert mock_bisync.call_args[1]["context"] == "init:docs"
