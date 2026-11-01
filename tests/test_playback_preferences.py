from threading import Event
import time
from unittest.mock import Mock

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from game_stream.app import Wall
from game_stream.config import ConfigStore, DEFAULTS


def until(predicate):
    end = time.monotonic() + 6
    while not predicate() and time.monotonic() < end:
        QTest.qWait(10)
    assert predicate()


def test_new_load_takes_audio_even_when_mixing_enabled():
    app = QApplication.instance() or QApplication([])
    engine = Mock()
    players = [Mock() for _ in range(4)]
    engine.media_player_new.side_effect = players
    wall = Wall(engine)
    wall.show()
    wall.solo.setChecked(False)
    wall.panes[0].audio.setChecked(True)
    wall.panes[1].audio.setChecked(True)
    pane = wall.panes[2]
    try:
        pane.url.setText('https://example.com/new.m3u8')
        pane.load()
        assert [p.audio.isChecked() for p in wall.panes] == [False, False, True, False]
        until(lambda: pane.worker is None)
        # A late native Playing event from another pane must reapply mute.
        wall.panes[0].playback_started.emit()
        players[0].audio_set_mute.assert_called_with(True)
        pane.playback_started.emit()
        players[2].audio_set_mute.assert_called_with(False)
    finally:
        pane.stop()
        until(lambda: pane.worker is None)
        wall.close()
        mute_calls = players[2].audio_set_mute.call_count
        pane.playback_started.emit()  # A native callback queued during shutdown.
        assert players[2].audio_set_mute.call_count == mute_calls


def test_paste_load_and_invalid_clipboard_leave_current_link(monkeypatch):
    app = QApplication.instance() or QApplication([])
    wall = Wall(Mock())
    pane = wall.panes[0]
    pane.load = Mock()
    try:
        app.clipboard().setText('  https://example.com/live  ')
        pane.paste_button.click()
        assert pane.url.text() == 'https://example.com/live'
        pane.load.assert_called_once()
        app.clipboard().setText('file:///etc/passwd')
        pane.paste_button.click()
        assert pane.load.call_count == 1
        assert pane.url.text() == 'https://example.com/live'
        assert 'Clipboard' in pane.status.text()
    finally:
        wall.close()


def test_audio_border_is_one_pixel_and_fullscreen_keeps_video_geometry():
    app = QApplication.instance() or QApplication([])
    wall = Wall(Mock())
    wall.show()
    wall.select_audio(wall.panes[0])
    try:
        assert wall.panes[0].layout.contentsMargins().left() == 1
        assert wall.panes[1].layout.contentsMargins().left() == 0
        wall.toggle_fullscreen()
        app.processEvents()
        until(lambda: wall.video_input.mapToGlobal(QPoint()) == wall.root.mapToGlobal(QPoint()))
        wall.pointer_timer.stop()
        for pane in wall.panes:
            assert pane.video.geometry() == pane.rect()
        image = wall.video_input.grab().toImage()
        corner = wall.video_input.mapFromGlobal(wall.panes[0].mapToGlobal(QPoint()))
        assert image.pixelColor(corner).name() == '#00ff00'
        assert image.pixelColor(corner + QPoint(1, 1)).name() != '#00ff00'
        wall.apply_view_preferences({**DEFAULTS, 'audio_border_color': '#ff00ff'})
        assert wall.video_input.grab().toImage().pixelColor(corner).name() == '#ff00ff'
        wall.select_audio(wall.panes[1])
        assert wall.video_input.grab().toImage().pixelColor(corner).name() != '#ff00ff'
        wall.apply_view_preferences({**DEFAULTS, 'audio_border_enabled': False})
        other = wall.video_input.mapFromGlobal(wall.panes[1].mapToGlobal(QPoint()))
        assert wall.video_input.grab().toImage().pixelColor(other).name() != '#00ff00'
    finally:
        wall.close()


def test_overlay_preferences_shortcuts_disabled_pointer_enabled_and_no_overlays():
    app = QApplication.instance() or QApplication([])
    wall = Wall(Mock())
    wall.show()
    wall.apply_view_preferences({**DEFAULTS, 'overlay_on_shortcuts': False, 'overlay_timeout_ms': 9000})
    wall.toggle_fullscreen()
    app.processEvents()
    wall.pointer_timer.stop()
    try:
        wall.hide_controls()
        QTest.keyClick(wall, Qt.Key.Key_1)
        QTest.keyClick(wall, Qt.Key.Key_6)
        app.processEvents()
        assert wall.expanded is wall.panes[1]
        assert not wall.tools_overlay.isVisible()
        assert not any(p.overlay.isVisible() for p in wall.panes)
        wall.show_controls()  # Pointer-triggered controls are still available.
        assert wall.tools_overlay.isVisible()
        assert wall.idle.interval() == 9000 and wall.idle.isActive()
        wall.apply_view_preferences({**DEFAULTS, 'overlay_timeout_ms': 0})
        wall.show_controls()
        assert not wall.idle.isActive()
        wall.apply_view_preferences({**DEFAULTS, 'overlay_enabled': False})
        wall.show_controls()
        assert not wall.tools_overlay.isVisible()
        assert wall.video_input.isVisible()  # Double-click / border layer survives.
        assert not wall.idle.isActive()
    finally:
        wall.close()


def test_replace_waits_for_cleanup_skips_superseded_urls(monkeypatch):
    from game_stream import app as module
    from game_stream.bridge import BrowserRelay
    from game_stream.resolver import Source
    app = QApplication.instance() or QApplication([])
    cleanup_gate = Event()
    events = []
    def resolve(url, *args, cancel, on_ready, **kwargs):
        events.append(('open', url))
        relay = BrowserRelay()
        on_ready(Source(relay.url, relay=relay))
        cancel.wait(5)
        if url.endswith('/first'):
            cleanup_gate.wait(5)
            on_ready(Source(relay.url, relay=relay))  # A stale queued callback.
        relay.close()
        events.append(('closed', url))
    monkeypatch.setattr(module, 'resolve', resolve)
    wall = Wall(Mock())
    wall.show()
    pane = wall.panes[0]
    try:
        pane.url.setText('https://example.com/first')
        pane.load()
        until(lambda: pane.relay is not None)
        pane.url.setText('https://example.com/superseded')
        pane.load()
        pane.url.setText('https://example.com/final')
        pane.load()
        QTest.qWait(50)
        assert [url for kind, url in events if kind == 'open'] == ['https://example.com/first']
        cleanup_gate.set()
        until(lambda: ('open', 'https://example.com/final') in events and pane.relay is not None)
        assert not any(url.endswith('/superseded') for kind, url in events)
        assert events.index(('closed', 'https://example.com/first')) < events.index(('open', 'https://example.com/final'))
    finally:
        cleanup_gate.set()
        pane.stop()
        until(lambda: pane.worker is None)
        wall.close()


def test_stop_cancels_already_queued_restart(monkeypatch):
    from game_stream import app as module
    from game_stream.bridge import BrowserRelay
    from game_stream.resolver import Source
    app = QApplication.instance() or QApplication([])
    urls = []
    def resolve(url, *args, cancel, on_ready, **kwargs):
        urls.append(url)
        relay = BrowserRelay()
        on_ready(Source(relay.url, relay=relay))
        cancel.wait(5)
        relay.close()
    monkeypatch.setattr(module, 'resolve', resolve)
    wall = Wall(Mock())
    wall.show()
    pane = wall.panes[0]
    try:
        pane.url.setText('https://example.com/first')
        pane.load()
        until(lambda: pane.relay is not None)
        pane.worker.finished.connect(pane.stop)  # Runs after queued restart is scheduled.
        pane.url.setText('https://example.com/second')
        pane.load()
        until(lambda: pane.worker is None)
        QTest.qWait(50)
        assert urls == ['https://example.com/first']
        assert pane.relay is None and not pane.want_playing
    finally:
        pane.stop()
        until(lambda: pane.worker is None)
        wall.close()


def test_existing_config_gains_safe_defaults_and_validates_new_values(tmp_path):
    import json
    import pytest
    store = ConfigStore(tmp_path / 'config.json')
    store.path.write_text(json.dumps({'root_url': 'https://v2.streameast.ga/', 'double_tap_ms': 400}))
    value = store.load()
    assert value['audio_border_color'] == '#00ff00'
    assert value['overlay_timeout_ms'] == 2200
    for invalid in ({'audio_border_color': 'red; invalid'}, {'overlay_timeout_ms': -1}, {'overlay_enabled': 'no'}):
        with pytest.raises(ValueError):
            store.save({**DEFAULTS, **invalid})
