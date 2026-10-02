"""Configuration data models, validation, and serialization."""

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from rc_sync.paths import get_installed_schema_path


class MappingConfig(BaseModel):
    """Configuration for an individual sync mapping."""

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )

    alias: str = Field(
        ...,
        validation_alias=AliasChoices("alias", "Alias"),
        description="Unique identifier for the mapping",
    )
    path1: str = Field(
        ...,
        validation_alias=AliasChoices("path1", "Path1"),
        description="First path (local or remote)",
    )
    path2: str = Field(
        ...,
        validation_alias=AliasChoices("path2", "Path2"),
        description="Second path (local or remote)",
    )
    extra_flags: str = Field(
        default="",
        validation_alias=AliasChoices("extra_flags", "ExtraFlags"),
        description="Additional flags passed to rclone during regular sync",
    )
    override_flags: str | None = Field(
        default=None,
        validation_alias=AliasChoices("override_flags", "OverrideFlags"),
        description="Flags that override global flags during regular sync",
    )
    extra_flags_init: str = Field(
        default="",
        validation_alias=AliasChoices("extra_flags_init", "ExtraFlagsInit"),
        description="Additional flags applied ONLY during automatic initial resync",
    )
    override_flags_init: str | None = Field(
        default=None,
        validation_alias=AliasChoices("override_flags_init", "OverrideFlagsInit"),
        description="Flags that override global flags ONLY during automatic initial resync",
    )
    allow_resync_non_empty: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "allow_resync_non_empty", "AllowResyncNonEmpty"
        ),
        description="Ignore mandatory condition that at least one path is empty for initial resync",
    )
    enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("enabled", "Enabled"),
        description="Whether this mapping is active",
    )

    @field_validator("alias", "path1", "path2")
    @classmethod
    def validate_non_empty(cls, v: str, info: Any) -> str:
        if not v or not v.strip():
            raise ValueError(f"'{info.field_name}' cannot be empty")
        return v.strip()

    @model_validator(mode="after")
    def validate_flag_conflicts(self) -> "MappingConfig":
        if self.override_flags is not None and self.extra_flags.strip():
            msg = (
                f"Mapping '{self.alias}': 'extra_flags' and 'override_flags' "
                "conflict and cannot be used together."
            )
            raise ValueError(msg)
        if self.override_flags_init is not None and self.extra_flags_init.strip():
            msg = (
                f"Mapping '{self.alias}': 'extra_flags_init' and 'override_flags_init' "
                "conflict and cannot be used together."
            )
            raise ValueError(msg)
        return self


class Config(BaseModel):
    """Global configuration for rc-sync."""

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )

    extra_flags: str = Field(
        default="--resilient --recover --max-lock %tm",
        validation_alias=AliasChoices("extra_flags", "ExtraFlags"),
        description="Global extra arguments passed to rclone (%t replaced by sync_freq_minutes)",
    )
    rclone_path: str = Field(
        default="rclone",
        validation_alias=AliasChoices("rclone_path", "RclonePath"),
        description="Command or executable path for rclone",
    )
    sync_freq_minutes: int = Field(
        default=5,
        ge=3,
        validation_alias=AliasChoices("sync_freq_minutes", "SyncFreqMinutes"),
        description="Sync frequency in minutes (must be >= 3)",
    )
    mappings: list[MappingConfig] = Field(
        default_factory=list,
        validation_alias=AliasChoices("mappings", "Mappings"),
        description="List of sync mappings",
    )

    @field_validator("mappings")
    @classmethod
    def validate_unique_aliases(cls, mappings: list[MappingConfig]) -> list[MappingConfig]:
        seen = set()
        for m in mappings:
            if m.alias in seen:
                raise ValueError(f"Duplicate mapping alias found: '{m.alias}'")
            seen.add(m.alias)
        return mappings


DEFAULT_CONFIG_TEMPLATE = """# yaml-language-server: $schema=./schema.json

rclone_path: rclone
sync_freq_minutes: 5
extra_flags: "--resilient --recover --max-lock %tm"

mappings:
  - alias: docs
    path1: ~/Docs
    path2: remote:Docs
    enabled: true
    allow_resync_non_empty: false
"""


def load_config(path: Path) -> Config:
    """Load, parse and validate YAML configuration from path."""
    if not path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if data is None:
        data = {}
    elif not isinstance(data, dict):
        raise ValueError(f"Configuration file {path} must be a YAML mapping (dictionary)")

    return Config.model_validate(data)


def get_default_config_template(schema_uri: str | None = None) -> str:
    """Return default YAML configuration template."""
    if not schema_uri:
        installed_schema = get_installed_schema_path()
        if installed_schema:
            schema_uri = str(installed_schema)
        else:
            schema_uri = "./schema.json"

    return f"""# yaml-language-server: $schema={schema_uri}

rclone_path: rclone
sync_freq_minutes: 5
extra_flags: "--resilient --recover --max-lock %tm"

mappings:
  - alias: docs
    path1: ~/Docs
    path2: remote:Docs
    enabled: true
    allow_resync_non_empty: false
"""


def get_json_schema() -> dict[str, Any]:
    """Return JSON schema generated by Pydantic."""
    return Config.model_json_schema()


def write_schema(output_path: Path) -> None:
    """Write JSON schema to disk."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    schema = get_json_schema()
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2)


def write_template(target_path: Path) -> None:
    """Write default configuration template to target_path and schema.json next to it."""
    if target_path.exists():
        raise FileExistsError(f"Configuration file already exists: {target_path}")

    # Check parent directory permissions / creatability
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise PermissionError(f"Cannot create directory '{target_path.parent}': {e}") from e

    # Write config template
    try:
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(DEFAULT_CONFIG_TEMPLATE)
    except OSError as e:
        raise PermissionError(f"Cannot write configuration file '{target_path}': {e}") from e

    # Also write schema.json in the same directory
    schema_path = target_path.parent / "schema.json"
    write_schema(schema_path)
