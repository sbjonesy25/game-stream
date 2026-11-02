"""Remove owned launchers/runtime; preserve settings unless explicitly purged."""
import argparse
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

APP_ID = 'game-stream'


def xdg(variable, fallback):
    value = Path(os.environ.get(variable, Path.home() / fallback))
    return value if value.is_absolute() else Path.home() / fallback


def owned_runtime(path):
    try:
        return not path.is_symlink() and json.loads((path / '.game-stream-managed.json').read_text()).get('app') == APP_ID
    except (OSError, ValueError, AttributeError):
        return False


def user_files(purge=False):
    data = xdg('XDG_DATA_HOME', '.local/share')
    paths = [data / 'applications/game-stream.desktop']
    paths += [data / f'icons/hicolor/{s}x{s}/apps/game-stream.png' for s in (16, 24, 32, 48, 64, 128, 256, 512)]
    if purge:
        paths += [xdg('XDG_CONFIG_HOME', '.config') / APP_ID / 'config.json',
                  xdg('XDG_STATE_HOME', '.local/state') / APP_ID / 'session.json',
                  xdg('XDG_STATE_HOME', '.local/state') / APP_ID / 'setup.log',
                  xdg('XDG_STATE_HOME', '.local/state') / APP_ID / 'update.log',
                  xdg('XDG_CACHE_HOME', '.cache') / APP_ID / 'catalog.json']
    return paths


def purge_debug(dry_run=False):
    # Kept stdlib-only so the standalone uninstaller does not need the app runtime.
    root = xdg('XDG_STATE_HOME', '.local/state') / APP_ID / 'debug'
    def marker(path):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, encoding='utf-8') as stream:
            return json.loads(stream.read(65536))
    try:
        if root.is_symlink() or marker(root / '.game-stream-debug.json') != {'kind': 'game-stream-diagnostics-v1'}:
            return
    except (OSError, ValueError):
        return
    for directory in root.iterdir():
        if directory.is_symlink() or not directory.is_dir():
            continue
        try:
            info = marker(directory / '.game-stream-capture.json')
            if info.get('kind') != 'game-stream-diagnostics-v1':
                continue
            pid, ticks = info.get('monitor_pid'), info.get('monitor_start_ticks')
            if type(pid) is int and ticks is not None:
                try:
                    current = int(Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19])
                    if current == ticks:
                        print('Kept active debug capture: ' + str(directory))
                        continue
                except (OSError, ValueError, IndexError):
                    pass
        except (OSError, ValueError, AttributeError):
            continue
        print(('Would remove ' if dry_run else 'Removing ') + str(directory))
        if not dry_run:
            shutil.rmtree(directory)


def remove_user_install(purge=False, dry_run=False):
    if purge:
        purge_debug(dry_run)
    for path in user_files(purge):
        if path.exists() or path.is_symlink():
            print(('Would remove ' if dry_run else 'Removing ') + str(path))
            if not dry_run:
                path.unlink()
    runtime = xdg('XDG_DATA_HOME', '.local/share') / APP_ID
    if owned_runtime(runtime):
        print(('Would remove ' if dry_run else 'Removing ') + str(runtime))
        if not dry_run:
            shutil.rmtree(runtime)
    elif runtime.exists():
        print('Kept unrecognized data directory: ' + str(runtime))
    if not dry_run and shutil.which('gsettings'):
        try:
            result = subprocess.run(['gsettings', 'get', 'org.gnome.shell', 'favorite-apps'], capture_output=True, text=True, timeout=5)
            raw = result.stdout.strip()
            if result.returncode == 0 and raw.startswith('['):
                favorites = ast.literal_eval(raw)
                if isinstance(favorites, list) and 'game-stream.desktop' in favorites:
                    favorites = [f for f in favorites if f != 'game-stream.desktop']
                    subprocess.run(['gsettings', 'set', 'org.gnome.shell', 'favorite-apps', repr(favorites)], capture_output=True, timeout=5, check=False)
        except (OSError, ValueError, SyntaxError, subprocess.TimeoutExpired):
            pass


def package_installed():
    if not shutil.which('dpkg-query'):
        return False
    result = subprocess.run(['dpkg-query', '-W', '-f=${db:Status-Status}', APP_ID], capture_output=True, text=True, check=False)
    return result.returncode == 0 and result.stdout.strip() == 'installed'


def main():
    parser = argparse.ArgumentParser(description='Uninstall Game Stream. Close the app first. Settings are kept by default.')
    parser.add_argument('--purge-user-data', action='store_true', help='Also delete this user\'s preferences, session, game cache and inactive debug captures')
    parser.add_argument('--dry-run', action='store_true', help='Print removal actions without changing anything')
    args = parser.parse_args()
    try:
        remove_user_install(args.purge_user_data, args.dry_run)
        if package_installed():
            command = (['sudo'] if os.geteuid() else []) + ['apt-get', 'remove', '-y', APP_ID]
            if args.dry_run:
                print('Would run: ' + ' '.join(command))
            else:
                subprocess.run(command, check=True)
    except (OSError, subprocess.CalledProcessError):
        print('Uninstall did not finish. Check permissions and package manager output.', file=sys.stderr)
        return 1
    print('Game Stream uninstall complete.' if not args.dry_run else 'Dry run complete.')
    if not args.purge_user_data:
        print('Preferences, saved session, game cache and debug captures kept. Checkout and source .venv are never deleted.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
