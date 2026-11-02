from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt, QTimer, QPoint, QRect, QEvent, QCoreApplication
from PySide6.QtGui import QKeyEvent, QCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QPushButton
from game_stream.app import Wall
from game_stream.config import ConfigStore, DEFAULTS
from game_stream.preferences import PreferencesController, PreferencesDialog


def test_alt_pane_count_shortcuts_restore_grid_and_stop_only_hidden_streams():
    app = QApplication.instance() or QApplication([])
    engine = Mock()
    players = [Mock() for _ in range(4)]
    engine.media_player_new.side_effect = players
    wall = Wall(engine)
    wall.show()
    wall.panes[3].url.setText('https://example.com/keep-this-link')
    wall.panes[3].want_playing = True
    wall.select_audio(wall.panes[3])
    app.processEvents()
    try:
        for count in (1, 2, 3, 4):
            QTest.keyClick(wall, Qt.Key.Key_0 + count, Qt.KeyboardModifier.AltModifier)
            app.processEvents()
            assert wall.count.currentIndex() == count - 1
            assert sum(p.isVisible() for p in wall.panes) == count
        assert not wall.panes[3].want_playing and not wall.panes[3].audio.isChecked()
        assert wall.panes[3].url.text() == 'https://example.com/keep-this-link'
        QTest.keyClick(wall, Qt.Key.Key_F)
        QTest.keyClick(wall.tools_overlay, Qt.Key.Key_3, Qt.KeyboardModifier.AltModifier)
        wall.expand(wall.panes[1])
        QTest.keyClick(wall.tools_overlay, Qt.Key.Key_3, Qt.KeyboardModifier.AltModifier)
        assert wall.expanded is None and wall.isFullScreen()
        assert sum(p.isVisible() for p in wall.panes) == 3
        app.sendEvent(wall, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_1,
            Qt.KeyboardModifier.AltModifier, '1', True))
        assert wall.count.currentIndex() == 2
        QTest.keyClick(wall, Qt.Key.Key_1, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
        assert wall.count.currentIndex() == 2
    finally:
        wall.close()


def test_shift_large_pane_accepts_digits_and_shifted_symbols_preserves_audio_and_text(tmp_path):
    app = QApplication.instance() or QApplication([])
    wall = Wall(Mock())
    store = ConfigStore(tmp_path / 'config.json')
    controller = PreferencesController(wall, store)
    controller.start()
    wall.show()
    wall.count.setCurrentIndex(2)
    wall.select_audio(wall.panes[1])
    app.processEvents()
    try:
        for key, expected in ((Qt.Key.Key_1, 1), (Qt.Key.Key_At, 2), (Qt.Key.Key_NumberSign, 3)):
            QTest.keyClick(wall, key, Qt.KeyboardModifier.ShiftModifier)
            assert wall.three_pane_primary == expected
            assert not wall.isFullScreen()
            assert wall.panes[1].audio.isChecked()
        QTest.keyClick(wall, Qt.Key.Key_F)
        QTest.keyClick(wall.tools_overlay, Qt.Key.Key_Exclam, Qt.KeyboardModifier.ShiftModifier)
        assert wall.three_pane_primary == 1 and wall.isFullScreen()
        QTest.keyClick(wall, Qt.Key.Key_Dollar, Qt.KeyboardModifier.ShiftModifier)
        assert wall.three_pane_primary == 1
        app.sendEvent(wall, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_2,
            Qt.KeyboardModifier.ShiftModifier, '@', True))
        assert wall.three_pane_primary == 1
        assert store.load()['three_pane_primary'] == 1
        QTest.keyClick(wall, Qt.Key.Key_F)
        app.processEvents()
        field = wall.panes[0].url
        field.setFocus()
        app.processEvents()
        QTest.keyClicks(field, 'https://example.com/!@#$')
        assert field.text() == 'https://example.com/!@#$'
        assert wall.three_pane_primary == 1 and wall.panes[1].audio.isChecked()
        field.clearFocus()
        wall.count.setCurrentIndex(3)
        QTest.keyClick(wall, Qt.Key.Key_3, Qt.KeyboardModifier.ShiftModifier)
        assert wall.three_pane_primary == 1
    finally:
        wall.close()


def test_playback_flags_live_in_preferences_and_toolbar_has_no_cookie_or_app_controls(tmp_path):
    app = QApplication.instance() or QApplication([])
    wall = Wall(Mock())
    store = ConfigStore(tmp_path / 'config.json')
    controller = PreferencesController(wall, store)
    controller.start()
    wall.show()
    wall.browser.setChecked(False)
    wall.solo.setChecked(False)
    wall.panes[0].audio.setChecked(True)
    wall.panes[1].audio.setChecked(True)
    seen = []
    def edit():
        dialog = app.activeModalWidget()
        assert isinstance(dialog, QDialog)
        seen.append((dialog.browser.isChecked(), dialog.solo.isChecked()))
        dialog.browser.setChecked(True)
        dialog.solo.setChecked(True)
        dialog.save()
    try:
        QTimer.singleShot(0, edit)
        controller.open_preferences()
        assert seen == [(False, False)]
        assert wall.browser.isChecked() and wall.solo.isChecked()
        assert store.load()['show_browser'] is True and store.load()['solo_audio'] is True
        assert sum(p.audio.isChecked() for p in wall.panes) == 1
        assert not wall.browser.isVisible() and not wall.solo.isVisible()
        assert not hasattr(wall, 'cookies') and not hasattr(wall, 'app_button')
        assert not any('cookie' in b.text().lower() for b in wall.tools.findChildren(QPushButton))
    finally:
        wall.close()


def test_preferences_update_button_saves_and_closes_modal_before_starting_check(tmp_path):
    app = QApplication.instance() or QApplication([])
    wall = Wall(Mock())
    wall.show()
    store = ConfigStore(tmp_path / 'config.json')
    controller = PreferencesController(wall, store)
    controller.start()
    wall.updater = Mock()
    events = []
    def requested():
        assert app.activeModalWidget() is None
        assert store.load()['button_text_size'] == 15
        assert wall.button_text_size == 15
        events.append('check updates')
    wall.update_action.triggered.connect(requested)
    def edit():
        dialog = app.activeModalWidget()
        dialog.text_size.setValue(15)
        dialog.update_button.click()
    try:
        QTimer.singleShot(0, edit)
        controller.open_preferences()
        assert events == ['check updates']
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not wall.findChildren(PreferencesDialog)
    finally:
        wall.updater = None
        wall.close()


def test_translucent_overlay_reveal_fade_and_larger_text_wrap_within_pane():
    app = QApplication.instance() or QApplication([])
    wall = Wall(Mock())
    wall.show()
    wall.count.setCurrentIndex(2)
    wall.toggle_fullscreen()
    app.processEvents()
    wall.pointer_timer.stop()
    pane = wall.panes[2]
    handles = [int(p.video.winId()) for p in wall.panes]
    try:
        QCursor.setPos(pane.mapToGlobal(pane.rect().center()))
        wall.show_controls()
        assert pane.overlay.windowOpacity() == pytest.approx(.85, abs=.01)
        wall.apply_view_preferences({**DEFAULTS, 'overlay_opacity_percent': 45, 'button_text_size': 24})
        app.processEvents()
        wall.show_controls()
        app.processEvents()
        assert pane.overlay.windowOpacity() == pytest.approx(.45, abs=.01)
        assert pane.audio.font().pixelSize() == 24
        assert [int(p.video.winId()) for p in wall.panes] == handles
        buttons = [b for b in pane.chrome.findChildren(QPushButton) if b.isVisible()]
        assert len({b.mapTo(pane.overlay, QPoint()).y() for b in buttons}) > 1
        for button in buttons:
            bounds = QRect(button.mapTo(pane.overlay, QPoint()), button.size())
            assert pane.overlay.rect().contains(bounds), (button.text(), bounds, pane.overlay.rect())
        bounds = QRect(pane.mapToGlobal(QPoint()), pane.size())
        assert bounds.contains(pane.overlay.geometry())
        assert QApplication.mouseButtons() == Qt.MouseButton.NoButton
        assert QApplication.activePopupWidget() is None
        wall.fade_controls()
        for bar in [wall.tools_overlay, *[p.overlay for p in wall.panes]]:
            bar.animation.setCurrentTime(bar.animation.duration())
        app.processEvents()
        assert not pane.overlay.isVisible()
        wall.show_controls()
        assert pane.overlay.windowOpacity() == pytest.approx(.45, abs=.01)
    finally:
        wall.close()


def test_new_preferences_validate_limits_and_gain_defaults_from_old_configs(tmp_path):
    import json
    store = ConfigStore(tmp_path / 'config.json')
    store.path.write_text(json.dumps({'overlay_timeout_ms': 1500}))
    assert store.load()['overlay_opacity_percent'] == 85
    assert store.load()['button_text_size'] == 12
    for invalid in ({'button_text_size': 30}, {'overlay_opacity_percent': 0},
                    {'show_browser': 'yes'}, {'solo_audio': 1}):
        with pytest.raises(ValueError):
            store.save({**DEFAULTS, **invalid})
