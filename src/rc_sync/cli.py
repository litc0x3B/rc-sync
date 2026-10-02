"""Command-line interface entry points for rc-sync."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import yaml

from rc_sync.config import (
    get_default_config_template,
    get_json_schema,
    load_config,
    write_schema,
    write_template,
)
from rc_sync.daemon import DaemonManager
from rc_sync.logger import get_logger, setup_logger
from rc_sync.paths import get_config_path
from rc_sync.state import StateManager
from rc_sync.sync_engine import SyncEngine


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for rc-sync CLI."""
    parser = argparse.ArgumentParser(
        prog="rc-sync",
        description="rclone bisync wrapper for synchronization and daemon management",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # 1. sync (<alias>+|all) [--resync] [--override-flags] [<extra-flags>]
    sync_parser = subparsers.add_parser(
        "sync",
        help="Run synchronization for specified mappings or all active mappings",
    )
    sync_parser.add_argument(
        "targets",
        nargs="+",
        help="One or more mapping aliases or 'all'",
    )
    sync_parser.add_argument(
        "--resync",
        action="store_true",
        help="Force initial/resync mode in rclone bisync",
    )
    sync_parser.add_argument(
        "--override-flags",
        action="store_true",
        help="Override all global and mapping flags using only CLI extra flags",
    )
    sync_parser.add_argument(
        "--extra-flags",
        type=str,
        default="",
        help="Additional flags to pass to rclone",
    )

    # 2. status
    subparsers.add_parser(
        "status",
        help="Display daemon status and current status of all mappings",
    )

    # 3. daemon (up|enable|disable|logs|status)
    daemon_parser = subparsers.add_parser(
        "daemon",
        help="Manage systemd daemon and view logs",
    )
    daemon_sub = daemon_parser.add_subparsers(dest="daemon_action", help="Daemon actions")

    daemon_sub.add_parser("up", help="Generate units, enable timer, and run initial sync")
    daemon_sub.add_parser("enable", help="Generate units and enable timer in systemd")
    daemon_sub.add_parser("disable", help="Disable and stop timer in systemd")
    daemon_sub.add_parser("status", help="Show systemd status")

    logs_parser = daemon_sub.add_parser("logs", help="View daemon logs via journalctl")
    logs_parser.add_argument(
        "-f",
        "--follow",
        action="store_true",
        help="Follow log stream",
    )

    # 4. config (validate|show|template|schema)
    config_parser = subparsers.add_parser(
        "config",
        help="Manage and inspect configuration",
    )
    config_sub = config_parser.add_subparsers(dest="config_action", help="Config actions")

    val_parser = config_sub.add_parser(
        "validate", help="Validate YAML configuration structure"
    )
    val_parser.add_argument(
        "config_path",
        nargs="?",
        default=None,
        help="Path to configuration file to validate (default: active config path)",
    )

    config_sub.add_parser(
        "show", help="Output parsed configuration with all defaults applied"
    )

    # config template (cat|gen)
    tmpl_parser = config_sub.add_parser("template", help="Configuration template operations")
    tmpl_sub = tmpl_parser.add_subparsers(dest="template_action", help="Template actions")
    tmpl_sub.add_parser("cat", help="Print default configuration template to stdout")
    tmpl_sub.add_parser(
        "gen", help="Write default template and schema.json to configuration path"
    )

    # config schema (cat|gen)
    schema_parser = config_sub.add_parser("schema", help="Configuration JSON Schema operations")
    schema_sub = schema_parser.add_subparsers(dest="schema_action", help="Schema actions")
    schema_sub.add_parser("cat", help="Print JSON Schema to stdout")
    schema_gen = schema_sub.add_parser("gen", help="Write JSON Schema to disk")
    schema_gen.add_argument(
        "output_path",
        nargs="?",
        default=None,
        help="Output path for schema.json (default: next to config file)",
    )

    return parser


def handle_sync(args: argparse.Namespace, unknown_args: list[str]) -> int:
    """Handle sync command."""
    logger = get_logger()
    config_path = get_config_path()

    if not config_path.is_file():
        logger.error(
            f"Configuration file not found: {config_path}. "
            "Generate one with 'rc-sync config template gen'.",
            extra={"context": "sync"},
        )
        return 1

    try:
        config = load_config(config_path)
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}", extra={"context": "sync"})
        return 1

    state_manager = StateManager()

    # Process targets and extra flags
    raw_targets = list(args.targets)
    extra_flags_parts = [args.extra_flags] if args.extra_flags else []

    # If first target is 'all' and there are trailing arguments, they are extra flags
    if raw_targets and raw_targets[0].lower() == "all" and len(raw_targets) > 1:
        extra_flags_parts.extend(raw_targets[1:])
        raw_targets = ["all"]
    elif len(raw_targets) > 1 and raw_targets[-1].startswith("-"):
        extra_flags_parts.append(raw_targets.pop())

    # Include any unknown CLI arguments as extra flags
    if unknown_args:
        extra_flags_parts.extend(unknown_args)

    final_extra_flags = " ".join(part.strip() for part in extra_flags_parts if part.strip())

    engine = SyncEngine(config=config, state_manager=state_manager)
    return engine.sync(
        target_aliases=raw_targets,
        force_resync=args.resync,
        cli_override_flags=args.override_flags,
        cli_extra_flags=final_extra_flags,
    )


def handle_status(args: argparse.Namespace) -> int:
    """Handle status command."""
    config_path = get_config_path()
    state_manager = StateManager()

    if config_path.is_file():
        try:
            config = load_config(config_path)
        except Exception as e:
            sys.stderr.write(f"Error loading configuration: {e}\n")
            return 1
    else:
        # Create an empty config so we can at least show daemon status
        from rc_sync.config import Config
        config = Config()

    dm = DaemonManager(config, state_manager)
    print(dm.format_status())
    return 0


def handle_daemon(args: argparse.Namespace) -> int:
    """Handle daemon command."""
    logger = get_logger()
    config_path = get_config_path()

    if config_path.is_file():
        try:
            config = load_config(config_path)
        except Exception as e:
            logger.error(f"Configuration error: {e}", extra={"context": "daemon"})
            return 1
    else:
        from rc_sync.config import Config
        config = Config()

    state_manager = StateManager()
    dm = DaemonManager(config, state_manager)

    action = args.daemon_action
    if action == "up":
        # Enable daemon and immediately run sync all
        code = dm.enable()
        if code != 0:
            return code
        logger.info("Running initial synchronization...", extra={"context": "daemon"})
        engine = SyncEngine(config=config, state_manager=state_manager)
        return engine.sync(target_aliases=["all"])
    elif action == "enable":
        return dm.enable()
    elif action == "disable":
        return dm.disable()
    elif action == "logs":
        return dm.logs(follow=args.follow)
    elif action == "status":
        print(dm.format_status())
        return 0
    else:
        msg = "Missing or unknown daemon action. Use: up, enable, disable, logs, status\n"
        sys.stderr.write(msg)
        return 1


def handle_config(args: argparse.Namespace) -> int:
    """Handle config command."""
    action = args.config_action

    if action == "validate":
        if args.config_path:
            target = Path(args.config_path).expanduser().resolve()
        else:
            target = get_config_path()
        try:
            load_config(target)
            print(f"Configuration is valid: {target}")
            return 0
        except Exception as e:
            sys.stderr.write(f"Configuration validation failed: {e}\n")
            return 1

    elif action == "show":
        target = get_config_path()
        try:
            cfg = load_config(target)
            data = cfg.model_dump(by_alias=False)
            print(yaml.dump(data, sort_keys=False))
            return 0
        except Exception as e:
            sys.stderr.write(f"Failed to show configuration: {e}\n")
            return 1

    elif action == "template":
        tmpl_act = args.template_action
        if tmpl_act == "cat":
            print(get_default_config_template(), end="")
            return 0
        elif tmpl_act == "gen":
            target = get_config_path()
            try:
                write_template(target)
                print(f"Generated default configuration at: {target}")
                print(f"Generated JSON Schema at: {target.parent / 'schema.json'}")
                return 0
            except Exception as e:
                sys.stderr.write(f"Failed to generate template: {e}\n")
                return 1
        else:
            sys.stderr.write("Unknown template action. Use 'cat' or 'gen'.\n")
            return 1

    elif action == "schema":
        schema_act = args.schema_action
        if schema_act == "cat":
            print(json.dumps(get_json_schema(), indent=2))
            return 0
        elif schema_act == "gen":
            if args.output_path:
                target = Path(args.output_path).expanduser().resolve()
            else:
                target = get_config_path().parent / "schema.json"
            try:
                write_schema(target)
                print(f"Generated JSON Schema at: {target}")
                return 0
            except Exception as e:
                sys.stderr.write(f"Failed to generate schema: {e}\n")
                return 1
        else:
            sys.stderr.write("Unknown schema action. Use 'cat' or 'gen'.\n")
            return 1

    else:
        msg = "Missing or unknown config action. Use: validate, show, template, schema\n"
        sys.stderr.write(msg)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Main CLI entry point."""
    setup_logger()
    parser = build_parser()

    if argv is None:
        argv = sys.argv[1:]

    # Preprocess argv to handle '--extra-flags <value>' even when value starts with '-'
    processed_argv: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--extra-flags" and i + 1 < len(argv):
            processed_argv.append(f"--extra-flags={argv[i+1]}")
            i += 2
            continue
        processed_argv.append(arg)
        i += 1

    args, unknown_args = parser.parse_known_args(processed_argv)

    if not args.command:
        parser.print_help(sys.stderr)
        return 1

    if args.command == "sync":
        return handle_sync(args, unknown_args)
    elif args.command == "status":
        return handle_status(args)
    elif args.command == "daemon":
        return handle_daemon(args)
    elif args.command == "config":
        return handle_config(args)
    else:
        parser.print_help(sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
