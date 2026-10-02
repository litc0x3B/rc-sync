import json
import os
from unittest.mock import MagicMock, patch

from rc_sync.cli import main


def test_cli_config_template_cat(capsys):
    code = main(["config", "template", "cat"])
    assert code == 0
    captured = capsys.readouterr()
    assert "# yaml-language-server: $schema=./schema.json" in captured.out
    assert "sync_freq_minutes: 5" in captured.out


def test_cli_config_template_gen(tmp_path, capsys):
    target_config = tmp_path / "cfg" / "config.yaml"
    with patch.dict(os.environ, {"RC_SYNC_CONFIG_PATH": str(target_config)}):
        code = main(["config", "template", "gen"])
        assert code == 0
        assert target_config.exists()
        assert (tmp_path / "cfg" / "schema.json").exists()

        # Running again should fail
        code2 = main(["config", "template", "gen"])
        assert code2 == 1


def test_cli_config_schema_cat(capsys):
    code = main(["config", "schema", "cat"])
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
    cfg_text = "SyncFreqMinutes: 5\nMappings:\n  - Alias: m\n    Path1: a\n    Path2: b\n"
    valid_cfg.write_text(cfg_text)

    code = main(["config", "validate", str(valid_cfg)])
    assert code == 0
    captured = capsys.readouterr()
    assert "Configuration is valid:" in captured.out

    invalid_cfg = tmp_path / "invalid.yaml"
    invalid_cfg.write_text("SyncFreqMinutes: 1\n")
    code2 = main(["config", "validate", str(invalid_cfg)])
    assert code2 == 1


def test_cli_config_show(tmp_path, capsys):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("SyncFreqMinutes: 6\nMappings:\n  - Alias: m\n    Path1: a\n    Path2: b\n")
    with patch.dict(os.environ, {"RC_SYNC_CONFIG_PATH": str(cfg_file)}):
        code = main(["config", "show"])
        assert code == 0
        captured = capsys.readouterr()
        assert "sync_freq_minutes: 6" in captured.out


def test_cli_status(tmp_path, capsys):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("SyncFreqMinutes: 5\nMappings:\n  - Alias: m\n    Path1: a\n    Path2: b\n")
    with patch.dict(os.environ, {
        "RC_SYNC_CONFIG_PATH": str(cfg_file),
        "RC_SYNC_STATE_PATH": str(tmp_path / "state.json"),
    }), patch("subprocess.run") as mock_run:
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
    cfg_file.write_text("SyncFreqMinutes: 5\nMappings:\n  - Alias: m\n    Path1: a\n    Path2: b\n")
    with patch.dict(os.environ, {
        "RC_SYNC_CONFIG_PATH": str(cfg_file),
        "RC_SYNC_STATE_PATH": str(tmp_path / "state.json"),
        "RC_SYNC_LOCK_PATH": str(tmp_path / "test.lock"),
    }), patch("rc_sync.sync_engine.SyncEngine.sync", return_value=0) as mock_sync:
        code = main(["sync", "m", "--resync"])
        assert code == 0
        mock_sync.assert_called_once_with(
            target_aliases=["m"],
            force_resync=True,
            cli_override_flags=False,
            cli_extra_flags="",
        )


def test_cli_daemon_enable(tmp_path):
    with patch("rc_sync.daemon.DaemonManager.enable", return_value=0) as mock_enable:
        code = main(["daemon", "enable"])
        assert code == 0
        mock_enable.assert_called_once()
