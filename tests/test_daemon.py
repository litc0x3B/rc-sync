from unittest.mock import MagicMock, patch

from rc_sync.config import Config, MappingConfig
from rc_sync.daemon import DaemonManager
from rc_sync.state import MappingStatus, StateManager


def test_daemon_generate_units(tmp_path):
    systemd_dir = tmp_path / "systemd" / "user"
    cfg = Config(
        sync_freq_minutes=15,
        mappings=[MappingConfig(alias="docs", path1="p1", path2="p2")],
    )
    sm = StateManager(tmp_path / "state.json")
    dm = DaemonManager(cfg, sm, systemd_dir=systemd_dir)

    service_p, timer_p = dm.generate_units()
    assert service_p.exists()
    assert timer_p.exists()

    service_content = service_p.read_text()
    assert "ExecStart=" in service_content
    assert "sync all" in service_content
    assert "SyslogIdentifier=rc-sync" in service_content
    assert 'Environment="RC_SYNC_TRIGGER=systemd"' in service_content

    timer_content = timer_p.read_text()
    assert "OnBootSec=1m" in timer_content
    assert "OnActiveSec=1m" in timer_content
    assert "OnUnitInactiveSec=15m" in timer_content


def test_format_status(tmp_path):
    cfg = Config(
        mappings=[
            MappingConfig(alias="docs", path1="~/Docs", path2="remote:Docs", enabled=True),
            MappingConfig(alias="photos", path1="~/Photos", path2="remote:Photos", enabled=False),
        ]
    )
    sm = StateManager(tmp_path / "state.json")
    sm.update_status(
        "~/Docs",
        "remote:Docs",
        status=MappingStatus.SYNC_SUCCESS,
        sync_time="2026-10-02T05:20:00",
    )

    dm = DaemonManager(cfg, sm, systemd_dir=tmp_path / "systemd")

    with patch.object(dm, "get_systemd_status", return_value="Active: active (running)"):
        output = dm.format_status()

    assert "=== Daemon Status (systemd) ===" in output
    assert "Active: active (running)" in output
    assert "=== Mappings Status ===" in output
    assert "- [docs] (~/Docs <-> remote:Docs)" in output
    assert "    Status:    SYNC_SUCCESS" in output
    assert "    Last sync: 2026-10-02T05:20:00" in output
    assert "    Enabled:   true" in output
    assert "- [photos] (~/Photos <-> remote:Photos)" in output
    assert "    Status:    INIT_PENDING" in output
    assert "    Last sync: null" in output
    assert "    Enabled:   false" in output


def test_daemon_enable_disable(tmp_path):
    cfg = Config()
    sm = StateManager(tmp_path / "state.json")
    dm = DaemonManager(cfg, sm, systemd_dir=tmp_path / "systemd")

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        assert dm.enable() == 0
        assert dm.disable() == 0
        assert mock_run.call_count >= 3

        calls = [c[0][0] for c in mock_run.call_args_list]
        assert ["systemctl", "--user", "enable", "rc-sync.timer"] in calls
        assert ["systemctl", "--user", "disable", "rc-sync.timer"] in calls

        mock_run.reset_mock()
        assert dm.enable(now=True) == 0
        assert dm.disable(now=True) == 0
        now_calls = [c[0][0] for c in mock_run.call_args_list]
        assert ["systemctl", "--user", "enable", "--now", "rc-sync.timer"] in now_calls
        assert ["systemctl", "--user", "disable", "--now", "rc-sync.timer"] in now_calls


def test_daemon_up(tmp_path):
    cfg = Config()
    sm = StateManager(tmp_path / "state.json")
    dm = DaemonManager(cfg, sm, systemd_dir=tmp_path / "systemd")

    with (
        patch("subprocess.run") as mock_run,
        patch("rc_sync.daemon.SyncEngine.sync", return_value=0) as mock_sync,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        code = dm.up()
        assert code == 0
        mock_sync.assert_called_once_with(target_aliases=["all"])
        assert mock_run.call_count >= 3


def test_daemon_install_and_remove(tmp_path):
    systemd_dir = tmp_path / "systemd" / "user"
    cfg = Config(sync_freq_minutes=10)
    sm = StateManager(tmp_path / "state.json")
    dm = DaemonManager(cfg, sm, systemd_dir=systemd_dir)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Install
        code = dm.install()
        assert code == 0
        assert (systemd_dir / "rc-sync.service").is_file()
        assert (systemd_dir / "rc-sync.timer").is_file()
        assert "OnUnitInactiveSec=10m" in (systemd_dir / "rc-sync.timer").read_text()

        # Remove
        code = dm.remove()
        assert code == 0
        assert not (systemd_dir / "rc-sync.service").exists()
        assert not (systemd_dir / "rc-sync.timer").exists()


def test_daemon_install_readonly_error(tmp_path):
    systemd_dir = tmp_path / "systemd" / "user"
    systemd_dir.mkdir(parents=True)
    cfg = Config()
    sm = StateManager(tmp_path / "state.json")
    dm = DaemonManager(cfg, sm, systemd_dir=systemd_dir)

    # Existing file with 0444 permissions
    timer_path = systemd_dir / "rc-sync.timer"
    timer_path.write_text("dummy")
    timer_path.chmod(0o444)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        # Install fails because timer_path is read-only
        assert dm.install() == 1

    # Cleanup permissions so tmp_path can be deleted
    timer_path.chmod(0o644)


def test_daemon_remove_readonly_error(tmp_path):
    systemd_dir = tmp_path / "systemd" / "user"
    systemd_dir.mkdir(parents=True)
    cfg = Config()
    sm = StateManager(tmp_path / "state.json")
    dm = DaemonManager(cfg, sm, systemd_dir=systemd_dir)

    (systemd_dir / "rc-sync.timer").write_text("dummy")
    # Make directory read-only
    systemd_dir.chmod(0o555)

    try:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            assert dm.remove() == 1
    finally:
        systemd_dir.chmod(0o755)


def test_daemon_remove_write_protected_file_succeeds_in_writable_dir(tmp_path):
    systemd_dir = tmp_path / "systemd" / "user"
    systemd_dir.mkdir(parents=True)
    cfg = Config()
    sm = StateManager(tmp_path / "state.json")
    dm = DaemonManager(cfg, sm, systemd_dir=systemd_dir)

    timer_path = systemd_dir / "rc-sync.timer"
    timer_path.write_text("dummy")
    timer_path.chmod(0o444)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        # In POSIX, unlinking a write-protected file in a writable directory succeeds
        assert dm.remove() == 0
        assert not timer_path.exists()


def test_daemon_writable_symlink_supported(tmp_path):
    systemd_dir = tmp_path / "systemd" / "user"
    systemd_dir.mkdir(parents=True)

    # Point to an external writable dotfiles directory
    dotfiles_dir = tmp_path / "dotfiles"
    dotfiles_dir.mkdir()
    target_service = dotfiles_dir / "rc-sync.service"
    target_timer = dotfiles_dir / "rc-sync.timer"
    target_service.write_text("old service")
    target_timer.write_text("[Timer]\nOnUnitInactiveSec=5m\nPersistent=true\n")

    (systemd_dir / "rc-sync.service").symlink_to(target_service)
    (systemd_dir / "rc-sync.timer").symlink_to(target_timer)

    cfg = Config(sync_freq_minutes=15)
    sm = StateManager(tmp_path / "state.json")
    dm = DaemonManager(cfg, sm, systemd_dir=systemd_dir)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        # sync_timer_frequency works through the symlink!
        assert dm.sync_timer_frequency() is True
        assert "OnUnitInactiveSec=15m" in target_timer.read_text()

        # install works through the symlink!
        assert dm.install() == 0

        # remove unlinks the symlink from systemd_dir without deleting dotfiles
        assert dm.remove() == 0
        assert not (systemd_dir / "rc-sync.timer").exists()
        assert target_timer.exists()


def test_sync_timer_frequency_not_existing(tmp_path):
    systemd_dir = tmp_path / "systemd" / "user"
    cfg = Config(sync_freq_minutes=10)
    sm = StateManager(tmp_path / "state.json")
    dm = DaemonManager(cfg, sm, systemd_dir=systemd_dir)

    # Timer doesn't exist -> no update, returns False
    assert dm.sync_timer_frequency() is False


def test_sync_timer_frequency_matches(tmp_path):
    systemd_dir = tmp_path / "systemd" / "user"
    systemd_dir.mkdir(parents=True)
    timer_path = systemd_dir / "rc-sync.timer"
    timer_path.write_text("[Timer]\nOnUnitInactiveSec=10m\nPersistent=true\n")

    cfg = Config(sync_freq_minutes=10)
    sm = StateManager(tmp_path / "state.json")
    dm = DaemonManager(cfg, sm, systemd_dir=systemd_dir)

    assert dm.sync_timer_frequency() is False
    assert "OnUnitInactiveSec=10m" in timer_path.read_text()


def test_sync_timer_frequency_updates_active_timer(tmp_path):
    systemd_dir = tmp_path / "systemd" / "user"
    systemd_dir.mkdir(parents=True)
    timer_path = systemd_dir / "rc-sync.timer"
    # Test upgrading from legacy OnUnitActiveSec=5m
    timer_path.write_text("[Timer]\nOnUnitActiveSec=5m\nPersistent=true\n")

    cfg = Config(sync_freq_minutes=15)
    sm = StateManager(tmp_path / "state.json")
    dm = DaemonManager(cfg, sm, systemd_dir=systemd_dir)

    with patch("subprocess.run") as mock_run:
        # Mock daemon-reload and is-active returning 0 (active)
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        assert dm.sync_timer_frequency() is True
        assert "OnUnitInactiveSec=15m" in timer_path.read_text()
        assert "OnActiveSec=1m" in timer_path.read_text()
        assert "OnBootSec=1m" in timer_path.read_text()
        # Verify restart was called
        calls = [c[0][0] for c in mock_run.call_args_list]
        assert ["systemctl", "--user", "daemon-reload"] in calls
        assert ["systemctl", "--user", "restart", "rc-sync.timer"] in calls


def test_sync_timer_frequency_readonly_skipped(tmp_path):
    systemd_dir = tmp_path / "systemd" / "user"
    systemd_dir.mkdir(parents=True)
    timer_path = systemd_dir / "rc-sync.timer"
    timer_path.write_text("[Timer]\nOnUnitInactiveSec=5m\nPersistent=true\n")
    timer_path.chmod(0o444)

    cfg = Config(sync_freq_minutes=15)
    sm = StateManager(tmp_path / "state.json")
    dm = DaemonManager(cfg, sm, systemd_dir=systemd_dir)

    try:
        with patch("subprocess.run") as mock_run:
            assert dm.sync_timer_frequency() is False
            mock_run.assert_not_called()
            assert "OnUnitInactiveSec=5m" in timer_path.read_text()
    finally:
        timer_path.chmod(0o644)


def test_daemon_render_units(tmp_path):
    cfg = Config(sync_freq_minutes=20)
    sm = StateManager(tmp_path / "state.json")
    dm = DaemonManager(cfg, sm, systemd_dir=tmp_path)

    service_txt = dm.render_service_unit()
    assert "ExecStart=" in service_txt
    assert "sync all" in service_txt

    custom_service_txt = dm.render_service_unit(exec_path="/custom/path/rc-sync")
    assert "ExecStart=/custom/path/rc-sync sync all" in custom_service_txt

    timer_txt = dm.render_timer_unit()
    assert "OnBootSec=1m" in timer_txt
    assert "OnActiveSec=1m" in timer_txt
    assert "OnUnitInactiveSec=20m" in timer_txt
    assert "Persistent=true" in timer_txt


def test_daemon_generate_units_with_exec_path(tmp_path):
    systemd_dir = tmp_path / "systemd" / "user"
    cfg = Config(sync_freq_minutes=10)
    sm = StateManager(tmp_path / "state.json")
    dm = DaemonManager(cfg, sm, systemd_dir=systemd_dir)

    service_p, timer_p = dm.generate_units(exec_path="/nix/store/test-rc-sync/bin/rc-sync")
    service_content = service_p.read_text()
    assert "ExecStart=/nix/store/test-rc-sync/bin/rc-sync sync all" in service_content


def test_daemon_generate_units_skips_write_when_up_to_date(tmp_path):
    systemd_dir = tmp_path / "systemd" / "user"
    cfg = Config(sync_freq_minutes=10)
    sm = StateManager(tmp_path / "state.json")
    dm = DaemonManager(cfg, sm, systemd_dir=systemd_dir)

    service_p, timer_p = dm.generate_units()
    mtime_service = service_p.stat().st_mtime_ns
    mtime_timer = timer_p.stat().st_mtime_ns

    # Call again without changing config - files should not be rewritten
    with patch.object(dm._logger, "info") as mock_info:
        dm.generate_units()
        calls = [c.args[0] for c in mock_info.call_args_list if c.args]
        assert any("rc-sync.service already exists with desired configuration" in msg for msg in calls)
        assert any("rc-sync.timer already exists with desired configuration" in msg for msg in calls)

    assert service_p.stat().st_mtime_ns == mtime_service
    assert timer_p.stat().st_mtime_ns == mtime_timer


def test_daemon_enable_succeeds_when_units_are_readonly_but_up_to_date(tmp_path):
    systemd_dir = tmp_path / "systemd" / "user"
    cfg = Config(sync_freq_minutes=10)
    sm = StateManager(tmp_path / "state.json")
    dm = DaemonManager(cfg, sm, systemd_dir=systemd_dir)

    # Initial generation
    service_p, timer_p = dm.generate_units()

    # Make them read-only (simulate Home Manager / Nix store permissions)
    service_p.chmod(0o444)
    timer_p.chmod(0o444)

    try:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            # enable succeeds without trying to write to read-only files
            assert dm.enable() == 0
            # up succeeds without trying to write to read-only files
            with patch("rc_sync.daemon.SyncEngine.sync", return_value=0):
                assert dm.up() == 0
    finally:
        service_p.chmod(0o644)
        timer_p.chmod(0o644)


def test_daemon_enable_and_up_fail_when_unit_needs_changes_and_is_readonly(tmp_path):
    systemd_dir = tmp_path / "systemd" / "user"
    cfg = Config(sync_freq_minutes=10)
    sm = StateManager(tmp_path / "state.json")
    dm = DaemonManager(cfg, sm, systemd_dir=systemd_dir)

    service_p, timer_p = dm.generate_units()

    # Outdated timer content that doesn't match config (e.g. 5m vs 10m) and is read-only
    timer_p.write_text("[Timer]\nOnUnitInactiveSec=5m\n")
    timer_p.chmod(0o444)

    try:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            # Fails with exit code 1 because changes were actually needed and could not be written
            assert dm.enable() == 1
            assert dm.up() == 1
    finally:
        timer_p.chmod(0o644)


def test_daemon_start_and_stop(tmp_path):
    cfg = Config()
    sm = StateManager(tmp_path / "state.json")
    dm = DaemonManager(cfg, sm, systemd_dir=tmp_path / "systemd")

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        assert dm.start() == 0
        assert dm.stop() == 0

        calls = [c[0][0] for c in mock_run.call_args_list]
        assert ["systemctl", "--user", "start", "rc-sync.timer"] in calls
        assert ["systemctl", "--user", "stop", "rc-sync.timer", "rc-sync.service"] in calls


