"""User-editable preferences and private atomic JSON persistence."""
import json
import os
from pathlib import Path
import tempfile
from .resolver import validate_url

DEFAULTS = dict(root_url='https://v2.streameast.ga/', catalog_browser=False,
                refresh_on_start=True, double_tap_ms=350)


def xdg_path(variable, fallback, suffix):
    root = Path(os.environ.get(variable, Path.home() / fallback))
    if not root.is_absolute():
        root = Path.home() / fallback
    return root / 'game-stream' / suffix


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as f:
            temporary = f.name
            os.fchmod(f.fileno(), 0o600)
            json.dump(data, f, ensure_ascii=True, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)


class ConfigStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else xdg_path('XDG_CONFIG_HOME', '.config', 'config.json')

    def validate(self, data):
        value = {**DEFAULTS, **data}
        if not isinstance(value['root_url'], str) or len(value['root_url']) > 16384:
            raise ValueError('Enter a valid root page URL')
        value['root_url'] = validate_url(value['root_url'].strip())
        if type(value['double_tap_ms']) is not int or not 150 <= value['double_tap_ms'] <= 800:
            raise ValueError('Double-tap interval must be 150–800 ms')
        if any(type(value[k]) is not bool for k in ('catalog_browser', 'refresh_on_start')):
            raise ValueError('Invalid preferences')
        return {key: value[key] for key in DEFAULTS}

    def load(self):
        try:
            with self.path.open('r', encoding='utf-8') as f:
                raw = f.read(65537)
            if len(raw) > 65536:
                raise ValueError('Preferences file is too large')
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError('Invalid preferences')
            return self.validate(data)
        except FileNotFoundError:
            return dict(DEFAULTS)

    def save(self, value):
        value = self.validate(value)
        write_json(self.path, value)
        return value
