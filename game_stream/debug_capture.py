"""Bounded, opt-in diagnostics. The sampler runs independently of the Qt loop."""
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

# Also runnable as an isolated script from both a checkout and an installed wheel.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from game_stream.config import xdg_path, write_json
from game_stream.resources import memory_snapshot, diagnostic_metadata

MARKER = '.game-stream-capture.json'
ROOT_MARKER = '.game-stream-debug.json'
KIND = 'game-stream-diagnostics-v1'
MAX_BYTES = 8 * 1024 * 1024
RETAIN_RUNS = 5


def debug_root():
    return xdg_path('XDG_STATE_HOME', '.local/state', 'debug')


def read_private_json(path):
    # Refuse symlinks and never ingest large or arbitrary user files.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, encoding='utf-8') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError('Diagnostic marker is too large')
    return json.loads(raw)


def owned_root(root):
    try:
        return not root.is_symlink() and read_private_json(root / ROOT_MARKER) == {'kind': KIND}
    except (OSError, ValueError):
        return False


def ensure_root(root):
    root = Path(root)
    if not root.exists() and not root.is_symlink():
        root.mkdir(parents=True, mode=0o700)
        write_json(root / ROOT_MARKER, {'kind': KIND})
    if not owned_root(root):
        raise ValueError('The diagnostic directory is unmarked or is a symbolic link')
    return root


def start_ticks(pid):
    try:
        return int(Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def runs(root):
    if not owned_root(root):
        return []
    found = []
    for directory in root.iterdir():
        if directory.is_symlink() or not directory.is_dir():
            continue
        try:
            marker = read_private_json(directory / MARKER)
            if marker.get('kind') == KIND:
                pid, ticks = marker.get('monitor_pid'), marker.get('monitor_start_ticks')
                active = type(pid) is int and ticks is not None and start_ticks(pid) == ticks
                found.append((directory, active))
        except (OSError, ValueError, AttributeError):
            continue
    return sorted(found, key=lambda item: item[0].name, reverse=True)


def clear_captures(root, keep=0):
    """Only marked, inactive captures; never browser caches, sessions or symlink targets."""
    entries = runs(Path(root))
    removed = 0
    for directory, active in entries[keep:]:
        if not active:
            shutil.rmtree(directory)
            removed += 1
    return removed


def new_capture(root):
    root = ensure_root(root)
    directory = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ-'), dir=root))
    write_json(directory / MARKER, {'kind': KIND, 'monitor_pid': None})
    clear_captures(root, keep=RETAIN_RUNS)
    return directory


def owned_capture(directory):
    try:
        if directory.is_symlink() or not owned_root(directory.parent):
            return False
        marker = read_private_json(directory / MARKER)
        return isinstance(marker, dict) and marker.get('kind') == KIND
    except (OSError, ValueError):
        return False


def append_sample(directory, sample):
    path = directory / 'memory.jsonl'
    if path.exists() and path.stat().st_size >= MAX_BYTES:
        (directory / 'memory.3.jsonl').unlink(missing_ok=True)
        for number in (2, 1):
            previous = directory / f'memory.{number}.jsonl'
            if previous.exists():
                previous.replace(directory / f'memory.{number + 1}.jsonl')
        path.replace(directory / 'memory.1.jsonl')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(json.dumps(sample, allow_nan=False) + '\n')
        stream.flush()


def read_heartbeat(path):
    """Keep only the numeric/boolean UI counters, even if a file was edited."""
    value = read_private_json(path)
    if not isinstance(value, dict):
        raise ValueError('Invalid heartbeat')
    result = {key: value[key] for key in ('pid', 'visible_panes', 'expanded_pane')
              if type(value.get(key)) is int}
    result.update({key: value[key] for key in ('closing', 'fullscreen', 'solo_audio', 'show_browser')
                   if type(value.get(key)) is bool})
    result['panes'] = []
    panes = value.get('panes', [])
    if isinstance(panes, list):
        for pane in panes[:4]:
            if not isinstance(pane, dict) or type(pane.get('number')) is not int:
                continue
            row = {'number': pane['number']}
            row.update({key: pane[key] for key in ('audio', 'has_link', 'worker_running', 'relay_running')
                        if type(pane.get(key)) is bool})
            if type(pane.get('playback_started')) is bool:
                row['playback_started'] = pane['playback_started']
            for key in ('accepted_segments', 'buffered_segments'):
                if type(pane.get(key)) is int and pane[key] >= 0:
                    row[key] = pane[key]
            if type(pane.get('relay_age_seconds')) in (int, float) and 0 <= pane['relay_age_seconds'] < 1e9:
                row['relay_age_seconds'] = pane['relay_age_seconds']
            if pane.get('relay_kind') in ('none', 'direct', 'browser'):
                row['relay_kind'] = pane['relay_kind']
            if pane.get('player_state') in ('NothingSpecial', 'Opening', 'Buffering', 'Playing', 'Paused',
                                          'Stopped', 'Ended', 'Error', 'unknown'):
                row['player_state'] = pane['player_state']
            result['panes'].append(row)
    return result


def run_capture(pid, directory, interval=15):
    directory = Path(directory)
    if not owned_capture(directory):
        raise ValueError('Not an owned capture directory')
    started = time.monotonic()
    initial = memory_snapshot(pid)
    initial_ticks = initial['root_start_ticks']
    write_json(directory / 'startup.json', initial)
    write_json(directory / 'metadata.json', dict(diagnostic_metadata(), interval_seconds=interval,
               log_limit_bytes=MAX_BYTES * 4, retained_runs=RETAIN_RUNS,
               monitor_pid=os.getpid(), python_version=list(sys.version_info[:3])))
    sample = initial
    reason = 'app_exited'
    while True:
        sample['elapsed_seconds'] = round(time.monotonic() - started, 1)
        try:
            heartbeat = read_heartbeat(directory / 'heartbeat.json')
            sample['ui_heartbeat'] = heartbeat
            sample['ui_heartbeat_age_seconds'] = round(max(0, time.time() - (directory / 'heartbeat.json').stat().st_mtime), 1)
        except (OSError, ValueError):
            sample['ui_heartbeat'] = None
        append_sample(directory, sample)
        if not sample['running']:
            break
        if initial_ticks is not None and sample['root_start_ticks'] != initial_ticks:
            reason = 'pid_reused'
            break
        deadline = time.monotonic() + interval
        while time.monotonic() < deadline:
            if (directory / 'stop').exists():
                reason = 'stopped'
                break
            time.sleep(min(.25, max(0, deadline - time.monotonic())))
        if reason == 'stopped':
            break
        sample = memory_snapshot(pid)
    write_json(directory / 'status.json', {'reason': reason})


def module_command(module, *arguments):
    # Pin the same package/interpreter even for source launches from another cwd.
    return [sys.executable, '-I', '-c',
            'import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module(sys.argv.pop(1),run_name="__main__")',
            str(Path(__file__).resolve().parent.parent), module, *arguments]


class CaptureProcess:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else debug_root()
        self.directory = None
        self.process = None

    def start(self):
        self.stop()
        self.directory = new_capture(self.root)
        try:
            self.process = subprocess.Popen(module_command('game_stream.debug_capture', '--pid', str(os.getpid()),
                                            '--directory', str(self.directory)), stdin=subprocess.DEVNULL,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            write_json(self.directory / MARKER, dict(kind=KIND, monitor_pid=self.process.pid,
                       monitor_start_ticks=start_ticks(self.process.pid)))
        except OSError:
            self.stop()
            raise
        return self.directory

    def stop(self):
        if self.process is None:
            return
        if self.process.poll() is None:
            try:
                fd = os.open(self.directory / 'stop', os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                os.close(fd)
            except OSError:
                self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=1)
        self.process = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', required=True, type=int)
    parser.add_argument('--directory', required=True, type=Path)
    args = parser.parse_args()
    if args.pid <= 0:
        parser.error('PID must be positive')
    try:
        run_capture(args.pid, args.directory)
    except (OSError, ValueError):
        # Never log raw errors: file names or provider credentials might be present.
        try:
            if owned_capture(args.directory):
                write_json(args.directory / 'status.json', {'reason': 'capture_failed'})
        except OSError:
            pass
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
