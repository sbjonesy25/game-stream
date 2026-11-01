import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from game_stream.app import Wall
from game_stream.recovery import SessionRecovery
from game_stream.session import SessionError, SessionStore, validate_session


def snapshot():
    return dict(version=1, panes=[dict(url=f'https://example.com/page/{i}', volume=30+i,
                audio=False, active=i < 2) for i in range(4)], pane_count=2,
                last_audio=1, expanded=1, solo_audio=True, show_browser=True,
                fullscreen=True, geometry='', cookie_file='')


def test_private_atomic_store_and_invalid_files(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / 'state/session.json')
    assert store.load() is None
    original = snapshot()
    store.save(original)
    assert store.load()['last_audio'] == 1
    assert store.load()['clean_exit'] is False
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert not list(store.path.parent.glob('.session-*'))
    # A failed replacement preserves the previous good snapshot.
    def fail_replace(*args):
        raise OSError('disk unavailable')
    monkeypatch.setattr(os, 'replace', fail_replace)
    with pytest.raises(OSError):
        store.save({**original, 'pane_count': 3})
    assert store.load()['pane_count'] == 2
    assert not list(store.path.parent.glob('.session-*'))
    store.path.write_text('{broken')
    with pytest.raises(SessionError):
        store.load()


def test_only_session_fields_and_active_panes_are_saved(tmp_path):
    value = snapshot()
    value.update(cookies=[{'secret': 'DO NOT STORE'}], authorization='DO NOT STORE')
    store = SessionStore(tmp_path / 'session.json')
    store.save(value)
    assert 'DO NOT STORE' not in store.path.read_text()
    invalid = snapshot()
    invalid['panes'][0]['volume'] = 200
    with pytest.raises(SessionError):
        validate_session(invalid)


def test_saved_state_survives_process_crash(tmp_path):
    store = SessionStore(tmp_path / 'session.json')
    result = subprocess.run([sys.executable, '-c',
        'import json,os,signal,sys; from game_stream.session import SessionStore; '
        'SessionStore(sys.argv[1]).save(json.loads(sys.argv[2])); '
        'os.kill(os.getpid(), signal.SIGKILL)', str(store.path), json.dumps(snapshot())],
        capture_output=True)
    assert result.returncode < 0
    assert store.load()['clean_exit'] is False
    assert store.load()['panes'][1]['active'] is True


def test_restore_prompt_accepts_settings_and_only_restarts_active_panes(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = SessionStore(tmp_path / 'session.json')
    store.save(snapshot())
    wall = Wall(Mock())
    wall.show()
    loads = []
    for i, pane in enumerate(wall.panes):
        pane.load = lambda i=i: loads.append(i)
    recovery = SessionRecovery(wall, store)
    questions = []
    try:
        recovery.start(ask=lambda previous: questions.append(previous) or True)
        app.processEvents()
        assert len(questions) == 1
        assert questions[0]['clean_exit'] is False
        assert wall.count.currentIndex() == 1
        assert wall.expanded is wall.panes[1]
        assert wall.isFullScreen()
        assert wall.panes[3].url.text() == 'https://example.com/page/3'
        assert wall.panes[1].volume.value() == 31
        assert not any(p.audio.isChecked() for p in wall.panes)
        assert wall.last_audio is wall.panes[1]
        assert loads == [0, 1]
        wall.toggle_audio_mute()
        assert wall.panes[1].audio.isChecked()
    finally:
        wall.close()
    assert store.load()['clean_exit'] is True


def test_decline_restore_and_clean_close_keeps_active_flags(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = SessionStore(tmp_path / 'session.json')
    store.save(snapshot(), clean_exit=True)
    wall = Wall(Mock())
    wall.show()
    recovery = SessionRecovery(wall, store)
    recovery.start(ask=lambda previous: False)
    assert all(not p.url.text() for p in wall.panes)
    pane = wall.panes[0]
    pane.url.setText('https://example.com/remember')
    pane.want_playing = True
    recovery.checkpoint()
    assert store.load()['panes'][0]['active'] is True
    assert store.load()['clean_exit'] is False
    wall.close()
    assert pane.want_playing is False
    # Closing stop() must not overwrite the pre-close active stream snapshot.
    saved = store.load()
    assert saved['clean_exit'] is True
    assert saved['panes'][0]['url'] == 'https://example.com/remember'
    assert saved['panes'][0]['active'] is True


def test_edits_checkpoint_before_close(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = SessionStore(tmp_path / 'session.json')
    wall = Wall(Mock())
    recovery = SessionRecovery(wall, store)
    recovery.start()
    try:
        wall.panes[2].url.setText('https://example.com/new-link')
        QTest.qWait(400)
        assert store.load()['panes'][2]['url'] == 'https://example.com/new-link'
        assert store.load()['clean_exit'] is False
    finally:
        wall.close()
