#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 game_stream/uninstall.py "$@"
