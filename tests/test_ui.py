"""Exercise Qt controls offscreen; native VLC rendering is a host smoke test."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from unittest.mock import Mock
from PySide6.QtWidgets import QApplication
from game_stream.app import Wall


def test_layout_audio_fullscreen_and_cleanup():
    app = QApplication.instance() or QApplication([])
    engine = Mock()
    players = [Mock() for _ in range(4)]
    engine.media_player_new.side_effect = players
    wall = Wall(engine)
    wall.show()
    app.processEvents()
    assert all(pane.isVisible() for pane in wall.panes)
    wall.panes[0].audio.setChecked(True)
    wall.panes[1].audio.setChecked(True)
    assert not wall.panes[0].audio.isChecked()
    assert wall.panes[1].audio.isChecked()
    wall.solo.setChecked(False)
    wall.panes[0].audio.setChecked(True)
    assert wall.panes[1].audio.isChecked()
    wall.solo.setChecked(True)
    assert sum(p.audio.isChecked() for p in wall.panes) == 1
    wall.panes[0].volume.setValue(41)
    players[0].audio_set_volume.assert_called_with(41)
    wall.expand(wall.panes[2])
    assert wall.panes[2].isVisible()
    assert not wall.panes[0].isVisible()
    wall.restore()
    assert all(pane.isVisible() for pane in wall.panes)
    for count in (1, 2, 3, 4):
        wall.count.setCurrentIndex(count - 1)
        assert sum(pane.isVisible() for pane in wall.panes) == count
    wall.toggle_fullscreen()
    assert wall.isFullScreen()
    wall.restore()
    assert not wall.isFullScreen()
    wall.panes[0].url.setText('file:///etc/passwd')
    wall.panes[0].load()
    assert 'HTTP' in wall.panes[0].status.text()
    wall.close()
    for player in players:
        player.release.assert_called_once()
    engine.release.assert_called_once()


def test_fullscreen_video_spacing_and_fading_controls():
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QCursor
    from PySide6.QtTest import QTest
    app = QApplication.instance() or QApplication([])
    wall = Wall(Mock())
    wall.show()
    app.processEvents()
    handles = [int(p.video.winId()) for p in wall.panes]
    wall.toggle_fullscreen()
    app.processEvents()
    assert wall.immersive
    assert handles == [int(p.video.winId()) for p in wall.panes]
    assert wall.layout.contentsMargins().left() == 0
    for pane in wall.panes:
        assert pane.chrome.parent() is pane.overlay
        assert pane.video.geometry() == pane.rect()
    a, b, c = wall.panes[:3]
    assert b.video.mapToGlobal(QPoint()).x() - a.video.mapToGlobal(QPoint()).x() - a.video.width() == 2
    assert c.video.mapToGlobal(QPoint()).y() - a.video.mapToGlobal(QPoint()).y() - a.video.height() == 2
    QCursor.setPos(wall.panes[0].mapToGlobal(wall.panes[0].rect().center()))
    wall.show_controls()
    assert wall.panes[0].overlay.isVisible()
    assert sum(p.overlay.isVisible() for p in wall.panes) == 1
    wall.fade_controls()
    for bar in [wall.tools_overlay, *[p.overlay for p in wall.panes]]:
        bar.animation.setCurrentTime(bar.animation.duration())
    app.processEvents()
    assert not wall.tools_overlay.isVisible()
    assert all(not p.overlay.isVisible() for p in wall.panes)
    wall.show_controls()
    assert wall.tools_overlay.isVisible()
    wall.expand(wall.panes[0])
    app.processEvents()
    assert all(not p.overlay.isVisible() for p in wall.panes[1:])
    QTest.keyClick(wall.tools_overlay, Qt.Key.Key_Escape)
    app.processEvents()
    assert not wall.immersive
    assert handles == [int(p.video.winId()) for p in wall.panes]
    assert all(p.chrome.parent() is p for p in wall.panes)
    assert all(not p.overlay.isVisible() for p in wall.panes)
    wall.close()


def test_persistent_worker_reload_stop_and_cleanup(monkeypatch):
    import time
    from PySide6.QtTest import QTest
    from game_stream import app as app_module
    from game_stream.bridge import BrowserRelay
    from game_stream.resolver import Source
    app = QApplication.instance() or QApplication([])
    relays = []

    def resolve(*args, cancel, on_ready, on_status):
        relay = BrowserRelay()
        relays.append(relay)
        on_ready(Source(relay.url, relay=relay))
        cancel.wait(5)
        relay.close()

    def until(predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
        assert predicate()

    monkeypatch.setattr(app_module, 'resolve', resolve)
    wall = Wall(Mock())
    wall.show()
    pane = wall.panes[0]
    pane.url.setText('https://player.example/watch')
    try:
        pane.load()
        until(lambda: pane.relay is not None)
        assert pane.worker.isRunning()
        assert pane.load_button.isEnabled()
        pane.load()  # Cancels the old session before starting another.
        until(lambda: len(relays) == 2 and pane.relay is relays[1])
        assert relays[0].closed.is_set()
        pane.stop()
        until(lambda: pane.worker is None)
        assert pane.relay is None
        assert relays[1].closed.is_set()
    finally:
        pane.stop()
        until(lambda: pane.worker is None)
        wall.close()


def test_audio_number_keys_mute_restore_and_editing():
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent
    from PySide6.QtTest import QTest
    app = QApplication.instance() or QApplication([])
    engine = Mock()
    players = [Mock() for _ in range(4)]
    engine.media_player_new.side_effect = players
    wall = Wall(engine)
    wall.show()
    app.processEvents()
    try:
        for i in range(4):
            QTest.keyClick(wall, Qt.Key.Key_1 + i)
            assert [p.audio.isChecked() for p in wall.panes] == [j == i for j in range(4)]
            players[i].audio_set_mute.assert_called_with(False)
        QTest.keyClick(wall, Qt.Key.Key_2, Qt.KeyboardModifier.KeypadModifier)
        assert wall.panes[1].audio.isChecked()
        QTest.keyClick(wall, Qt.Key.Key_4, Qt.KeyboardModifier.ControlModifier)
        assert wall.panes[1].audio.isChecked()
        QTest.keyClick(wall, Qt.Key.Key_4)
        QTest.keyClick(wall, Qt.Key.Key_0)
        assert not any(p.audio.isChecked() for p in wall.panes)
        QTest.keyClick(wall, Qt.Key.Key_0)
        assert wall.panes[3].audio.isChecked()
        wall.solo.setChecked(False)
        wall.panes[0].audio.setChecked(True)
        QTest.keyClick(wall, Qt.Key.Key_0)
        assert not any(p.audio.isChecked() for p in wall.panes)
        QTest.keyClick(wall, Qt.Key.Key_0)
        assert [p.audio.isChecked() for p in wall.panes] == [True, False, False, False]
        QTest.keyClick(wall, Qt.Key.Key_2)
        assert [p.audio.isChecked() for p in wall.panes] == [False, True, False, False]
        # Auto-repeat must not toggle mute repeatedly when a key is held.
        app.sendEvent(wall, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_0,
                                     Qt.KeyboardModifier.NoModifier, '0', True))
        assert wall.panes[1].audio.isChecked()
        field = wall.panes[0].url
        field.setFocus()
        app.processEvents()
        QTest.keyClicks(field, '012345678')
        assert field.text() == '012345678'
        assert wall.panes[1].audio.isChecked()
        assert wall.expanded is None
        # Shortcut keys for panes removed from the layout have no side effects.
        field.clearFocus()
        wall.count.setCurrentIndex(0)
        wall.select_audio(wall.panes[0])
        QTest.keyClick(wall, Qt.Key.Key_4)
        QTest.keyClick(wall, Qt.Key.Key_8)
        assert wall.panes[0].audio.isChecked()
        assert wall.expanded is None
        assert not wall.isFullScreen()
    finally:
        wall.close()


def test_zoom_number_keys_follow_audio_and_restore_grid():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    app = QApplication.instance() or QApplication([])
    wall = Wall(Mock())
    wall.show()
    app.processEvents()
    try:
        for i in range(4):
            QTest.keyClick(wall, Qt.Key.Key_5 + i)
            app.processEvents()
            assert wall.isFullScreen()
            assert wall.expanded is wall.panes[i]
            assert sum(p.isVisible() for p in wall.panes) == 1
            assert [p.audio.isChecked() for p in wall.panes] == [i == j for j in range(4)]
            # Keys also work when a floating control is the keyboard recipient.
            QTest.keyClick(wall.tools_overlay, Qt.Key.Key_5 + i)
            app.processEvents()
            assert wall.expanded is None
            assert wall.isFullScreen()
            assert all(p.isVisible() for p in wall.panes)
            assert wall.panes[i].audio.isChecked()
        QTest.keyClick(wall, Qt.Key.Key_0)
        assert not any(p.audio.isChecked() for p in wall.panes)
        QTest.keyClick(wall, Qt.Key.Key_0)
        assert wall.panes[3].audio.isChecked()
        QTest.keyClick(wall, Qt.Key.Key_5)
        QTest.keyClick(wall, Qt.Key.Key_6)
        assert wall.expanded is wall.panes[1]
        assert wall.panes[1].audio.isChecked()
        QTest.keyClick(wall, Qt.Key.Key_0)
        QTest.keyClick(wall, Qt.Key.Key_6)
        assert wall.expanded is None
        assert not any(p.audio.isChecked() for p in wall.panes)
        QTest.keyClick(wall, Qt.Key.Key_0)
        assert wall.panes[1].audio.isChecked()
    finally:
        wall.close()


def test_double_click_input_above_native_video_and_audio():
    from PySide6.QtCore import Qt, QPoint
    from PySide6.QtTest import QTest
    app = QApplication.instance() or QApplication([])
    wall = Wall(Mock())
    wall.show()
    wall.toggle_fullscreen()
    app.processEvents()
    try:
        assert wall.video_input.isVisible()
        for count in (2, 4):
            wall.count.setCurrentIndex(count - 1)
            app.processEvents()
            pane = wall.panes[count - 1]
            point = wall.video_input.mapFromGlobal(pane.mapToGlobal(pane.rect().center()))
            QTest.mouseDClick(wall.video_input, Qt.MouseButton.LeftButton, pos=point)
            app.processEvents()
            assert wall.expanded is pane
            assert pane.audio.isChecked()
            assert sum(p.isVisible() for p in wall.panes) == 1
            # Returning to the grid must not switch audio away from this stream.
            QTest.mouseDClick(wall.video_input, Qt.MouseButton.LeftButton,
                             pos=wall.video_input.rect().center())
            app.processEvents()
            assert wall.expanded is None
            assert wall.isFullScreen()
            assert sum(p.isVisible() for p in wall.panes) == count
            assert pane.audio.isChecked()
        wall.restore()
        app.processEvents()
        assert not wall.video_input.isVisible()
    finally:
        wall.close()


def test_two_pane_hover_controls_never_overlap():
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtGui import QCursor
    app = QApplication.instance() or QApplication([])
    wall = Wall(Mock())
    wall.show()
    wall.count.setCurrentIndex(1)
    wall.toggle_fullscreen()
    app.processEvents()
    try:
        for pane in wall.panes[:2]:
            QCursor.setPos(pane.mapToGlobal(pane.rect().center()))
            wall.show_controls()
            app.processEvents()
            assert [p.overlay.isVisible() for p in wall.panes] == [p is pane for p in wall.panes]
            assert not pane.entry.isVisible()
            assert pane.reload_button.isVisible()
            assert pane.overlay.width() <= pane.width() - 12
            bounds = QRect(pane.mapToGlobal(QPoint()), pane.size())
            assert bounds.contains(pane.overlay.geometry()), (bounds, pane.overlay.geometry())
        wall.restore()
        app.processEvents()
        for pane in wall.panes[:2]:
            assert pane.entry.isVisible()
            assert not pane.reload_button.isVisible()
    finally:
        wall.close()
