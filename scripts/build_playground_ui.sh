#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UI_DIR="$ROOT_DIR/penguiflow/cli/playground_ui"

cd "$UI_DIR"

if [ ! -f package-lock.json ]; then
  npm install
fi

npm run build

echo "Playground UI built into penguiflow/cli/playground_ui/dist"
