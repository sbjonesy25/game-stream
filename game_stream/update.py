"""Repository package updates; the detached installer uses only the stdlib."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from time import monotonic, sleep

RELEASE_ROOT = 'https://raw.githubusercontent.com/sbjonesy25/game-stream/main/releases/'
REPOSITORY = 'sbjonesy25/game-stream'
MAX_PACKAGE = 200 * 1024 * 1024


class UpdateError(RuntimeError):
    pass


def record_failure(message):
    """Only actionable summaries, never GitHub credentials or response bodies."""
    state = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state'))
    if not state.is_absolute():
        state = Path.home() / '.local/state'
    try:
        path = state / 'game-stream/update.log'
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600), 'w') as log:
            log.write(message + '\n')
    except OSError:
        pass


def transfer_authenticated(filename, limit, cancel, consume):
    """Use the user's GitHub CLI login; never read/export/store its token."""
    if not shutil.which('gh', path='/usr/bin:/usr/local/bin'):
        raise UpdateError('GitHub requires repository access. If this repository is private, install GitHub CLI (sudo apt install gh), run gh auth login with an account that can read it, and retry. You can also install the .deb manually.')
    cli = shutil.which('gh', path='/usr/bin:/usr/local/bin')
    env = dict(clean_environment(), GH_PROMPT_DISABLED='1', GH_HOST='github.com')
    command = [cli, 'api', f'repos/{REPOSITORY}/contents/releases/{filename}?ref=main',
               '--hostname', 'github.com', '-H', 'Accept: application/vnd.github.raw+json']
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdout=output, stderr=errors, stdin=subprocess.DEVNULL, env=env)
        deadline = monotonic() + 180
        try:
            while process.poll() is None:
                if cancel.wait(.1):
                    raise UpdateError('Update cancelled')
                if os.fstat(output.fileno()).st_size > limit:
                    raise UpdateError('Update download exceeds its expected size')
                if monotonic() > deadline:
                    raise UpdateError('Authenticated GitHub download timed out. Check connectivity and retry.')
            if cancel.is_set():
                raise UpdateError('Update cancelled')
            if process.returncode:
                raise UpdateError('GitHub CLI could not read the update. Run gh auth login (or gh auth refresh for missing repository access), check that your account can read sbjonesy25/game-stream, then retry. Private-repository updates require access for each user.')
            output.seek(0)
            size = 0
            for block in iter(lambda: output.read(65536), b''):
                if cancel.is_set():
                    raise UpdateError('Update cancelled')
                size += len(block)
                if size > limit:
                    raise UpdateError('Update download exceeds its expected size')
                consume(block)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def version_tuple(version):
    if not isinstance(version, str) or not re.fullmatch(r'\d{1,6}\.\d{1,6}\.\d{1,6}', version):
        raise UpdateError('Invalid update version')
    return tuple(map(int, version.split('.')))


def validate_release(value):
    if not isinstance(value, dict) or value.get('schema') != 1:
        raise UpdateError('Unsupported update information')
    version_tuple(value.get('version'))
    if (value.get('filename') != f'game-stream_{value["version"]}_amd64.deb' or
            not isinstance(value.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', value['sha256']) or
            type(value.get('size')) is not int or not 1 <= value['size'] <= MAX_PACKAGE):
        raise UpdateError('Invalid update information')
    return {k: value[k] for k in ('schema', 'version', 'filename', 'sha256', 'size')}


def installed_version():
    # Source checkouts must never replace a different system installation.
    from .bootstrap import runtime_root
    if (os.environ.get('GAME_STREAM_SYSTEM_PACKAGE') != '/usr/share/game-stream' or
            Path(sys.prefix).resolve() != (runtime_root() / 'runtime').resolve()):
        raise UpdateError('This updater is for the Ubuntu .deb installation. Update a source checkout with git pull, or install the latest .deb first.')
    result = subprocess.run(['/usr/bin/dpkg-query', '-W', '-f=${db:Status-Status} ${Version} ${Architecture}', 'game-stream'],
                            capture_output=True, text=True, timeout=10)
    parts = result.stdout.strip().split()
    if result.returncode or len(parts) != 3 or parts[0] != 'installed' or parts[2] != 'amd64':
        raise UpdateError('An installed Ubuntu amd64 package is required for in-app updates.')
    version_tuple(parts[1])
    return parts[1]


def transfer(url, limit, cancel, consume):
    import requests
    if cancel.is_set():
        raise UpdateError('Update cancelled')
    try:
        with requests.get(url, timeout=(10, 20), stream=True, allow_redirects=False,
                          headers={'Cache-Control': 'no-cache', 'User-Agent': 'Game-Stream-Updater'}) as response:
            if response.status_code in (401, 403, 404):
                try:
                    transfer_authenticated(url.removeprefix(RELEASE_ROOT), limit, cancel, consume)
                    return
                except UpdateError as exc:
                    raise UpdateError(f'GitHub HTTP {response.status_code}. {exc}') from exc
            if response.status_code != 200:
                raise UpdateError(f'GitHub returned HTTP {response.status_code} for the update. Try again later; check ~/.local/state/game-stream/update.log for details.')
            size = 0
            for block in response.iter_content(65536):
                if cancel.is_set():
                    raise UpdateError('Update cancelled')
                size += len(block)
                if size > limit:
                    raise UpdateError('Update download exceeds its expected size')
                consume(block)
            if cancel.is_set():
                raise UpdateError('Update cancelled')
    except requests.RequestException as exc:
        raise UpdateError('Cannot reach the update repository. Check internet access and try again.') from exc


def fetch_release(cancel):
    raw = bytearray()
    transfer(RELEASE_ROOT + 'latest.json', 16384, cancel, raw.extend)
    try:
        return validate_release(json.loads(raw))
    except (ValueError, UnicodeError) as exc:
        raise UpdateError('The repository returned invalid update information') from exc


def verify_package(path, release):
    release = validate_release(release)
    path = Path(path)
    if path.is_symlink() or path.stat().st_size != release['size']:
        raise UpdateError('The update package size does not match')
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(65536), b''):
            digest.update(block)
    if digest.hexdigest() != release['sha256']:
        raise UpdateError('The update checksum does not match. No update was installed; try again.')
    result = subprocess.run(['/usr/bin/dpkg-deb', '--field', str(path), 'Package', 'Version', 'Architecture'],
                            capture_output=True, text=True, timeout=20)
    fields = dict(line.split(': ', 1) for line in result.stdout.splitlines() if ': ' in line)
    if result.returncode or fields != {'Package': 'game-stream', 'Version': release['version'], 'Architecture': 'amd64'}:
        raise UpdateError('The download is not the expected Game Stream Ubuntu package')


def download_release(release, cancel, progress=lambda value: None):
    release = validate_release(release)
    stage = Path(tempfile.mkdtemp(prefix='game-stream-update-'))
    try:
        package = stage / release['filename']
        size = 0
        with package.open('wb') as target:
            def consume(block):
                nonlocal size
                target.write(block)
                size += len(block)
                progress(size * 100 // release['size'])
            transfer(RELEASE_ROOT + release['filename'], release['size'], cancel, consume)
        verify_package(package, release)
        (stage / 'plan.json').write_text(json.dumps(release))
        shutil.copy2(__file__, stage / 'install.py')
        return stage
    except BaseException:
        shutil.rmtree(stage)
        raise


def process_token(pid):
    try:
        # /proc stat names may contain spaces/parentheses; starttime is field 22.
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return None if fields[0] == 'Z' else fields[19]
    except (OSError, IndexError):
        return None


def clean_environment():
    return {k: v for k, v in os.environ.items() if k not in (
        'PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH')}


def handoff(stage):
    if not shutil.which('pkexec', path='/usr/bin'):
        raise UpdateError('Ubuntu’s pkexec authorization tool is missing. Install the latest .deb manually.')
    token = process_token(os.getpid())
    if token is None:
        raise UpdateError('Cannot track application shutdown; update manually with the .deb.')
    # Copy already staged: apt may replace the installed runtime while this runs.
    return subprocess.Popen(['/usr/bin/python3', '-I', str(Path(stage) / 'install.py'),
                             '--apply', str(os.getpid()), token], env=clean_environment(),
                            start_new_session=True, close_fds=True, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def notify(message):
    if shutil.which('zenity'):
        subprocess.run(['zenity', '--error', '--title=Game Stream update', '--text=' + message], check=False)


def apply_update(stage, parent_pid, parent_token):
    stage = Path(stage)
    deadline = monotonic() + 180
    while process_token(parent_pid) == parent_token:
        if monotonic() > deadline:
            notify('Game Stream did not finish closing its source browsers. No update was installed. Close it and retry.')
            shutil.rmtree(stage)
            return 1
        sleep(.2)
    success = False
    try:
        release = validate_release(json.loads((stage / 'plan.json').read_text()))
        package = stage / release['filename']
        verify_package(package, release)
        state = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state'))
        if not state.is_absolute():
            state = Path.home() / '.local/state'
        log_path = state / 'game-stream/update.log'
        log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with os.fdopen(os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'wb') as log:
            result = subprocess.run(['/usr/bin/pkexec', '/usr/bin/apt-get', 'install', '--yes', '--no-remove', str(package)],
                                    stdout=log, stderr=subprocess.STDOUT, env=clean_environment(), check=False)
        success = result.returncode == 0
        if not success:
            notify('Update was cancelled or could not finish. Reopening the installed version. Details: ' + str(log_path))
    except Exception:
        notify('The update could not finish. Reopening the installed version. You can retry Check for Updates.')
    finally:
        shutil.rmtree(stage)
    subprocess.Popen(['/usr/bin/game-stream', '--restore-after-update'], env=clean_environment(),
                     start_new_session=True, close_fds=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 0 if success else 1


if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] != '--apply':
        sys.exit('Launch updates from the Game Stream application menu.')
    sys.exit(apply_update(Path(__file__).resolve().parent, int(sys.argv[2]), sys.argv[3]))
