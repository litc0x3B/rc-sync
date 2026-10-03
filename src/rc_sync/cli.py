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
from rc_sync.lock import ProcessLock
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

state_app = typer.Typer(
    name="state",
    help="Inspect and manage synchronization state",
    no_args_is_help=True,
)
app.add_typer(state_app, name="state")

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


# 1. sync (<alias>+|all) [--force-init] [--override-flags] [<extra-flags>]
@app.command(
    "sync",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    help="Run synchronization for specified mappings or all active mappings",
    epilog=(
        "Trailing arguments (e.g. '--dry-run', '-v') are forwarded to 'rclone bisync'."
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
    force_init: Annotated[
        bool,
        typer.Option(
            "--force-init",
            help="Force initial resync ignoring preconditions (e.g. non-empty paths)",
        ),
    ] = False,
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

    filtered_extra_flags: list[str] = []
    for part in extra_flags_parts:
        if part == "--force-init":
            force_init = True
        elif part == "--override-flags":
            override_flags = True
        else:
            filtered_extra_flags.append(part)

    if not clean_targets:
        logger.error(
            "No sync targets specified. Provide mapping aliases or 'all'.",
            extra={"context": "sync"},
        )
        raise typer.Exit(code=2)

    final_extra_flags = " ".join(part.strip() for part in filtered_extra_flags if part.strip())

    engine = SyncEngine(config=config, state_manager=state_manager)
    exit_code = engine.sync(
        target_aliases=clean_targets,
        force_init=force_init,
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


@daemon_app.command("enable", help="Enable timer for autostart in systemd")
def daemon_enable(
    now: Annotated[
        bool,
        typer.Option("--now", help="Start timer immediately in addition to enabling"),
    ] = False,
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
    code = dm.enable(exec_path=exec_path, now=now)
    if code != 0:
        raise typer.Exit(code=code)


@daemon_app.command("disable", help="Disable timer autostart in systemd")
def daemon_disable(
    now: Annotated[
        bool,
        typer.Option("--now", help="Stop timer immediately in addition to disabling"),
    ] = False,
) -> None:
    config, state_manager = _get_config_and_state("daemon")
    dm = DaemonManager(config, state_manager)
    code = dm.disable(now=now)
    if code != 0:
        raise typer.Exit(code=code)


@daemon_app.command(
    "start",
    help="Generate units if needed, reload daemon, and start timer in systemd",
)
def daemon_start(
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
    code = dm.start(exec_path=exec_path)
    if code != 0:
        raise typer.Exit(code=code)


@daemon_app.command("stop", help="Stop timer and active synchronization in systemd")
def daemon_stop() -> None:
    config, state_manager = _get_config_and_state("daemon")
    dm = DaemonManager(config, state_manager)
    code = dm.stop()
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


# 5. state (show|reset|reset-paths|clear)
@state_app.command("show", help="Show synchronization state records")
def state_show(
    raw: Annotated[bool, typer.Option("--raw", help="Output raw JSON state file")] = False,
) -> None:
    config, state_manager = _get_config_and_state(context="state")
    if raw:
        if state_manager.state_path.is_file():
            sys.stdout.write(state_manager.state_path.read_text(encoding="utf-8") + "\n")
        else:
            sys.stdout.write('{"mappings": []}\n')
        return

    path_to_alias: dict[tuple[str, str], str] = {}
    for m in config.mappings.values():
        path_to_alias[(m.path1, m.path2)] = m.alias
        path_to_alias[(m.path1.strip().rstrip("/"), m.path2.strip().rstrip("/"))] = m.alias

    states = state_manager.all_states()
    if not states:
        sys.stdout.write("No state records found.\n")
        return

    lines = ["=== Mappings State ==="]
    for s in states:
        alias = path_to_alias.get((s.path1, s.path2)) or path_to_alias.get(
            (s.path1.strip().rstrip("/"), s.path2.strip().rstrip("/"))
        )
        title = (
            f"- [{alias}] ({s.path1} <-> {s.path2})"
            if alias
            else f"- ({s.path1} <-> {s.path2})"
        )
        status_str = s.status.value if hasattr(s.status, "value") else str(s.status)
        lines.append(title)
        lines.append(f"    Status:       {status_str}")
        lines.append(f"    Init success: {'true' if s.init_success else 'false'}")
        lines.append(f"    Last sync:    {s.last_sync_time or 'null'}")

    sys.stdout.write("\n".join(lines) + "\n")


@state_app.command(
    "reset",
    help="Reset state for mappings by alias, all, or specific paths (--path1/--path2)",
)
def state_reset(
    targets: Annotated[
        list[str] | None,
        typer.Argument(
            help="Mapping aliases to reset, 'all', or two paths",
            autocompletion=complete_alias,
        ),
    ] = None,
    path1: Annotated[
        str | None,
        typer.Option("--path1", help="Path 1 of mapping to reset"),
    ] = None,
    path2: Annotated[
        str | None,
        typer.Option("--path2", help="Path 2 of mapping to reset"),
    ] = None,
    remove: Annotated[
        bool,
        typer.Option(
            "--remove",
            "--delete",
            help="Remove state record completely instead of resetting to INIT_PENDING",
        ),
    ] = False,
) -> None:
    config, state_manager = _get_config_and_state(context="state")
    logger = get_logger()

    if path1 or path2:
        if not path1 or not path2:
            logger.error("Both --path1 and --path2 must be provided.", extra={"context": "state"})
            raise typer.Exit(code=2)
        with ProcessLock():
            state_manager.load()
            if remove:
                removed = state_manager.remove_state(path1, path2)
                state_manager.save()
                if removed:
                    logger.info(
                        f"Removed state for paths '{path1}' <-> '{path2}'.",
                        extra={"context": "state"},
                    )
                else:
                    logger.info(
                        f"No existing state found for paths '{path1}' <-> '{path2}'.",
                        extra={"context": "state"},
                    )
            else:
                state_manager.reset_state(path1, path2)
                state_manager.save()
                logger.info(
                    f"Reset state for paths '{path1}' <-> '{path2}'.",
                    extra={"context": "state"},
                )
        return

    if not targets:
        logger.error(
            "No reset targets specified. Provide mapping aliases, 'all', or --path1 and --path2.",
            extra={"context": "state"},
        )
        raise typer.Exit(code=2)

    # Check if 2 targets were provided that are paths rather than aliases
    if (
        len(targets) == 2
        and any(t not in config.mappings for t in targets)
        and any("/" in t or ":" in t or "~" in t for t in targets)
    ):
        p1, p2 = targets[0], targets[1]
        with ProcessLock():
            state_manager.load()
            if remove:
                removed = state_manager.remove_state(p1, p2)
                state_manager.save()
                if removed:
                    logger.info(
                        f"Removed state for paths '{p1}' <-> '{p2}'.",
                        extra={"context": "state"},
                    )
                else:
                    logger.info(
                        f"No existing state found for paths '{p1}' <-> '{p2}'.",
                        extra={"context": "state"},
                    )
            else:
                state_manager.reset_state(p1, p2)
                state_manager.save()
                logger.info(
                    f"Reset state for paths '{p1}' <-> '{p2}'.",
                    extra={"context": "state"},
                )
        return

    if "all" in targets:
        with ProcessLock():
            state_manager.load()
            if remove:
                state_manager.clear_all()
                state_manager.save()
                logger.info("Removed all mapping states.", extra={"context": "state"})
            else:
                for m in config.mappings.values():
                    state_manager.reset_state(m.path1, m.path2)
                state_manager.reset_all()
                state_manager.save()
                logger.info("Reset state for all mappings.", extra={"context": "state"})
        return

    unknown = [a for a in targets if a not in config.mappings]
    if unknown:
        for a in unknown:
            logger.error(
                f"Mapping alias '{a}' not found in configuration.",
                extra={"context": "state"},
            )
        raise typer.Exit(code=1)

    with ProcessLock():
        state_manager.load()
        for a in targets:
            m = config.mappings[a]
            if remove:
                state_manager.remove_state(m.path1, m.path2)
                logger.info(
                    f"Removed state for mapping '{a}' ({m.path1} <-> {m.path2}).",
                    extra={"context": "state"},
                )
            else:
                state_manager.reset_state(m.path1, m.path2)
                logger.info(
                    f"Reset state for mapping '{a}' ({m.path1} <-> {m.path2}).",
                    extra={"context": "state"},
                )
        state_manager.save()


@state_app.command(
    "reset-paths",
    help="Reset state for a specific pair of paths (path1, path2)",
)
def state_reset_paths(
    path1: Annotated[str, typer.Argument(..., help="Path 1 of mapping")],
    path2: Annotated[str, typer.Argument(..., help="Path 2 of mapping")],
    remove: Annotated[
        bool,
        typer.Option(
            "--remove",
            "--delete",
            help="Remove state record completely instead of resetting to INIT_PENDING",
        ),
    ] = False,
) -> None:
    config, state_manager = _get_config_and_state(context="state")
    logger = get_logger()
    with ProcessLock():
        state_manager.load()
        if remove:
            removed = state_manager.remove_state(path1, path2)
            state_manager.save()
            if removed:
                logger.info(
                    f"Removed state for paths '{path1}' <-> '{path2}'.",
                    extra={"context": "state"},
                )
            else:
                logger.info(
                    f"No existing state found for paths '{path1}' <-> '{path2}'.",
                    extra={"context": "state"},
                )
        else:
            state_manager.reset_state(path1, path2)
            state_manager.save()
            logger.info(
                f"Reset state for paths '{path1}' <-> '{path2}'.",
                extra={"context": "state"},
            )


@state_app.command("clear", help="Clear all state entries from state.json")
def state_clear() -> None:
    config, state_manager = _get_config_and_state(context="state")
    logger = get_logger()
    with ProcessLock():
        state_manager.load()
        state_manager.clear_all()
        state_manager.save()
        logger.info("Cleared all state records.", extra={"context": "state"})


# 6. rclone (direct pass-through to rclone)
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
