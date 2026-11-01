"""Install a per-user freedesktop launcher; no sudo or dock settings changes."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from .branding import APP_ID, DISPLAY_NAME, ICON_PATH

SIZES = (16, 24, 32, 48, 64, 128, 256, 512)


def desktop_value(value):
    return str(value).replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')


def exec_argument(value):
    value = str(value)
    if any(ord(c) < 32 for c in value):
        raise ValueError('Launcher paths cannot contain control characters')
    # Exec quoting is separate from desktop-entry string unescaping. No shell
    # ever interprets this command; percent signs must escape field-code parsing.
    value = value.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$').replace('`', '\\`')
    return '"' + desktop_value(value).replace('%', '%%') + '"'


def install_launcher(data_home=None, project=None, refresh=True):
    root = Path(project).resolve() if project else Path(__file__).resolve().parent.parent
    data = Path(data_home) if data_home else Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
    if not data.is_absolute():
        data = Path.home() / '.local/share'
    launcher = root / 'scripts/run.sh'
    # A wheel install has no checkout scripts. Preserve the venv interpreter path
    # (resolve() would follow its symlink outside the virtual environment).
    command = [str(launcher)] if launcher.is_file() else [os.path.abspath(sys.executable), '-m', 'game_stream.app']
    # GIO checks the first executable before expanding %% field escapes. A
    # stable env executable also permits checkouts with literal percent signs.
    executable = ' '.join(exec_argument(v) for v in ['/usr/bin/env', '--', *command])
    icon = QImage(str(ICON_PATH))
    if icon.isNull():
        raise OSError('Game Stream icon is missing')
    theme = data / 'icons/hicolor'
    for size in SIZES:
        target = theme / f'{size}x{size}/apps/{APP_ID}.png'
        target.parent.mkdir(parents=True, exist_ok=True)
        image = icon.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
        if not image.save(str(target), 'PNG'):
            raise OSError('Could not install Game Stream icon')
    target = data / 'applications' / f'{APP_ID}.desktop'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('\n'.join([
        '[Desktop Entry]', 'Version=1.0', 'Type=Application', f'Name={DISPLAY_NAME}',
        'Comment=Watch up to four sports streams', f'Exec={executable}',
        f'Path={desktop_value(root)}', f'Icon={APP_ID}', f'StartupWMClass={APP_ID}',
        'StartupNotify=true', 'Terminal=false', 'Categories=AudioVideo;Video;Player;',
        'Keywords=Sports;Streaming;HLS;Game;', '',
    ]), encoding='utf-8')
    target.chmod(0o644)
    if refresh:
        for program, args in [('update-desktop-database', [str(target.parent)]),
                              ('gtk-update-icon-cache', ['-q', '-t', str(theme)])]:
            executable = shutil.which(program)
            if executable:
                try:
                    subprocess.run([executable, *args], capture_output=True, timeout=15, check=False)
                except (OSError, subprocess.TimeoutExpired):
                    pass  # Shells can refresh the icon/database on the next login.
    return target


def main():
    try:
        install_launcher()
    except (OSError, ValueError):
        print('Could not install the Game Stream launcher. Check your user applications directory permissions.', file=sys.stderr)
        return 1
    print('Game Stream launcher installed. Open Applications, launch Game Stream, then right-click its dock icon and choose Pin / Add to Favorites.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
