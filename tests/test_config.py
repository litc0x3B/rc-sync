import json

import pytest
from pydantic import ValidationError

from rc_sync.config import (
    Config,
    MappingConfig,
    get_default_config_template,
    get_json_schema,
    load_config,
    write_schema,
    write_template,
)


def test_mapping_config_valid():
    m = MappingConfig(
        alias="docs",
        path1="/local/docs",
        path2="remote:docs",
        extra_flags="--fast-list",
        allow_resync_non_empty=True,
    )
    assert m.alias == "docs"
    assert m.path1 == "/local/docs"
    assert m.path2 == "remote:docs"
    assert m.extra_flags == "--fast-list"
    assert m.allow_resync_non_empty is True
    assert m.enabled is True


def test_mapping_config_pascal_case():
    m = MappingConfig.model_validate({
        "Alias": "photos",
        "Path1": "~/Photos",
        "Path2": "remote:Photos",
        "ExtraFlagsInit": "--backup-dir old",
        "AllowResyncNonEmpty": False,
        "Enabled": False,
    })
    assert m.alias == "photos"
    assert m.path1 == "~/Photos"
    assert m.extra_flags_init == "--backup-dir old"
    assert m.enabled is False


def test_mapping_config_conflict_extra_and_override():
    with pytest.raises(ValidationError, match="extra_flags.*override_flags"):
        MappingConfig(
            alias="docs",
            path1="p1",
            path2="p2",
            extra_flags="--flag1",
            override_flags="--flag2",
        )


def test_mapping_config_conflict_init():
    with pytest.raises(ValidationError, match="extra_flags_init.*override_flags_init"):
        MappingConfig(
            alias="docs",
            path1="p1",
            path2="p2",
            extra_flags_init="--flag1",
            override_flags_init="--flag2",
        )


def test_global_config_freq_validation():
    # sync_freq_minutes must be >= 3
    with pytest.raises(ValidationError, match="greater than or equal to 3"):
        Config(sync_freq_minutes=2)

    cfg = Config(sync_freq_minutes=3)
    assert cfg.sync_freq_minutes == 3


def test_global_config_unique_aliases():
    m1 = MappingConfig(alias="docs", path1="a", path2="b")
    m2 = MappingConfig(alias="docs", path1="c", path2="d")
    with pytest.raises(ValidationError, match="Duplicate mapping alias"):
        Config(mappings=[m1, m2])


def test_load_config_from_yaml(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
SyncFreqMinutes: 10
RclonePath: /usr/local/bin/rclone
ExtraFlags: "--max-lock %tm"
Mappings:
  - Alias: books
    Path1: /home/user/books
    Path2: remote:books
    OverrideFlags: "--verbose"
""")
    cfg = load_config(config_file)
    assert cfg.sync_freq_minutes == 10
    assert cfg.rclone_path == "/usr/local/bin/rclone"
    assert cfg.extra_flags == "--max-lock %tm"
    assert len(cfg.mappings) == 1
    assert cfg.mappings[0].alias == "books"
    assert cfg.mappings[0].override_flags == "--verbose"


def test_schema_and_template_generation(tmp_path):
    template = get_default_config_template()
    assert "# yaml-language-server: $schema=./schema.json" in template
    assert "sync_freq_minutes: 5" in template

    schema = get_json_schema()
    assert "properties" in schema

    # Write template and schema
    target_config = tmp_path / "subdir" / "config.yaml"
    write_template(target_config)
    assert target_config.exists()
    assert (tmp_path / "subdir" / "schema.json").exists()

    # Re-writing should fail
    with pytest.raises(FileExistsError):
        write_template(target_config)

    # Write schema custom path
    custom_schema = tmp_path / "custom_schema.json"
    write_schema(custom_schema)
    assert custom_schema.exists()
    loaded_schema = json.loads(custom_schema.read_text())
    assert loaded_schema["type"] == "object"


def test_installed_schema_lookup(tmp_path, monkeypatch):
    share_dir = tmp_path / "share" / "rc-sync"
    share_dir.mkdir(parents=True)
    fake_schema = share_dir / "schema.json"
    fake_schema.write_text("{}")

    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "share"))
    template = get_default_config_template()
    assert f"# yaml-language-server: $schema={fake_schema}" in template

