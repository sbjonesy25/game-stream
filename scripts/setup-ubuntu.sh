#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
sudo apt-get update
sudo apt-get install -y python3-venv vlc libvlc5 libxcb-cursor0 libxcb-xinerama0 libxkbcommon-x11-0
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m playwright install --with-deps chromium
printf '\nSetup complete. Run ./scripts/run.sh\n'
