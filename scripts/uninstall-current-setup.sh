#!/usr/bin/env bash
# Remove the old registration/console command before testing the .deb.
set -euo pipefail
project_root="$(cd "$(dirname "$0")/.." && pwd)"
"$project_root/scripts/uninstall.sh" "$@"
if [[ -x "$project_root/.venv/bin/python" && ! -L "$project_root/.venv" ]]; then
    "$project_root/.venv/bin/python" -I - "$project_root" "$@" <<'PY'
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import unquote, urlsplit
root = Path(sys.argv[1]).resolve()
for name in ('game-stream', 'streamwall'):
    try:
        dist = importlib.metadata.distribution(name)
        direct = json.loads(dist.read_text('direct_url.json') or '{}')
        parsed = urlsplit(direct.get('url', ''))
        if (parsed.scheme != 'file' or not direct.get('dir_info', {}).get('editable') or
                Path(unquote(parsed.path)).resolve() != root):
            continue
    except (importlib.metadata.PackageNotFoundError, ValueError, OSError):
        continue
    if '--dry-run' in sys.argv[2:]:
        print('Would remove this checkout\'s old console package: ' + name)
    else:
        subprocess.run([sys.executable, '-I', '-m', 'pip', 'uninstall', '-y', name], check=True)
print('Source checkout, virtual environment, dependencies and settings are preserved unless a data purge was requested.')
PY
fi
