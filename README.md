# rc-sync

Declarative CLI wrapper for `rclone bisync` that simplifies bidirectional folder synchronization between local and remote storage. It manages background scheduling via systemd user services and timers without manual scripting.

> [!WARNING]
> **Disclaimer**: This project is provided "as is", without warranty of any kind. It was developed with the assistance of AI / neural networks. Always verify configuration and backup critical data before enabling automated synchronization.

---

## Table of Contents

- [Features](#features)
- [Configuration Example](#configuration-example)
- [Basic Usage](#basic-usage)
- [Development & Testing](#development--testing)
  - [Standard Environment](#standard-environment)
  - [Nix Environment](#nix-environment)
  - [Updating Schema for Home Manager](#updating-schema-for-home-manager)
- [Installation in Nix](#installation-in-nix)
- [Home Manager Module](#home-manager-module)

---

## Features

- **Declarative synchronization**: Map local and remote paths using a clean YAML configuration without writing manual cron jobs or systemd service units.
- **Automated initial resync**: Runs the initial `rclone bisync --resync` automatically with zero user intervention when safety conditions are satisfied (at least one path is empty, or `allow_init_non_empty` is enabled).
- **Schema-backed YAML config**: Structured validation powered by Pydantic and an auto-generated JSON schema (`schema.json`) for editor completions and error checking.
- **Home Manager module**: First-class Nix module with pure evaluation, build-time Pydantic validation, and automatic systemd user service and timer generation.
- **Per-mapping flags**: Customize flags per target (filters, conflict resolution, global flag overrides, and `%t` sync-frequency variable interpolation).
- **Concurrency safe**: Global file lock (`flock`) prevents overlapping runs between the timer daemon and manual CLI invocations.
- **Live streaming logs**: Clear, unified console logging with formatted indentation for live rclone output.

---

## Configuration Example

By default, the configuration is stored at `~/.config/rc-sync/config.yaml` (overridable via the `RC_SYNC_CONFIG_PATH` environment variable).

```yaml
# yaml-language-server: $schema=./schema.json

rclone_path: rclone
sync_freq_minutes: 5
global_flags: "--resilient --recover --max-lock %tm"

mappings:
  # Mapping with file filter rules via extra_flags
  docs:
    path1: ~/Documents
    path2: remote:Documents
    enabled: true
    # Pass extra flags to regular rclone bisync runs (e.g. filter/exclude patterns)
    extra_flags: "--exclude '*.tmp' --exclude '.git/**'"

  # Mapping with conflict resolution for initial resync when both targets are non-empty
  photos:
    path1: ~/Pictures
    path2: gdrive:Pictures
    enabled: true
    # Allow initial resync even if both path1 and path2 already contain files
    allow_init_non_empty: true
    # Conflict resolution flags used ONLY during the first resync
    extra_flags_init: "--resync-mode newer"
```

> [!NOTE]
> `%t` in flag strings is automatically substituted with the value of `sync_freq_minutes`. Use `%%` to output a literal percent sign.

---

## Basic Usage

### 1. Typical Workflow

1. **Configure rclone remotes**:
   ```bash
   rclone config
   # or via the rc-sync pass-through command:
   rc-sync rclone config
   ```

2. **Generate configuration template and schema**:
   ```bash
   rc-sync config template gen
   ```
   This creates `~/.config/rc-sync/config.yaml` and `~/.config/rc-sync/schema.json`.

3. **Edit and validate your configuration**:
   Edit `~/.config/rc-sync/config.yaml` to specify your sync targets, then validate it:
   ```bash
   rc-sync config validate
   ```

4. **Install and start the background daemon**:
   ```bash
   rc-sync daemon up
   ```
   This command generates systemd user units (`rc-sync.service` and `rc-sync.timer`), enables and starts the timer, and immediately runs the initial sync in your terminal.

---

### 2. Manual Synchronization & Recovering from Errors

- **Sync all active mappings manually**:
  ```bash
  rc-sync sync all
  ```

- **Sync a specific mapping**:
  ```bash
  rc-sync sync docs
  ```

- **Manual resync after an error**:
  If a sync fails (for example, due to an unhandled conflict or path change), pass `--resync` to re-baseline the paths:
  ```bash
  rc-sync sync docs -- --resync
  ```
  You can also pass custom conflict resolution rules:
  ```bash
  rc-sync sync docs -- --resync --resync-mode newer
  ```

- **Force initial synchronization**:
  Force initial resync (applying configured `init_flags`) and bypass initial safety preconditions (e.g. requiring at least one directory to be empty):
  ```bash
  rc-sync sync docs --force-init
  ```

---

### 3. Monitoring & Daemon Management

```bash
# Check status of systemd timer/service and each sync mapping
rc-sync status

# Stream daemon logs in real time
rc-sync daemon logs -f

# Temporarily stop the timer and active synchronization
rc-sync daemon stop

# Start the timer (ensures units exist and reloads daemon if needed)
rc-sync daemon start

# Disable timer autostart (use --now to also stop it immediately)
rc-sync daemon disable
rc-sync daemon disable --now

# Enable timer autostart (use --now to also start it immediately)
rc-sync daemon enable
rc-sync daemon enable --now

# Remove systemd units completely
rc-sync daemon remove
```

---

### 4. State Management

`rc-sync` tracks synchronization progress in `~/.local/state/rc-sync/state.json`. You can inspect or manually reset state entries:

```bash
# View recorded states
rc-sync state show

# View raw state JSON
rc-sync state show --raw

# Reset state for a mapping by alias (sets to INIT_PENDING, forcing initial resync on next run)
rc-sync state reset docs

# Reset state for all mappings
rc-sync state reset all

# Reset state for specific paths
rc-sync state reset --path1 ~/Docs --path2 remote:Docs
# or using positional paths:
rc-sync state reset-paths ~/Docs remote:Docs

# Completely delete a mapping record from state.json
rc-sync state reset docs --remove
rc-sync state reset-paths ~/Docs remote:Docs --remove

# Clear all records from state.json
rc-sync state clear
```

---

## Development & Testing

### Standard Environment

Requires Python >= 3.10 and `rclone` in `PATH`.

```bash
# Set up a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies and development tools
pip install -e . pytest ruff mypy types-pyyaml

# Run test suite
pytest

# Run linter
ruff check .

# Run static type checking
mypy src
```

### Nix Environment

A preconfigured Nix development environment with all required packages (Python 3, rclone, pytest, ruff, mypy) is provided via `flake.nix`:

```bash
# Enter development shell
nix develop

# Inside the dev shell, run tests and checks:
pytest
ruff check .
mypy src
```

### Updating Schema for Home Manager

The Home Manager module reads `schema.json` directly from the repository root to evaluate Nix options in pure mode without IFD (*Import From Derivation*).

When modifying configuration models in `src/rc_sync/config.py`, regenerate the root schema file:

```bash
# Using rc-sync CLI
rc-sync config schema gen schema.json

# Or via Python module directly
python -m rc_sync.cli config schema gen schema.json
```

A pytest test (`tests/test_config.py::test_root_schema_json_sync`) automatically verifies that `schema.json` is kept in sync with the Pydantic models.

---

## Installation in Nix

### Using `nix profile`

Install the CLI directly from your local checkout or GitHub:

```bash
# From local directory
nix profile install .

# From GitHub repository
nix profile install github:litc0x3B/rc-sync
```

### In NixOS Configuration

Add the flake input and include the package in your system packages:

```nix
{
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    rc-sync.url = "github:litc0x3B/rc-sync";
  };

  outputs = { self, nixpkgs, rc-sync, ... }: {
    nixosConfigurations.myhostname = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        {
          environment.systemPackages = [
            rc-sync.packages.x86_64-linux.default
          ];
        }
      ];
    };
  };
}
```

---

## Home Manager Module

`rc-sync` includes a Home Manager module that manages configuration and systemd user services declaratively with build-time validation.

### 1. Add Flake Input

In your `flake.nix`:

```nix
{
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    home-manager.url = "github:nix-community/home-manager";
    rc-sync.url = "github:litc0x3B/rc-sync";
  };

  outputs = { self, nixpkgs, home-manager, rc-sync, ... }: {
    # ...
  };
}
```

### 2. Configure in Home Manager

In your Home Manager configuration (e.g. `home.nix`):

```nix
{ inputs, pkgs, ... }:

{
  imports = [
    inputs.rc-sync.homeManagerModules.default
  ];

  services.rc-sync = {
    enable = true;
    settings = {
      sync_freq_minutes = 15;
      global_flags = "--resilient --recover --max-lock %tm";

      mappings = {
        docs = {
          path1 = "~/Documents";
          path2 = "remote:Documents";
          extra_flags = "--exclude '*.tmp'";
        };
        photos = {
          path1 = "~/Pictures";
          path2 = "drive:Pictures";
          allow_init_non_empty = true;
          extra_flags_init = "--resync-mode newer";
        };
      };
    };
  };
}
```

### How the Module Works

1. **Pure Schema Evaluation**: Configuration options are generated dynamically from `schema.json` via `nix/schema-to-options.nix`, providing IDE completions and type descriptions.
2. **Build-Time Validation**: During `home-manager switch`, Nix runs `rc-sync config validate` on your configuration. If any constraint is violated (e.g., duplicate alias or `sync_freq_minutes < 3`), the build fails immediately before switching generations.
3. **Automatic Systemd Units**: `rc-sync.service` and `rc-sync.timer` are generated and managed automatically, keeping your timer schedule in sync with `sync_freq_minutes`.
