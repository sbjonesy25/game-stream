"""Exercise Qt controls offscreen; native VLC rendering is a host smoke test."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from unittest.mock import Mock
from PySide6.QtWidgets import QApplication
from streamwall.app import Wall


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
    wall.show_controls()
    assert all(p.overlay.isVisible() for p in wall.panes)
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
    from streamwall import app as app_module
    from streamwall.bridge import BrowserRelay
    from streamwall.resolver import Source
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
