#!/usr/bin/env bash
set -euo pipefail

BUNDLED_NODE_DIR="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin"

if [ -x "$BUNDLED_NODE_DIR/node" ]; then
  export PATH="$BUNDLED_NODE_DIR:$PATH"
fi

exec "$@"
