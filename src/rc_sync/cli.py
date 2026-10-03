"""Command-line interface entry points for rc-sync implemented with Typer."""

import json
import os
import shutil
import sys
from collections.abc import Sequence
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer
import yaml

from rc_sync.config import (
    Config,
    ensure_schema,
    get_default_config_template,
    get_json_schema,
    load_config,
    write_schema,
    write_template,
)
from rc_sync.daemon import DaemonManager
from rc_sync.logger import get_logger, setup_logger
from rc_sync.paths import get_config_path, get_installed_schema_path
from rc_sync.state import StateManager
from rc_sync.sync_engine import SyncEngine


def complete_alias(incomplete: str) -> list[str]:
    """Auto-complete mapping aliases from config for shell."""
    try:
        config_path = get_config_path()
        if not config_path.is_file():
            return [a for a in ["all"] if a.startswith(incomplete)]
        cfg = load_config(config_path)
        aliases = ["all"] + list(cfg.mappings.keys())
        return [a for a in aliases if a.startswith(incomplete)]
    except Exception:
        return [a for a in ["all"] if a.startswith(incomplete)]


app = typer.Typer(
    name="rc-sync",
    help="rclone bisync wrapper for synchronization and daemon management",
    no_args_is_help=True,
    add_completion=True,
)

daemon_app = typer.Typer(
    name="daemon",
    help="Manage systemd daemon and view logs",
    no_args_is_help=True,
)
app.add_typer(daemon_app, name="daemon")

config_app = typer.Typer(
    name="config",
    help="Manage and inspect configuration",
    no_args_is_help=True,
)
template_app = typer.Typer(
    name="template",
    help="Configuration template operations",
    no_args_is_help=True,
)
schema_app = typer.Typer(
    name="schema",
    help="JSON Schema operations",
    no_args_is_help=True,
)

config_app.add_typer(template_app, name="template")
config_app.add_typer(schema_app, name="schema")
app.add_typer(config_app, name="config")


def _get_config_and_state(context: str) -> tuple[Config, StateManager]:
    """Helper to load config or return empty config on missing file."""
    config_path = get_config_path()
    state_manager = StateManager()

    if config_path.is_file():
        try:
            config = load_config(config_path)
        except Exception as e:
            get_logger().error(f"Configuration error: {e}", extra={"context": context})
            raise typer.Exit(code=1) from e
    else:
        config = Config()

    return config, state_manager


# 1. sync (<alias>+|all) [--override-flags] [<extra-flags>]
@app.command(
    "sync",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    help="Run synchronization for specified mappings or all active mappings",
    epilog=(
        "Trailing arguments (e.g. '--resync', '--dry-run', '-v') are forwarded to 'rclone bisync'."
    ),
)
def sync_cmd(
    ctx: typer.Context,
    targets: Annotated[
        list[str],
        typer.Argument(
            metavar="TARGETS... [ARGS]...",
            help=(
                "One or more mapping aliases or 'all'. "
                "Trailing arguments are forwarded to rclone bisync"
            ),
            autocompletion=complete_alias,
        ),
    ],
    override_flags: Annotated[
        bool,
        typer.Option(
            "--override-flags",
            help="Override all global and mapping flags using only CLI extra flags",
        ),
    ] = False,
) -> None:
    logger = get_logger()
    config_path = get_config_path()

    if not config_path.is_file():
        logger.error(
            f"Configuration file not found: {config_path}. "
            "Generate one with 'rc-sync config template gen'.",
            extra={"context": "sync"},
        )
        raise typer.Exit(code=1)

    try:
        config = load_config(config_path)
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}", extra={"context": "sync"})
        raise typer.Exit(code=1) from e

    state_manager = StateManager()

    # Automatically synchronize systemd timer frequency if timer unit exists
    dm = DaemonManager(config, state_manager)
    dm.sync_timer_frequency()

    clean_targets: list[str] = []
    extra_flags_parts: list[str] = []

    for i, arg in enumerate(targets):
        if arg.startswith("-"):
            extra_flags_parts.extend(targets[i:])
            break
        if clean_targets and clean_targets[0].lower() == "all":
            extra_flags_parts.extend(targets[i:])
            break
        clean_targets.append(arg)

    # Include any trailing arguments / unknown options collected by Typer
    if ctx.args:
        extra_flags_parts.extend(ctx.args)

    if not clean_targets:
        logger.error(
            "No sync targets specified. Provide mapping aliases or 'all'.",
            extra={"context": "sync"},
        )
        raise typer.Exit(code=2)

    final_extra_flags = " ".join(part.strip() for part in extra_flags_parts if part.strip())

    engine = SyncEngine(config=config, state_manager=state_manager)
    exit_code = engine.sync(
        target_aliases=clean_targets,
        cli_override_flags=override_flags,
        cli_extra_flags=final_extra_flags,
    )
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


# 2. status
@app.command(
    "status",
    help="Display daemon status and current status of all mappings",
)
def status_cmd() -> None:
    config_path = get_config_path()
    state_manager = StateManager()

    if config_path.is_file():
        try:
            config = load_config(config_path)
        except Exception as e:
            sys.stderr.write(f"Error loading configuration: {e}\n")
            raise typer.Exit(code=1) from e
    else:
        config = Config()

    dm = DaemonManager(config, state_manager)
    dm.sync_timer_frequency()
    print(dm.format_status())


class UnitType(str, Enum):
    service = "service"
    timer = "timer"


# 3. daemon (print|up|install|enable|disable|remove|logs|status)
@daemon_app.command("print", help="Print rendered systemd unit to stdout")
def daemon_print(
    unit: Annotated[
        UnitType,
        typer.Argument(
            case_sensitive=False,
            help="Unit type to print: 'service' or 'timer'",
        ),
    ],
    exec_path: Annotated[
        str | None,
        typer.Option(
            "--exec-path",
            help="Explicit path to rc-sync binary for ExecStart (service unit only)",
        ),
    ] = None,
) -> None:
    config, state_manager = _get_config_and_state("daemon")
    dm = DaemonManager(config, state_manager)
    if unit == UnitType.service:
        print(dm.render_service_unit(exec_path=exec_path), end="")
    elif unit == UnitType.timer:
        print(dm.render_timer_unit(), end="")


@daemon_app.command("up", help="Generate units, enable timer, and run initial sync via systemd")
def daemon_up(
    exec_path: Annotated[
        str | None,
        typer.Option(
            "--exec-path",
            help="Explicit path to rc-sync binary for ExecStart",
        ),
    ] = None,
) -> None:
    config, state_manager = _get_config_and_state("daemon")
    dm = DaemonManager(config, state_manager)
    code = dm.up(exec_path=exec_path)
    if code != 0:
        raise typer.Exit(code=code)


@daemon_app.command("install", help="Generate units and reload systemd user daemon")
def daemon_install(
    exec_path: Annotated[
        str | None,
        typer.Option(
            "--exec-path",
            help="Explicit path to rc-sync binary for ExecStart",
        ),
    ] = None,
) -> None:
    config, state_manager = _get_config_and_state("daemon")
    dm = DaemonManager(config, state_manager)
    code = dm.install(exec_path=exec_path)
    if code != 0:
        raise typer.Exit(code=code)


@daemon_app.command("remove", help="Stop, disable, and remove systemd user units")
def daemon_remove() -> None:
    config, state_manager = _get_config_and_state("daemon")
    dm = DaemonManager(config, state_manager)
    code = dm.remove()
    if code != 0:
        raise typer.Exit(code=code)


@daemon_app.command("enable", help="Generate units and enable timer in systemd")
def daemon_enable(
    exec_path: Annotated[
        str | None,
        typer.Option(
            "--exec-path",
            help="Explicit path to rc-sync binary for ExecStart",
        ),
    ] = None,
) -> None:
    config, state_manager = _get_config_and_state("daemon")
    dm = DaemonManager(config, state_manager)
    code = dm.enable(exec_path=exec_path)
    if code != 0:
        raise typer.Exit(code=code)


@daemon_app.command("disable", help="Disable and stop timer in systemd")
def daemon_disable() -> None:
    config, state_manager = _get_config_and_state("daemon")
    dm = DaemonManager(config, state_manager)
    code = dm.disable()
    if code != 0:
        raise typer.Exit(code=code)


@daemon_app.command("status", help="Show systemd status")
def daemon_status() -> None:
    config, state_manager = _get_config_and_state("daemon")
    dm = DaemonManager(config, state_manager)
    print(dm.format_status())


@daemon_app.command("logs", help="View daemon logs via journalctl")
def daemon_logs(
    follow: Annotated[
        bool,
        typer.Option("-f", "--follow", help="Follow log stream"),
    ] = False,
) -> None:
    config, state_manager = _get_config_and_state("daemon")
    dm = DaemonManager(config, state_manager)
    code = dm.logs(follow=follow)
    if code != 0:
        raise typer.Exit(code=code)


# 4. config (validate|show|template|schema)
@config_app.command("validate", help="Validate YAML configuration structure")
def config_validate(
    config_path: Annotated[
        Path | None,
        typer.Argument(
            help="Path to configuration file to validate (default: active config path)",
        ),
    ] = None,
) -> None:
    target = config_path.expanduser().resolve() if config_path else get_config_path()
    try:
        load_config(target)
        print(f"Configuration is valid: {target}")
    except Exception as e:
        sys.stderr.write(f"Configuration validation failed: {e}\n")
        raise typer.Exit(code=1) from e


@config_app.command("show", help="Output parsed configuration with all defaults applied")
def config_show() -> None:
    target = get_config_path()
    try:
        cfg = load_config(target)
        data = cfg.model_dump(by_alias=False)
        print(yaml.dump(data, sort_keys=False))
    except Exception as e:
        sys.stderr.write(f"Failed to show configuration: {e}\n")
        raise typer.Exit(code=1) from e


@template_app.command("print", help="Print default configuration template to stdout")
def template_print() -> None:
    try:
        schema_path, generated = ensure_schema()
        schema_uri = "./schema.json" if generated else str(schema_path)
        content = get_default_config_template(schema_uri=schema_uri)
    except Exception:
        content = get_default_config_template()
    print(content, end="")


@template_app.command(
    "cat",
    help="Print default configuration template to stdout (alias for print)",
    hidden=True,
)
def template_cat() -> None:
    template_print()


@template_app.command("gen", help="Write default template and schema.json to configuration path")
def template_gen() -> None:
    target = get_config_path()
    try:
        generated_schema = write_template(target)
        print(f"Generated default configuration at: {target}")
        if generated_schema:
            print(f"Generated JSON Schema at: {generated_schema}")
        else:
            installed = get_installed_schema_path()
            print(f"Using installed JSON Schema at: {installed}")
    except Exception as e:
        sys.stderr.write(f"Failed to generate template: {e}\n")
        raise typer.Exit(code=1) from e


@schema_app.command("print", help="Print JSON Schema to stdout")
def schema_print() -> None:
    print(json.dumps(get_json_schema(), indent=2))


@schema_app.command("cat", help="Print JSON Schema to stdout (alias for print)", hidden=True)
def schema_cat() -> None:
    schema_print()


@schema_app.command("gen", help="Write JSON Schema to disk")
def schema_gen(
    output_path: Annotated[
        Path | None,
        typer.Argument(
            help="Output path for schema.json (default: next to config file)",
        ),
    ] = None,
) -> None:
    target = (
        output_path.expanduser().resolve()
        if output_path
        else get_config_path().parent / "schema.json"
    )
    try:
        write_schema(target)
        print(f"Generated JSON Schema at: {target}")
    except Exception as e:
        sys.stderr.write(f"Failed to generate schema: {e}\n")
        raise typer.Exit(code=1) from e


# 5. rclone (direct pass-through to rclone)
def handle_rclone(args: Sequence[str]) -> int:
    """Pass command and arguments directly to rclone."""
    config_path = get_config_path()
    rclone_cmd = "rclone"
    if config_path.is_file():
        try:
            cfg = load_config(config_path)
            rclone_cmd = cfg.rclone_path
        except Exception:
            pass

    bin_path = shutil.which(rclone_cmd)
    if not bin_path:
        sys.stderr.write(f"Error: rclone executable '{rclone_cmd}' not found in PATH.\n")
        return 1

    sys.stdout.flush()
    sys.stderr.flush()

    cmd_args = [bin_path] + list(args)
    try:
        os.execvp(bin_path, cmd_args)
    except Exception as e:
        sys.stderr.write(f"Failed to execute rclone: {e}\n")
        return 1
    return 0


@app.command(
    "rclone",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    help="Pass commands and arguments directly to rclone (e.g. 'rc-sync rclone config')",
)
def rclone_cmd(ctx: typer.Context) -> None:
    exit_code = handle_rclone(ctx.args)
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


def main(argv: Sequence[str] | None = None) -> int:
    """Main CLI entry point."""
    setup_logger()

    if argv is None:
        argv = sys.argv[1:]

    # Direct pass-through for 'rclone' to preserve raw flags without typer parsing
    if argv and argv[0] == "rclone":
        return handle_rclone(argv[1:])

    try:
        ret = app(args=list(argv), standalone_mode=False)
        return ret if isinstance(ret, int) else 0
    except typer.Exit as e:
        return e.exit_code
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
