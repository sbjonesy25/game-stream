"""Check browser download selection without network or system package changes."""
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize('platform', ['ubuntu22.04-x64', 'ubuntu24.04-x64', 'ubuntu26.04-x64'])
def test_chromium_install_supports_ubuntu(platform):
    result = subprocess.run(
        [sys.executable, '-m', 'playwright', 'install', '--dry-run', 'chromium'],
        env={**os.environ, 'PLAYWRIGHT_HOST_PLATFORM_OVERRIDE': platform},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'chromium' in result.stdout.lower()
    assert 'Download url:' in result.stdout
