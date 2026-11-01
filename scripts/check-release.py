#!/usr/bin/env python3
"""Fail if the latest installer/uninstaller is missing or differs from source."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def check():
    version = re.search(r'^version = "([^"]+)"', (ROOT / 'pyproject.toml').read_text(), re.M)[1]
    releases = ROOT / 'releases'
    deb = releases / f'game-stream_{version}_amd64.deb'
    bundle = releases / f'game-stream-uninstaller_{version}.tar.gz'
    script = releases / 'uninstall-game-stream.sh'
    hashes = dict(line.split('  ', 1)[::-1] for line in (releases / 'SHA256SUMS').read_text().splitlines() if line)
    latest = releases / 'latest.json'
    for path in (deb, bundle, script, latest):
        assert path.is_file(), f'Missing release artifact: {path.name}'
        assert hashlib.sha256(path.read_bytes()).hexdigest() == hashes.get(path.name), f'Checksum mismatch: {path.name}'
    release = json.loads(latest.read_text())
    assert release == dict(schema=1, version=version, filename=deb.name,
                           sha256=hashlib.sha256(deb.read_bytes()).hexdigest(), size=deb.stat().st_size), 'Update feed is stale'
    spec = importlib.util.spec_from_file_location('deb_builder', ROOT / 'scripts/build-deb.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    assert script.read_text() == builder.uninstaller_script(), 'Standalone uninstaller is stale'
    with tarfile.open(bundle) as archive:
        assert archive.extractfile('uninstall-game-stream.sh').read() == script.read_bytes(), 'Uninstaller bundle is stale'
    with tempfile.TemporaryDirectory(prefix='game-stream-release-check-') as temporary:
        root = Path(temporary)
        subprocess.run(['dpkg-deb', '--extract', str(deb), str(root)], check=True)
        app = root / 'usr/share/game-stream'
        manifest = json.loads((app / 'app.json').read_text())
        wheel = app / manifest['wheel']
        assert manifest['version'] == version
        assert hashlib.sha256(wheel.read_bytes()).hexdigest() == manifest['sha256']
        expected = {str(p.relative_to(ROOT)): p for p in (ROOT / 'game_stream').rglob('*')
                    if p.is_file() and p.suffix in ('.py', '.js', '.png')}
        with zipfile.ZipFile(wheel) as archive:
            packaged = {name for name in archive.namelist() if name.startswith('game_stream/') and name.endswith(('.py', '.js', '.png'))}
            assert packaged == set(expected), 'Packaged module list differs from source'
            for name, path in expected.items():
                assert archive.read(name) == path.read_bytes(), f'Installer contains stale source: {name}'
            assert not any(n.startswith('streamwall/') for n in archive.namelist())
        for name in ('bootstrap.py', 'uninstall.py'):
            assert (app / name).read_bytes() == (ROOT / 'game_stream' / name).read_bytes(), f'Stale {name}'
        assert (root / 'usr/share/doc/game-stream/README.md').read_bytes() == (ROOT / 'README.md').read_bytes(), 'Packaged README is stale'
    print(f'Release {version}: installer, uninstaller, source, icon and checksums match')


if __name__ == '__main__':
    check()
