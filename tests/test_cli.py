import json
import os
from unittest.mock import MagicMock, patch

from rc_sync.cli import main


def test_cli_config_template_print(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("rc_sync.config.get_installed_schema_path", lambda: None)
    monkeypatch.setattr("rc_sync.cli.get_installed_schema_path", lambda: None)
    target_config = tmp_path / "cfg" / "config.yaml"
    with patch.dict(os.environ, {"RC_SYNC_CONFIG_PATH": str(target_config)}):
        code = main(["config", "template", "print"])
        assert code == 0
        captured = capsys.readouterr()
        expected_schema = tmp_path / "cfg" / "schema.json"
        assert expected_schema.exists()
        assert "# yaml-language-server: $schema=./schema.json" in captured.out
        assert "sync_freq_minutes: 5" in captured.out


def test_cli_config_template_print_with_global_schema(tmp_path, capsys, monkeypatch):
    share_dir = tmp_path / "share" / "rc-sync"
    share_dir.mkdir(parents=True)
    fake_schema = share_dir / "schema.json"
    fake_schema.write_text("{}")

    target_config = tmp_path / "cfg" / "config.yaml"
    monkeypatch.setattr("rc_sync.config.get_installed_schema_path", lambda: fake_schema)
    monkeypatch.setattr("rc_sync.cli.get_installed_schema_path", lambda: fake_schema)
    monkeypatch.setenv("RC_SYNC_CONFIG_PATH", str(target_config))

    code = main(["config", "template", "print"])
    assert code == 0
    captured = capsys.readouterr()
    assert f"# yaml-language-server: $schema={fake_schema}" in captured.out
    assert not (tmp_path / "cfg" / "schema.json").exists()


def test_cli_config_template_gen(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("rc_sync.config.get_installed_schema_path", lambda: None)
    monkeypatch.setattr("rc_sync.cli.get_installed_schema_path", lambda: None)
    target_config = tmp_path / "cfg" / "config.yaml"
    with patch.dict(os.environ, {"RC_SYNC_CONFIG_PATH": str(target_config)}):
        code = main(["config", "template", "gen"])
        assert code == 0
        expected_schema = tmp_path / "cfg" / "schema.json"
        assert expected_schema.exists()
        assert "# yaml-language-server: $schema=./schema.json" in target_config.read_text()

        # Running again should fail
        code2 = main(["config", "template", "gen"])
        assert code2 == 1


def test_cli_config_template_gen_with_global_schema(tmp_path, capsys, monkeypatch):
    share_dir = tmp_path / "share" / "rc-sync"
    share_dir.mkdir(parents=True)
    fake_schema = share_dir / "schema.json"
    fake_schema.write_text("{}")

    target_config = tmp_path / "cfg" / "config.yaml"
    monkeypatch.setattr("rc_sync.config.get_installed_schema_path", lambda: fake_schema)
    monkeypatch.setattr("rc_sync.cli.get_installed_schema_path", lambda: fake_schema)
    monkeypatch.setenv("RC_SYNC_CONFIG_PATH", str(target_config))

    code = main(["config", "template", "gen"])
    assert code == 0
    assert target_config.exists()
    assert not (tmp_path / "cfg" / "schema.json").exists()
    assert f"# yaml-language-server: $schema={fake_schema}" in target_config.read_text()

    captured = capsys.readouterr()
    assert f"Using installed JSON Schema at: {fake_schema}" in captured.out


def test_cli_config_schema_print(capsys):
    code = main(["config", "schema", "print"])
    assert code == 0
    captured = capsys.readouterr()
    schema = json.loads(captured.out)
    assert schema["type"] == "object"


def test_cli_config_schema_gen(tmp_path):
    schema_path = tmp_path / "custom" / "schema.json"
    code = main(["config", "schema", "gen", str(schema_path)])
    assert code == 0
    assert schema_path.exists()


def test_cli_config_validate(tmp_path, capsys):
    valid_cfg = tmp_path / "valid.yaml"
    cfg_text = "sync_freq_minutes: 5\nmappings:\n  m:\n    path1: a\n    path2: b\n"
    valid_cfg.write_text(cfg_text)

    code = main(["config", "validate", str(valid_cfg)])
    assert code == 0
    captured = capsys.readouterr()
    assert "Configuration is valid:" in captured.out

    invalid_cfg = tmp_path / "invalid.yaml"
    invalid_cfg.write_text("sync_freq_minutes: 1\n")
    code2 = main(["config", "validate", str(invalid_cfg)])
    assert code2 == 1


def test_cli_config_show(tmp_path, capsys):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "sync_freq_minutes: 6\nmappings:\n  m:\n    path1: a\n    path2: b\n"
    )
    with patch.dict(os.environ, {"RC_SYNC_CONFIG_PATH": str(cfg_file)}):
        code = main(["config", "show"])
        assert code == 0
        captured = capsys.readouterr()
        assert "sync_freq_minutes: 6" in captured.out


def test_cli_status(tmp_path, capsys):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "sync_freq_minutes: 5\nmappings:\n  m:\n    path1: a\n    path2: b\n"
    )
    with (
        patch.dict(
            os.environ,
            {
                "RC_SYNC_CONFIG_PATH": str(cfg_file),
                "RC_SYNC_STATE_PATH": str(tmp_path / "state.json"),
            },
        ),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="Unit status mock", stderr="")
        code = main(["status"])
        assert code == 0
        captured = capsys.readouterr()
        assert "=== Daemon Status (systemd) ===" in captured.out
        assert "Unit status mock" in captured.out
        assert "=== Mappings Status ===" in captured.out
        assert "- [m] (a <-> b)" in captured.out


def test_cli_sync_invocation(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "sync_freq_minutes: 5\nmappings:\n  m:\n    path1: a\n    path2: b\n"
    )
    with (
        patch.dict(
            os.environ,
            {
                "RC_SYNC_CONFIG_PATH": str(cfg_file),
                "RC_SYNC_STATE_PATH": str(tmp_path / "state.json"),
                "RC_SYNC_LOCK_PATH": str(tmp_path / "test.lock"),
            },
        ),
        patch("rc_sync.sync_engine.SyncEngine.sync", return_value=0) as mock_sync,
    ):
        code = main(["sync", "m", "--resync"])
        assert code == 0
        mock_sync.assert_called_once_with(
            target_aliases=["m"],
            cli_override_flags=False,
            cli_extra_flags="--resync",
        )

        mock_sync.reset_mock()
        code2 = main(["sync", "m", "--resync", "--dry-run", "-v"])
        assert code2 == 0
        mock_sync.assert_called_once_with(
            target_aliases=["m"],
            cli_override_flags=False,
            cli_extra_flags="--resync --dry-run -v",
        )


def test_cli_sync_help(capsys):
    code = main(["sync", "--help"])
    assert code == 0
    captured = capsys.readouterr()
    assert "TARGETS... [ARGS]..." in captured.out
    assert "Trailing arguments (e.g. '--resync', '--dry-run', '-v') are forwarded" in captured.out


def test_cli_daemon_enable(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("sync_freq_minutes: 5\n")
    with (
        patch.dict(
            os.environ,
            {
                "RC_SYNC_CONFIG_PATH": str(cfg_file),
                "RC_SYNC_STATE_PATH": str(tmp_path / "state.json"),
            },
        ),
        patch("rc_sync.daemon.DaemonManager.enable", return_value=0) as mock_enable,
    ):
        code = main(["daemon", "enable"])
        assert code == 0
        mock_enable.assert_called_once()


def test_cli_daemon_install_and_remove(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("sync_freq_minutes: 5\n")
    with (
        patch.dict(
            os.environ,
            {
                "RC_SYNC_CONFIG_PATH": str(cfg_file),
                "RC_SYNC_STATE_PATH": str(tmp_path / "state.json"),
            },
        ),
        patch("rc_sync.daemon.DaemonManager.install", return_value=0) as mock_install,
        patch("rc_sync.daemon.DaemonManager.remove", return_value=0) as mock_remove,
    ):
        code1 = main(["daemon", "install"])
        assert code1 == 0
        mock_install.assert_called_once()

        code2 = main(["daemon", "remove"])
        assert code2 == 0
        mock_remove.assert_called_once()


def test_cli_rclone_passthrough():
    with patch("shutil.which", return_value="/usr/bin/rclone"), patch("os.execvp") as mock_exec:
        main(["rclone", "config", "--param", "val"])
        mock_exec.assert_called_once_with(
            "/usr/bin/rclone",
            ["/usr/bin/rclone", "config", "--param", "val"],
        )


def test_cli_config_template_print(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("rc_sync.config.get_installed_schema_path", lambda: None)
    monkeypatch.setattr("rc_sync.cli.get_installed_schema_path", lambda: None)
    target_config = tmp_path / "cfg" / "config.yaml"
    with patch.dict(os.environ, {"RC_SYNC_CONFIG_PATH": str(target_config)}):
        code = main(["config", "template", "print"])
        assert code == 0
        captured = capsys.readouterr()
        assert "# yaml-language-server: $schema=./schema.json" in captured.out
        assert "sync_freq_minutes: 5" in captured.out


def test_cli_config_schema_print(capsys):
    code = main(["config", "schema", "print"])
    assert code == 0
    captured = capsys.readouterr()
    schema = json.loads(captured.out)
    assert schema["type"] == "object"


def test_cli_daemon_print_service(capsys):
    code = main(["daemon", "print", "service"])
    assert code == 0
    captured = capsys.readouterr()
    assert "[Unit]" in captured.out
    assert "ExecStart=" in captured.out
    assert "sync all" in captured.out


def test_cli_daemon_print_service_with_exec_path(capsys):
    custom_path = "/nix/store/test-package/bin/rc-sync"
    code = main(["daemon", "print", "service", "--exec-path", custom_path])
    assert code == 0
    captured = capsys.readouterr()
    assert f"ExecStart={custom_path} sync all" in captured.out


def test_cli_daemon_print_timer(tmp_path, capsys):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("sync_freq_minutes: 25\n")
    with patch.dict(os.environ, {"RC_SYNC_CONFIG_PATH": str(cfg_file)}):
        code = main(["daemon", "print", "timer"])
        assert code == 0
        captured = capsys.readouterr()
        assert "OnBootSec=1m" in captured.out
        assert "OnActiveSec=1m" in captured.out
        assert "OnUnitInactiveSec=25m" in captured.out
        assert "Persistent=true" in captured.out


def test_cli_daemon_commands_with_exec_path(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("sync_freq_minutes: 5\n")
    custom_path = "/custom/bin/rc-sync"
    with (
        patch.dict(
            os.environ,
            {
                "RC_SYNC_CONFIG_PATH": str(cfg_file),
                "RC_SYNC_STATE_PATH": str(tmp_path / "state.json"),
            },
        ),
        patch("rc_sync.daemon.DaemonManager.install", return_value=0) as mock_install,
        patch("rc_sync.daemon.DaemonManager.enable", return_value=0) as mock_enable,
        patch("rc_sync.daemon.DaemonManager.up", return_value=0) as mock_up,
    ):
        assert main(["daemon", "install", "--exec-path", custom_path]) == 0
        mock_install.assert_called_once_with(exec_path=custom_path)

        assert main(["daemon", "enable", "--exec-path", custom_path]) == 0
        mock_enable.assert_called_once_with(exec_path=custom_path, now=False)

        assert main(["daemon", "up", "--exec-path", custom_path]) == 0
        mock_up.assert_called_once_with(exec_path=custom_path)


def test_cli_daemon_start_and_stop(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("sync_freq_minutes: 5\n")
    with (
        patch.dict(
            os.environ,
            {
                "RC_SYNC_CONFIG_PATH": str(cfg_file),
                "RC_SYNC_STATE_PATH": str(tmp_path / "state.json"),
            },
        ),
        patch("rc_sync.daemon.DaemonManager.start", return_value=0) as mock_start,
        patch("rc_sync.daemon.DaemonManager.stop", return_value=0) as mock_stop,
    ):
        assert main(["daemon", "start"]) == 0
        mock_start.assert_called_once_with(exec_path=None)

        assert main(["daemon", "stop"]) == 0
        mock_stop.assert_called_once()


def test_cli_daemon_enable_disable_now(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("sync_freq_minutes: 5\n")
    with (
        patch.dict(
            os.environ,
            {
                "RC_SYNC_CONFIG_PATH": str(cfg_file),
                "RC_SYNC_STATE_PATH": str(tmp_path / "state.json"),
            },
        ),
        patch("rc_sync.daemon.DaemonManager.enable", return_value=0) as mock_enable,
        patch("rc_sync.daemon.DaemonManager.disable", return_value=0) as mock_disable,
    ):
        assert main(["daemon", "enable", "--now"]) == 0
        mock_enable.assert_called_once_with(exec_path=None, now=True)

        assert main(["daemon", "disable", "--now"]) == 0
        mock_disable.assert_called_once_with(now=True)


