#!/usr/bin/env python3
"""Build the small Ubuntu amd64 installer without sudo or maintainer-time pip."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEPENDS = [
    'python3 (>= 3.10)', 'python3 (<< 3.16)', 'python3-venv', 'ca-certificates', 'zenity',
    'vlc', 'libvlc5', 'libxcb-cursor0', 'libxcb-xinerama0', 'libxkbcommon-x11-0',
    'libxcb-icccm4', 'libxcb-keysyms1', 'libx11-xcb1', 'libgl1', 'libegl1',
    'libnss3', 'libnspr4', 'libatk1.0-0t64 | libatk1.0-0',
    'libatk-bridge2.0-0t64 | libatk-bridge2.0-0', 'libatspi2.0-0t64 | libatspi2.0-0',
    'libcups2t64 | libcups2', 'libasound2t64 | libasound2',
    'libglib2.0-0t64 | libglib2.0-0', 'libdbus-1-3', 'libdrm2', 'libgbm1',
    'libcairo2', 'libpango-1.0-0', 'libxcomposite1', 'libxdamage1', 'libxrandr2',
    'libxfixes3', 'fonts-liberation', 'fonts-noto-color-emoji',
]


def text(path, value, executable=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding='utf-8')
    path.chmod(0o755 if executable else 0o644)


def build(wheel, output):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    version = re.search(r'^version = "([^"]+)"', (ROOT / 'pyproject.toml').read_text(), re.M)[1]
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='game-stream-deb-') as temporary:
        stage = Path(temporary) / 'root'
        app = stage / 'usr/share/game-stream'
        app.mkdir(parents=True)
        stage.chmod(0o755)
        shutil.copy2(wheel, app / wheel.name)
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        text(app / 'app.json', json.dumps(dict(version=version, wheel=wheel.name, sha256=digest)))
        shutil.copy2(ROOT / 'game_stream/bootstrap.py', app / 'bootstrap.py')
        shutil.copy2(ROOT / 'game_stream/uninstall.py', app / 'uninstall.py')
        for name in ('game-stream', 'game-stream-uninstall'):
            module = 'bootstrap' if name == 'game-stream' else 'uninstall'
            text(stage / 'usr/bin' / name, f'#!/bin/sh\nexec /usr/bin/python3 /usr/share/game-stream/{module}.py "$@"\n', True)
        text(stage / 'usr/share/applications/game-stream.desktop', '''[Desktop Entry]
Version=1.0
Type=Application
Name=Game Stream
Comment=Watch up to four sports streams
Exec=/usr/bin/game-stream
Icon=game-stream
StartupWMClass=game-stream
StartupNotify=true
Terminal=false
Categories=AudioVideo;Video;Player;
Keywords=Sports;Streaming;HLS;Game;
''')
        icon = QImage(str(ROOT / 'game_stream/assets/game-stream.png'))
        if icon.isNull():
            raise RuntimeError('Icon missing')
        for size in (16, 24, 32, 48, 64, 128, 256, 512):
            path = stage / f'usr/share/icons/hicolor/{size}x{size}/apps/game-stream.png'
            path.parent.mkdir(parents=True)
            if not icon.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation).save(str(path), 'PNG'):
                raise RuntimeError('Icon could not be saved')
        doc = stage / 'usr/share/doc/game-stream'
        doc.mkdir(parents=True)
        shutil.copy2(ROOT / 'README.md', doc / 'README.md')
        shutil.copy2(ROOT / 'packaging/README.Debian', doc / 'README.Debian')
        size = sum(p.stat().st_size for p in stage.rglob('*') if p.is_file()) // 1024 + 1
        control = f'''Package: game-stream
Version: {version}
Section: video
Priority: optional
Architecture: amd64
Maintainer: Game Stream project <sbjonesy25@users.noreply.github.com>
Installed-Size: {size}
Depends: {', '.join(DEPENDS)}
Homepage: https://github.com/sbjonesy25/game-stream
Description: Four-pane sports stream viewer with a game catalog
 Displays one to four streams with per-pane audio, fullscreen shortcuts,
 session recovery, sports menus and a configurable source root.
 First launch downloads a private Python and Chromium runtime as the
 current user. Internet access and free disk space are required.
'''
        text(stage / 'DEBIAN/control', control)
        cache_script = '''#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true
fi
exit 0
'''
        for script in ('postinst', 'postrm'):
            text(stage / 'DEBIAN' / script, cache_script, True)
        # Package files must be readable by users, independently of build umask.
        for path in stage.rglob('*'):
            if path.is_dir():
                path.chmod(0o755)
            elif path.parent.name != 'DEBIAN' and path.parent != stage / 'usr/bin':
                path.chmod(0o644)
        if shutil.which('desktop-file-validate'):
            subprocess.run(['desktop-file-validate', str(stage / 'usr/share/applications/game-stream.desktop')], check=True)
        deb = output / f'game-stream_{version}_amd64.deb'
        subprocess.run(['dpkg-deb', '--root-owner-group', '-Zxz', '--build', str(stage), str(deb)], check=True)
        sha = hashlib.sha256(deb.read_bytes()).hexdigest()
        text(output / 'SHA256SUMS', f'{sha}  {deb.name}\n')
        return deb


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', type=Path, help='Use an existing application wheel')
    parser.add_argument('--output', type=Path, default=ROOT / 'releases')
    args = parser.parse_args()
    if not shutil.which('dpkg-deb'):
        parser.error('Install dpkg-dev / dpkg-deb before building')
    if args.wheel:
        print(build(args.wheel.resolve(), args.output.resolve()))
    else:
        with tempfile.TemporaryDirectory(prefix='game-stream-wheel-') as temp:
            subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps', str(ROOT), '--wheel-dir', temp], check=True)
            print(build(next(Path(temp).glob('game_stream-*.whl')), args.output.resolve()))


if __name__ == '__main__':
    main()
