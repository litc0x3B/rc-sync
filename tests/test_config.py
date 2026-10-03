import json
from pathlib import Path

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
        allow_init_non_empty=True,
    )
    assert m.alias == "docs"
    assert m.path1 == "/local/docs"
    assert m.path2 == "remote:docs"
    assert m.extra_flags == "--fast-list"
    assert m.allow_init_non_empty is True
    assert m.enabled is True


def test_mapping_config_rejects_camel_case():
    with pytest.raises(ValidationError):
        MappingConfig.model_validate(
            {
                "Alias": "photos",
                "Path1": "~/Photos",
                "Path2": "remote:Photos",
            }
        )


def test_mapping_config_override_flags():
    m = MappingConfig(
        alias="docs",
        path1="p1",
        path2="p2",
        extra_flags="--flag1",
        override_flags=True,
        extra_flags_init="--flag2",
        override_flags_init=True,
    )
    assert m.override_flags is True
    assert m.override_flags_init is True
    assert m.extra_flags == "--flag1"
    assert m.extra_flags_init == "--flag2"


def test_global_config_freq_validation():
    # sync_freq_minutes must be >= 3
    with pytest.raises(ValidationError, match="greater than or equal to 3"):
        Config(sync_freq_minutes=2)

    cfg = Config(sync_freq_minutes=3)
    assert cfg.sync_freq_minutes == 3


def test_config_rejects_list_format():
    with pytest.raises(ValidationError):
        Config.model_validate(
            {"mappings": [{"alias": "docs", "path1": "a", "path2": "b"}]}
        )


def test_load_config_from_yaml(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
sync_freq_minutes: 10
rclone_path: /usr/local/bin/rclone
global_flags: "--max-lock %tm"
global_flags_init: "--fast-list"
mappings:
  books:
    path1: /home/user/books
    path2: remote:books
    extra_flags: "--verbose"
    override_flags: true
""")
    cfg = load_config(config_file)
    assert cfg.sync_freq_minutes == 10
    assert cfg.rclone_path == "/usr/local/bin/rclone"
    assert cfg.global_flags == "--max-lock %tm"
    assert cfg.global_flags_init == "--fast-list"
    assert len(cfg.mappings) == 1
    assert "books" in cfg.mappings
    assert cfg.mappings["books"].alias == "books"
    assert cfg.mappings["books"].extra_flags == "--verbose"
    assert cfg.mappings["books"].override_flags is True


def test_load_config_from_yaml_dict_format(tmp_path):
    config_file = tmp_path / "config_dict.yaml"
    config_file.write_text("""
sync_freq_minutes: 5
mappings:
  docs:
    path1: ~/Docs
    path2: remote:Docs
    enabled: true
  photos:
    path1: ~/Photos
    path2: remote:Photos
    enabled: false
""")
    cfg = load_config(config_file)
    assert len(cfg.mappings) == 2
    assert "docs" in cfg.mappings
    assert cfg.mappings["docs"].alias == "docs"
    assert cfg.mappings["docs"].enabled is True
    assert "photos" in cfg.mappings
    assert cfg.mappings["photos"].alias == "photos"
    assert cfg.mappings["photos"].enabled is False


def test_config_empty_mapping_key():
    with pytest.raises(ValidationError, match="Mapping alias cannot be empty"):
        Config.model_validate({"mappings": {"": {"path1": "a", "path2": "b"}}})

    with pytest.raises(ValidationError, match="Mapping alias cannot be empty"):
        Config.model_validate({"mappings": {"   ": {"path1": "a", "path2": "b"}}})


def test_schema_and_template_generation(tmp_path, monkeypatch):
    monkeypatch.setattr("rc_sync.config.get_installed_schema_path", lambda: None)
    template = get_default_config_template()
    assert "# yaml-language-server: $schema=./schema.json" in template
    assert "sync_freq_minutes: 5" in template
    assert "enabled: false" in template

    schema = get_json_schema()
    assert "properties" in schema

    # Write template and schema
    target_config = tmp_path / "subdir" / "config.yaml"
    generated_schema = write_template(target_config)
    assert target_config.exists()
    assert generated_schema is not None
    assert generated_schema.exists()
    assert "# yaml-language-server: $schema=./schema.json" in target_config.read_text()

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
    fake_schema = tmp_path / "share" / "rc-sync" / "schema.json"
    monkeypatch.setattr("rc_sync.config.get_installed_schema_path", lambda: fake_schema)
    template = get_default_config_template()
    assert f"# yaml-language-server: $schema={fake_schema}" in template


def test_write_template_with_installed_schema(tmp_path, monkeypatch):
    fake_schema = tmp_path / "share" / "rc-sync" / "schema.json"
    monkeypatch.setattr("rc_sync.config.get_installed_schema_path", lambda: fake_schema)
    target_config = tmp_path / "custom" / "config.yaml"
    generated_schema = write_template(target_config)
    assert generated_schema is None
    assert target_config.exists()
    assert not (tmp_path / "custom" / "schema.json").exists()
    assert f"# yaml-language-server: $schema={fake_schema}" in target_config.read_text()


def test_root_schema_json_sync():
    """Ensure root schema.json is in sync with the Pydantic model."""
    root_schema = Path(__file__).resolve().parent.parent / "schema.json"
    assert root_schema.is_file(), "schema.json must exist in repository root"
    on_disk = json.loads(root_schema.read_text(encoding="utf-8"))
    actual = get_json_schema()
    assert on_disk == actual, (
        "Root schema.json is out of date. Run 'rc-sync config schema print > schema.json'"
    )
