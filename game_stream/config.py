"""User-editable preferences and private atomic JSON persistence."""
import json
import os
import re
from pathlib import Path
import tempfile

DEFAULTS = dict(three_pane_layout='focus', three_pane_primary=1,
                double_tap_ms=350, audio_border_enabled=True,
                audio_border_color='#00ff00', overlay_enabled=True,
                overlay_on_shortcuts=True, overlay_timeout_ms=2200)


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
        if value['three_pane_layout'] not in ('focus', 'grid'):
            raise ValueError('Choose a valid three-pane layout')
        if type(value['three_pane_primary']) is not int or not 1 <= value['three_pane_primary'] <= 3:
            raise ValueError('The large stream must be quadrant 1, 2 or 3')
        if type(value['double_tap_ms']) is not int or not 150 <= value['double_tap_ms'] <= 800:
            raise ValueError('Double-tap interval must be 150–800 ms')
        if (not isinstance(value['audio_border_color'], str) or
                not re.fullmatch(r'#[0-9a-fA-F]{6}', value['audio_border_color'])):
            raise ValueError('Audio border color must be a six-digit hex color, such as #00ff00')
        if type(value['overlay_timeout_ms']) is not int or not 0 <= value['overlay_timeout_ms'] <= 30000:
            raise ValueError('Overlay duration must be 0–30000 ms')
        if any(type(value[k]) is not bool for k in ('audio_border_enabled', 'overlay_enabled', 'overlay_on_shortcuts')):
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
