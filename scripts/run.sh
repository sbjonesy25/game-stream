#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export QT_QPA_PLATFORM=xcb
exec .venv/bin/python -m streamwall.app "$@"
