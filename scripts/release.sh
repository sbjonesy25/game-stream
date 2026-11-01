#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
./scripts/build-deb.sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
python3 scripts/check-release.py
