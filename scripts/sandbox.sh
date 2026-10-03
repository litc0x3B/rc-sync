#!/usr/bin/env bash
set -euo pipefail

# Create a temporary sandbox directory
SANDBOX_DIR="$(mktemp -d /tmp/rc-sync-sandbox-XXXXXX)"

cleanup() {
  echo ""
  echo "Cleaning up sandbox at $SANDBOX_DIR..."
  rm -rf "$SANDBOX_DIR"
  echo "Sandbox removed."
}
trap cleanup EXIT

# Setup isolated XDG directories
mkdir -p "$SANDBOX_DIR/config/rc-sync"
mkdir -p "$SANDBOX_DIR/state/rc-sync"
mkdir -p "$SANDBOX_DIR/run"
mkdir -p "$SANDBOX_DIR/data"
mkdir -p "$SANDBOX_DIR/cloud"
mkdir -p "$SANDBOX_DIR/local"

# Populate initial mock files
echo "Hello from mock cloud!" > "$SANDBOX_DIR/cloud/initial_remote.txt"

# Generate mock config
cat << EOF > "$SANDBOX_DIR/config/rc-sync/config.yaml"
# rc-sync sandbox configuration
sync_freq_minutes: 5
global_flags: "--resilient --recover --max-lock %tm"

mappings:
  test:
    path1: "$SANDBOX_DIR/cloud"
    path2: "$SANDBOX_DIR/local"
    enabled: true
    allow_init_non_empty: false
EOF

# Find rc-sync executable
RC_SYNC_BIN=""
if command -v rc-sync >/dev/null 2>&1; then
  RC_SYNC_BIN="$(command -v rc-sync)"
elif [ -x "./result/bin/rc-sync" ]; then
  RC_SYNC_BIN="$(pwd)/result/bin/rc-sync"
elif [ -x "./.venv/bin/rc-sync" ]; then
  RC_SYNC_BIN="$(pwd)/.venv/bin/rc-sync"
else
  echo "Error: Could not locate rc-sync executable."
  echo "Please run inside nix develop, build the package with 'nix build', or install in venv."
  exit 1
fi

export XDG_CONFIG_HOME="$SANDBOX_DIR/config"
export XDG_STATE_HOME="$SANDBOX_DIR/state"
export XDG_DATA_HOME="$SANDBOX_DIR/data"
export XDG_RUNTIME_DIR="$SANDBOX_DIR/run"
export RCLONE_CONFIG="$SANDBOX_DIR/rclone.conf"
BIN_DIR="$(dirname "$RC_SYNC_BIN")"
export PATH="$BIN_DIR:$PATH"

cat << EOF
======================================================================
  rc-sync Isolated Test Sandbox
======================================================================
Sandbox directory: $SANDBOX_DIR
Using binary:      $RC_SYNC_BIN

Isolated environment:
  XDG_CONFIG_HOME = $XDG_CONFIG_HOME
  XDG_STATE_HOME  = $XDG_STATE_HOME
  XDG_RUNTIME_DIR = $XDG_RUNTIME_DIR
  Config file     = $XDG_CONFIG_HOME/rc-sync/config.yaml

Pre-created test directories:
  cloud/ -> $SANDBOX_DIR/cloud (has 'initial_remote.txt')
  local/ -> $SANDBOX_DIR/local (empty, ready for initial sync)

Try these commands:
  1) rc-sync sync test          # Initial resync into local/
  2) rc-sync status             # Inspect mappings status
  3) echo "Hi" > local/new.txt  # Add a file in local
  4) rc-sync sync test          # Sync local changes back to cloud
  5) ls -la cloud/              # Verify new.txt arrived in cloud
  6) rc-sync config show        # View parsed configuration

Type 'exit' or press Ctrl+D when finished to destroy sandbox.
======================================================================
EOF

cat << 'EOF' > "$SANDBOX_DIR/.bashrc"
PS1='\[\033[01;32m\][rc-sync sandbox]\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\$ '
alias ll='ls -la'
EOF

cd "$SANDBOX_DIR"
bash --rcfile "$SANDBOX_DIR/.bashrc" -i
