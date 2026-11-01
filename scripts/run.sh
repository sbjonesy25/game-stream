#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export QT_QPA_PLATFORM=xcb
export RESOURCE_NAME=game-stream
exec .venv/bin/python -m game_stream.app "$@"
