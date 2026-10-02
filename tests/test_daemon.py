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

    timer_content = timer_p.read_text()
    assert "OnUnitActiveSec=15m" in timer_content


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
