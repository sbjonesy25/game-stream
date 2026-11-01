"""System-package entry point: standard library only until the user runtime exists."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

APP_ID = 'game-stream'
MARKER = '.game-stream-managed.json'


def user_path(variable, fallback):
    path = Path(os.environ.get(variable, Path.home() / fallback))
    return path if path.is_absolute() else Path.home() / fallback


def runtime_root():
    return user_path('XDG_DATA_HOME', '.local/share') / APP_ID


def is_managed(root):
    try:
        return not root.is_symlink() and json.loads((root / MARKER).read_text()).get('app') == APP_ID
    except (OSError, ValueError, AttributeError):
        return False


def prepare_root(root):
    if root.exists() and not is_managed(root) and any(root.iterdir()):
        raise RuntimeError('The runtime directory contains unrecognized files; move it aside before setup.')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink():
        raise RuntimeError('The runtime directory must not be a symbolic link.')
    (root / MARKER).write_text(json.dumps({'app': APP_ID, 'schema': 1}))
    (root / MARKER).chmod(0o600)


def read_manifest(package):
    manifest = json.loads((package / 'app.json').read_text())
    wheel = package / manifest['wheel']
    if Path(manifest['wheel']).name != manifest['wheel'] or wheel.suffix != '.whl':
        raise RuntimeError('Invalid application package')
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != manifest['sha256']:
        raise RuntimeError('Application package checksum does not match. Reinstall Game Stream.')
    return manifest, wheel


def current_runtime(root, manifest):
    runtime = root / 'runtime'
    try:
        state = json.loads((runtime / 'runtime.json').read_text())
        if (state['sha256'] == manifest['sha256'] and state['python'] == list(sys.version_info[:2])
                and (runtime / 'bin/python').exists()):
            return runtime
    except (OSError, ValueError, KeyError):
        pass
    return None


def run_step(command, env, log, message, progress):
    if progress and progress.poll() is None:
        try:
            progress.stdin.write('# ' + message + '\n')
            progress.stdin.flush()
        except (OSError, BrokenPipeError):
            pass
    log.write(('\n' + message + '\n').encode())
    log.flush()
    subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)


def build_runtime(root, wheel, manifest, env, log, progress=None):
    stage = Path(tempfile.mkdtemp(prefix='runtime-build-', dir=root))
    try:
        run_step([sys.executable, '-m', 'venv', str(stage)], env, log, 'Preparing Game Stream…', progress)
        python = str(stage / 'bin/python')
        run_step([python, '-I', '-m', 'pip', 'install', '--upgrade', 'pip'], env, log, 'Preparing downloads…', progress)
        run_step([python, '-I', '-m', 'pip', 'install', str(wheel)], env, log, 'Installing Game Stream components…', progress)
        run_step([python, '-I', '-m', 'playwright', 'install', 'chromium'], env, log, 'Downloading the source browser…', progress)
        runtime = root / 'runtime'
        # Entrypoint shebangs must refer to the final location after promotion.
        for script in (stage / 'bin').iterdir():
            if script.is_file() and not script.is_symlink():
                raw = script.read_bytes()
                if raw.startswith(b'#!') and str(stage).encode() in raw.split(b'\n', 1)[0]:
                    script.write_bytes(raw.replace(str(stage).encode(), str(runtime).encode()))
        (stage / 'runtime.json').write_text(json.dumps(dict(sha256=manifest['sha256'], python=list(sys.version_info[:2]))))
        previous = root / 'runtime-previous'
        if previous.exists():
            shutil.rmtree(previous)
        if runtime.exists():
            runtime.rename(previous)
        try:
            stage.rename(runtime)
        except OSError:
            if previous.exists() and not runtime.exists():
                previous.rename(runtime)
            raise
        if previous.exists():
            shutil.rmtree(previous)
        return runtime
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def progress_dialog():
    if shutil.which('zenity') and (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        return subprocess.Popen(['zenity', '--progress', '--pulsate', '--auto-close', '--no-cancel',
            '--title=Game Stream first-run setup', '--width=480',
            '--text=Preparing Game Stream. First launch needs internet access and may take several minutes.'],
            stdin=subprocess.PIPE, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return None


def main(arguments=None, package=None):
    package = Path(package) if package else Path(__file__).resolve().parent
    if not (3, 10) <= sys.version_info[:2] < (3, 16):
        print('Game Stream requires system Python 3.10–3.15.', file=sys.stderr)
        return 1
    root = runtime_root()
    progress = None
    try:
        manifest, wheel = read_manifest(package)
        prepare_root(root)
        lock = (root / 'setup.lock').open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Game Stream setup is already running. Wait for it to finish.')
        env = dict(os.environ, QT_QPA_PLATFORM='xcb', RESOURCE_NAME=APP_ID,
                   PLAYWRIGHT_BROWSERS_PATH=str(root / 'browsers'),
                   GAME_STREAM_SYSTEM_PACKAGE=str(package))
        runtime = current_runtime(root, manifest)
        if runtime is None:
            log_path = user_path('XDG_STATE_HOME', '.local/state') / APP_ID / 'setup.log'
            log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            progress = progress_dialog()
            with os.fdopen(fd, 'wb') as log:
                runtime = build_runtime(root, wheel, manifest, env, log, progress)
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
        if progress:
            progress.terminate()
            progress.wait(timeout=5)
            progress = None
        python = str(runtime / 'bin/python')
        os.execve(python, [python, '-I', '-m', 'game_stream.app', *(arguments if arguments is not None else sys.argv[1:])], env)
    except Exception as exc:
        message = ('Game Stream setup could not finish. Check internet access and free disk space, then launch again.\n'
                   'Details: ' + str(user_path('XDG_STATE_HOME', '.local/state') / APP_ID / 'setup.log'))
        if isinstance(exc, RuntimeError):
            message = str(exc)
        print(message, file=sys.stderr)
        if progress:
            progress.terminate()
            progress.wait(timeout=5)
        if shutil.which('zenity') and (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
            subprocess.run(['zenity', '--error', '--title=Game Stream setup', '--text=' + message], check=False)
        return 1


if __name__ == '__main__':
    sys.exit(main())
