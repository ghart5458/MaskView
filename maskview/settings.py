import json
import sys
from pathlib import Path


def _path() -> Path:
    if getattr(sys, 'frozen', False):
        # Packaged exe — store next to the executable
        return Path(sys.executable).parent / 'maskview_settings.json'
    # Development — store next to main.py (two levels up from this file)
    return Path(__file__).parent.parent / 'maskview_settings.json'


_DEFAULTS: dict = {
    'turbo_stride':       1,
    'checked_file_types': ['original', 'maskseg'],
}


def load() -> dict:
    try:
        raw = json.loads(_path().read_text(encoding='utf-8'))
        return {**_DEFAULTS, **raw}
    except Exception:
        return dict(_DEFAULTS)


def save(updates: dict) -> None:
    try:
        current = load()
        current.update(updates)
        _path().write_text(json.dumps(current, indent=2), encoding='utf-8')
    except Exception:
        pass
