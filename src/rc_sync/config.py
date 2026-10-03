"""Configuration data models, validation, and serialization."""

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
)

from rc_sync.paths import get_config_path, get_installed_schema_path


class MappingConfig(BaseModel):
    """Configuration for an individual sync mapping."""

    model_config = ConfigDict(
        extra="forbid",
    )

    path1: str = Field(
        ...,
        description="First path (local or remote)",
    )
    path2: str = Field(
        ...,
        description="Second path (local or remote)",
    )
    extra_flags: str = Field(
        default="",
        description="Additional flags passed to rclone during regular sync",
    )
    override_flags: bool = Field(
        default=False,
        description="Ignore global flags during regular sync",
    )
    extra_flags_init: str = Field(
        default="",
        description="Additional flags applied ONLY during automatic initial resync",
    )
    override_flags_init: bool = Field(
        default=False,
        description="Ignore global initial flags ONLY during automatic initial resync",
    )
    allow_init_non_empty: bool = Field(
        default=False,
        description="Ignore mandatory condition that at least one path is empty for initial resync",
    )
    enabled: bool = Field(
        default=True,
        description="Whether this mapping is active",
    )

    _alias: str = PrivateAttr(default="")

    def __init__(self, alias: str = "", **data: Any):
        super().__init__(**data)
        if alias:
            self._alias = str(alias).strip()

    @property
    def alias(self) -> str:
        return self._alias

    @alias.setter
    def alias(self, val: str) -> None:
        if not val or not str(val).strip():
            raise ValueError("Mapping alias cannot be empty")
        self._alias = str(val).strip()

    @field_validator("path1", "path2")
    @classmethod
    def validate_non_empty(cls, v: str, info: Any) -> str:
        if not v or not v.strip():
            raise ValueError(f"'{info.field_name}' cannot be empty")
        return v.strip()


class Config(BaseModel):
    """Global configuration for rc-sync."""

    model_config = ConfigDict(
        extra="forbid",
    )

    global_flags: str = Field(
        default="--resilient --recover --max-lock %tm",
        description="Global extra arguments passed to rclone (%t replaced by sync_freq_minutes)",
    )
    global_flags_init: str = Field(
        default="",
        description=(
            "Global initial arguments passed to rclone during initial sync "
            "(%t replaced by sync_freq_minutes)"
        ),
    )
    rclone_path: str = Field(
        default="rclone",
        description="Command or executable path for rclone",
    )
    sync_freq_minutes: int = Field(
        default=5,
        ge=3,
        description="Sync frequency in minutes (must be >= 3)",
    )
    mappings: dict[str, MappingConfig] = Field(
        default_factory=dict,
        description="Sync mappings keyed by alias",
    )


    @field_validator("mappings")
    @classmethod
    def validate_mapping_keys(cls, mappings: dict[str, MappingConfig]) -> dict[str, MappingConfig]:
        for k in mappings:
            if not k or not str(k).strip():
                raise ValueError("Mapping alias cannot be empty")
        return mappings

    @model_validator(mode="after")
    def set_mapping_aliases(self) -> "Config":
        for alias, mapping in self.mappings.items():
            if mapping.alias and mapping.alias != alias:
                raise ValueError(
                    f"Mapping key '{alias}' does not match internal alias '{mapping.alias}'"
                )
            mapping.alias = alias
        return self


DEFAULT_CONFIG_TEMPLATE = """# yaml-language-server: $schema=./schema.json

rclone_path: rclone
sync_freq_minutes: 5
global_flags: "--resilient --recover --max-lock %tm"

mappings:
  docs:
    path1: ~/Docs
    path2: remote:Docs
    enabled: true
    allow_init_non_empty: false
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
global_flags: "--resilient --recover --max-lock %tm"

mappings:
  docs:
    path1: ~/Docs
    path2: remote:Docs
    enabled: true
    allow_init_non_empty: false
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


def ensure_schema(preferred_dir: Path | None = None) -> tuple[Path, bool]:
    """Find installed global schema or generate one in preferred_dir (or config dir).

    Returns (schema_path, generated) where generated is True if a new schema was written to disk.
    """
    installed = get_installed_schema_path()
    if installed:
        return installed, False

    if preferred_dir is None:
        preferred_dir = get_config_path().parent

    schema_path = preferred_dir / "schema.json"
    write_schema(schema_path)
    return schema_path, True


def write_template(target_path: Path) -> Path | None:
    """Write default configuration template to target_path.

    Uses installed global schema if found; otherwise generates schema.json next to target_path.
    Returns path to generated schema if generated, or None if global schema was used.
    """
    if target_path.exists():
        raise FileExistsError(f"Configuration file already exists: {target_path}")

    # Check parent directory permissions / creatability
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise PermissionError(f"Cannot create directory '{target_path.parent}': {e}") from e

    schema_path, generated = ensure_schema(preferred_dir=target_path.parent)
    if generated or schema_path.parent == target_path.parent:
        schema_uri = "./schema.json"
    else:
        schema_uri = str(schema_path)
    template_content = get_default_config_template(schema_uri=schema_uri)

    # Write config template
    try:
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(template_content)
    except OSError as e:
        raise PermissionError(f"Cannot write configuration file '{target_path}': {e}") from e

    return schema_path if generated else None
