"""Private, atomic snapshots of user-facing settings, never browser credentials."""
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile


class SessionError(ValueError):
    pass


def state_path():
    root = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state'))
    if not root.is_absolute():
        root = Path.home() / '.local/state'
    return root / 'game-stream/session.json'


def validate_session(value):
    if not isinstance(value, dict) or value.get('version') != 1:
        raise SessionError('Unsupported session format')

    def number(obj, key, low, high, default):
        n = obj.get(key, default)
        if type(n) is not int or not low <= n <= high:
            raise SessionError('Invalid session setting')
        return n

    def flag(obj, key, default):
        n = obj.get(key, default)
        if type(n) is not bool:
            raise SessionError('Invalid session setting')
        return n

    def text(obj, key, limit):
        n = obj.get(key, '')
        if not isinstance(n, str) or len(n) > limit or '\x00' in n:
            raise SessionError('Invalid session setting')
        return n

    rows = value.get('panes')
    if not isinstance(rows, list) or len(rows) != 4 or any(not isinstance(p, dict) for p in rows):
        raise SessionError('Invalid session panes')
    panes = [dict(url=text(p, 'url', 16384), volume=number(p, 'volume', 0, 100, 80),
                  audio=flag(p, 'audio', False), active=flag(p, 'active', False)) for p in rows]
    count = number(value, 'pane_count', 1, 4, 4)
    for p in panes[count:]:
        p.update(audio=False, active=False)
    last = value.get('last_audio')
    expanded = value.get('expanded')
    for index in (last, expanded):
        if index is not None and (type(index) is not int or not 0 <= index < count):
            raise SessionError('Invalid session pane selection')
    geometry = text(value, 'geometry', 8192)
    try:
        base64.b64decode(geometry, validate=True)
    except ValueError as exc:
        raise SessionError('Invalid window geometry') from exc
    # Only these keys survive validation. Never serialize arbitrary page data.
    return dict(version=1, panes=panes, pane_count=count, last_audio=last, expanded=expanded,
                solo_audio=flag(value, 'solo_audio', True), show_browser=flag(value, 'show_browser', True),
                fullscreen=flag(value, 'fullscreen', False), clean_exit=flag(value, 'clean_exit', False),
                geometry=geometry, cookie_file=text(value, 'cookie_file', 4096))


class SessionStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else state_path()

    def load(self):
        try:
            with self.path.open('rb') as f:
                raw = f.read(1024 * 1024 + 1)
        except FileNotFoundError:
            return None
        if len(raw) > 1024 * 1024:
            raise SessionError('Session file is too large')
        try:
            return validate_session(json.loads(raw))
        except (ValueError, TypeError, RecursionError) as exc:
            raise SessionError('Could not read the saved session') from exc

    def save(self, snapshot, clean_exit=False):
        data = validate_session({**snapshot, 'clean_exit': clean_exit})
        data['saved_at'] = datetime.now(timezone.utc).isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                             prefix='.session-', delete=False) as f:
                temporary = f.name
                os.fchmod(f.fileno(), 0o600)
                json.dump(data, f, ensure_ascii=False, allow_nan=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, self.path)
            temporary = None
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
