import json
from unittest.mock import Mock
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QEvent, QPoint, QRect
from PySide6.QtGui import QKeyEvent, QCursor
from PySide6.QtTest import QTest
from game_stream.app import Wall
from game_stream.config import ConfigStore, DEFAULTS
from game_stream.preferences import PreferencesController, PreferencesDialog


def test_double_tap_audio_zoom_and_timeout():
    app = QApplication.instance() or QApplication([])
    wall = Wall(Mock())
    wall.show()
    app.processEvents()
    clock = [100.0]
    wall.key_clock = lambda: clock[0]
    try:
        QTest.keyClick(wall, Qt.Key.Key_1)
        assert wall.panes[0].audio.isChecked()
        assert not wall.isFullScreen()
        clock[0] += 0.1
        QTest.keyClick(wall, Qt.Key.Key_1)
        app.processEvents()
        assert wall.expanded is wall.panes[0]
        assert wall.isFullScreen()
        QTest.keyClick(wall, Qt.Key.Key_0)
        clock[0] += 1
        QTest.keyClick(wall, Qt.Key.Key_1)
        clock[0] += 0.1
        QTest.keyClick(wall, Qt.Key.Key_1)
        assert wall.expanded is None
        assert not any(p.audio.isChecked() for p in wall.panes)
        clock[0] += 1
        QTest.keyClick(wall, Qt.Key.Key_2)
        clock[0] += 1
        QTest.keyClick(wall, Qt.Key.Key_2)
        assert wall.expanded is None  # Slow taps are audio-only.
        assert wall.panes[1].audio.isChecked()
        app.sendEvent(wall, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_2,
            Qt.KeyboardModifier.NoModifier, '2', True))
        assert wall.expanded is None
        clock[0] += 1
        QTest.keyClick(wall, Qt.Key.Key_4)
        QTest.keyClick(wall, Qt.Key.Key_9)
        clock[0] += 0.1
        QTest.keyClick(wall, Qt.Key.Key_4)
        assert wall.expanded is None
    finally:
        wall.close()


def test_f_toggles_fullscreen_from_controls_but_preserves_text_and_modifiers():
    app = QApplication.instance() or QApplication([])
    wall = Wall(Mock())
    wall.show()
    app.processEvents()
    try:
        QTest.keyClick(wall, Qt.Key.Key_F)
        assert wall.isFullScreen()
        app.sendEvent(wall, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F,
                                     Qt.KeyboardModifier.NoModifier, 'f', True))
        assert wall.isFullScreen()
        QTest.keyClick(wall.tools_overlay, Qt.Key.Key_F)
        assert not wall.isFullScreen()
        QTest.keyClick(wall, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
        assert not wall.isFullScreen()
        field = wall.panes[0].url
        field.setFocus()
        app.processEvents()
        QTest.keyClicks(field, 'https://football.example/final')
        assert field.text() == 'https://football.example/final'
        assert not wall.isFullScreen()
        field.clearFocus()
        QTest.keyClick(wall, Qt.Key.Key_F11)
        assert wall.isFullScreen()
        QTest.keyClick(wall, Qt.Key.Key_F)
        assert not wall.isFullScreen()
    finally:
        wall.close()


def test_three_pane_focus_fills_canvas_all_primaries_and_keeps_playback():
    app = QApplication.instance() or QApplication([])
    engine = Mock()
    players = [Mock() for _ in range(4)]
    engine.media_player_new.side_effect = players
    wall = Wall(engine)
    wall.show()
    wall.count.setCurrentIndex(2)
    wall.select_audio(wall.panes[1])
    app.processEvents()
    handles = [int(p.video.winId()) for p in wall.panes]
    stops = [p.stop.call_count for p in players]
    wall.toggle_fullscreen()
    app.processEvents()
    try:
        assert wall.layout_button.isVisible()
        for primary in range(3):
            wall.primary_actions[primary].trigger()
            app.processEvents()
            large = wall.panes[primary]
            top, bottom = [p for p in wall.panes[:3] if p is not large]
            assert large.pos() == QPoint(0, 0)
            assert large.height() == wall.root.height()
            assert large.width() > top.width() * 1.8
            assert top.x() - large.geometry().right() - 1 == 2
            assert bottom.y() - top.geometry().bottom() - 1 == 2
            assert top.y() == 0 and bottom.geometry().bottom() == wall.root.rect().bottom()
            assert top.geometry().right() == bottom.geometry().right() == wall.root.rect().right()
            assert wall.panes[1].audio.isChecked()  # Layout selection never changes audio.
            assert [int(p.video.winId()) for p in wall.panes] == handles
            assert [p.stop.call_count for p in players] == stops
        wall.expand(wall.panes[0])
        wall.expand(wall.panes[0])
        app.processEvents()
        assert wall.panes[2].height() == wall.root.height()
        wall.layout_actions['grid'].trigger()
        app.processEvents()
        assert wall.panes[2].height() < wall.root.height()
        wall.layout_actions['focus'].trigger()
        wall.toggle_fullscreen()
        app.processEvents()
        assert wall.panes[2].height() < wall.root.height()  # Windowed grid retained.
        wall.count.setCurrentIndex(3)
        assert not wall.layout_button.isVisible()
    finally:
        wall.close()


def test_single_stream_fullscreen_hides_border_then_restores_it_in_grid():
    app = QApplication.instance() or QApplication([])
    wall = Wall(Mock())
    wall.show()
    wall.select_audio(wall.panes[0])
    wall.toggle_fullscreen()
    app.processEvents()
    wall.pointer_timer.stop()
    try:
        wall.position_video_input()
        corner = lambda: wall.video_input.mapFromGlobal(wall.panes[0].mapToGlobal(QPoint()))
        assert wall.video_input.grab().toImage().pixelColor(corner()).name() == '#00ff00'
        wall.expand(wall.panes[0])
        app.processEvents()
        assert wall.video_input.grab().toImage().pixelColor(corner()).name() != '#00ff00'
        assert wall.panes[0].audio.isChecked()
        wall.expand(wall.panes[0])
        app.processEvents()
        assert wall.video_input.grab().toImage().pixelColor(corner()).name() == '#00ff00'
        wall.count.setCurrentIndex(0)
        app.processEvents()
        assert wall.video_input.grab().toImage().pixelColor(corner()).name() != '#00ff00'
        wall.toggle_fullscreen()
        app.processEvents()
        assert wall.panes[0].layout.contentsMargins().left() == 1
    finally:
        wall.close()


def test_removed_catalog_legacy_config_migrates_and_layout_persists(tmp_path, monkeypatch):
    import requests
    monkeypatch.setattr(requests, 'get', Mock(side_effect=AssertionError('Unexpected site request')))
    store = ConfigStore(tmp_path / 'config.json')
    store.path.write_text(json.dumps(dict(root_url='bad old root', catalog_browser=True,
        refresh_on_start=True, double_tap_ms=450, audio_border_color='#112233')))
    app = QApplication.instance() or QApplication([])
    wall = Wall(Mock())
    controller = PreferencesController(wall, store)
    try:
        controller.start()
        assert not hasattr(wall, 'games_menu') and not hasattr(wall.panes[0], 'games_button')
        assert wall.double_tap_ms == 450 and wall.audio_border_color == '#112233'
        wall.choose_three_layout(primary=3)
        assert store.load()['three_pane_primary'] == 3
        assert 'root_url' not in json.loads(store.path.read_text())
        for invalid in ({'three_pane_primary': 4}, {'three_pane_layout': 'unknown'}, {'double_tap_ms': 0}):
            with pytest.raises(ValueError):
                store.save({**DEFAULTS, **invalid})
    finally:
        wall.close()
    other = Wall(Mock())
    try:
        PreferencesController(other, store).start()
        assert other.three_pane_primary == 3
    finally:
        other.close()


def test_preferences_dialog_saves_layout_and_overlay_choices(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / 'config.json')
    dialog = PreferencesDialog(store, DEFAULTS, None)
    dialog.three_layout.setCurrentIndex(1)
    dialog.three_primary.setCurrentIndex(1)
    dialog.overlay_delay.setValue(4000)
    dialog.border_color.setText('#123456')
    dialog.save()
    assert store.load()['three_pane_layout'] == 'grid'
    assert store.load()['three_pane_primary'] == 2
    assert store.load()['overlay_timeout_ms'] == 4000
    assert store.load()['audio_border_color'] == '#123456'
