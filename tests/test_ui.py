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
