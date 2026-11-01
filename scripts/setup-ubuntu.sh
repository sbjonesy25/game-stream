#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python_bin="${PYTHON_BIN:-python3}"
"$python_bin" - <<'PY'
import sys
if not (3, 10) <= sys.version_info[:2] < (3, 16):
    sys.exit('Streamwall requires Python 3.10–3.15. Select one with PYTHON_BIN=python3.14 ./scripts/setup-ubuntu.sh')
PY
if [[ -x .venv/bin/python ]]; then
    expected_version="$("$python_bin" -c 'import sys; print(sys.version_info[:2])')"
    actual_version="$(.venv/bin/python -c 'import sys; print(sys.version_info[:2])')"
    if [[ "$expected_version" != "$actual_version" ]]; then
        printf '%s\n' 'The existing .venv uses a different Python version. Rename .venv to a backup and rerun setup, or set PYTHON_BIN to its interpreter.' >&2
        exit 1
    fi
fi
sudo apt-get update
sudo apt-get install -y python3-venv vlc libvlc5 libxcb-cursor0 libxcb-xinerama0 libxkbcommon-x11-0
if [[ ! -x .venv/bin/python ]]; then
    "$python_bin" -m venv .venv
fi
.venv/bin/python --version
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m playwright --version
.venv/bin/python -m playwright install --with-deps chromium
printf '\nSetup complete. Run ./scripts/run.sh\n'
