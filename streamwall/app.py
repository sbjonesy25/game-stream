import os
import sys
from threading import Event

# libVLC embeds into an X11 window; XWayland supports this on Ubuntu Wayland.
os.environ.setdefault('QT_QPA_PLATFORM', 'xcb')
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QSlider, QVBoxLayout, QWidget)

from .proxy import Relay
from .resolver import resolve, validate_url


class ResolverWorker(QThread):
    resolved = Signal(object)
    failed = Signal(str)

    def __init__(self, url, cookies, visible, parent):
        super().__init__(parent)
        self.args = (url, cookies, visible)
        self.cancel = Event()

    def run(self):
        try:
            self.resolved.emit(resolve(*self.args, cancel=self.cancel))
        except Exception as exc:
            # Browser exceptions can embed signed URLs; show an actionable summary.
            if isinstance(exc, (ValueError, RuntimeError)):
                self.failed.emit(str(exc))
            else:
                self.failed.emit('Resolution failed. Check the URL, browser installation, network and cookies.')


class Pane(QFrame):
    def __init__(self, number, wall):
        super().__init__()
        self.wall = wall
        self.number = number
        self.worker = None
        self.relay = None
        self.player = wall.vlc.media_player_new()
        self.player.audio_set_mute(True)
        self.setObjectName('pane')
        layout = QVBoxLayout(self)
        title = QHBoxLayout()
        title.addWidget(QLabel(f'STREAM {number}'))
        title.addStretch()
        self.full = QPushButton('Expand')
        self.full.clicked.connect(lambda: wall.expand(self))
        title.addWidget(self.full)
        layout.addLayout(title)
        self.url = QLineEdit()
        self.url.setPlaceholderText('Paste a page link or direct .m3u8 URL')
        self.url.returnPressed.connect(self.load)
        layout.addWidget(self.url)
        self.video = QFrame()
        self.video.setStyleSheet('background: #000;')
        self.video.setMinimumSize(240, 135)
        self.video.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        layout.addWidget(self.video, 1)
        controls = QHBoxLayout()
        self.load_button = QPushButton('Load')
        self.load_button.clicked.connect(self.load)
        controls.addWidget(self.load_button)
        pause = QPushButton('Pause / resume')
        pause.clicked.connect(lambda: self.player.pause())
        controls.addWidget(pause)
        stop = QPushButton('Stop')
        stop.clicked.connect(self.stop)
        controls.addWidget(stop)
        self.audio = QPushButton('Audio off')
        self.audio.setCheckable(True)
        self.audio.toggled.connect(self.set_audio)
        controls.addWidget(self.audio)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setMaximumWidth(100)
        self.volume.setToolTip('Volume')
        self.volume.valueChanged.connect(self.player.audio_set_volume)
        controls.addWidget(self.volume)
        layout.addLayout(controls)
        self.status = QLabel('Ready — paste a link above')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_state)
        self.timer.start(750)

    def set_audio(self, enabled):
        if enabled and self.wall.solo.isChecked():
            for pane in self.wall.panes:
                if pane is not self:
                    pane.audio.setChecked(False)
        self.player.audio_set_mute(not enabled)
        self.audio.setText('Audio on' if enabled else 'Audio off')

    def load(self):
        if self.worker is not None:
            return
        try:
            validate_url(self.url.text().strip())
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.stop()
        self.status.setText('Looking for HLS… browser mode allows login or clicking the player.')
        self.load_button.setEnabled(False)
        self.worker = ResolverWorker(self.url.text().strip(), self.wall.cookie_path,
                                     self.wall.browser.isChecked(), self)
        self.worker.resolved.connect(self.play)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self.resolver_finished)
        self.worker.start()

    def resolver_finished(self):
        worker = self.worker
        self.worker = None
        self.load_button.setEnabled(True)
        if worker:
            worker.deleteLater()

    def failed(self, message):
        self.status.setText(message)

    def play(self, source):
        if self.worker and self.worker.cancel.is_set():
            return
        try:
            self.relay = Relay(source)
            self.player.set_xwindow(int(self.video.winId()))
            media = self.wall.vlc.media_new(self.relay.url)
            media.add_option(':network-caching=1500')
            self.player.set_media(media)
            media.release()
            self.player.audio_set_volume(self.volume.value())
            self.player.audio_set_mute(not self.audio.isChecked())
            self.player.play()
            self.status.setText('Buffering…')
        except Exception:
            self.status.setText('Could not start playback. Check VLC installation and stream availability.')

    def check_state(self):
        import vlc
        if not self.relay:
            return
        state = self.player.get_state()
        labels = {vlc.State.Playing: 'Playing', vlc.State.Paused: 'Paused',
                  vlc.State.Buffering: 'Buffering…', vlc.State.Ended: 'Stream ended — Load to reconnect',
                  vlc.State.Error: 'Playback failed — stream may have expired. Try Load or browser mode.'}
        if state in labels:
            self.status.setText(labels[state])

    def stop(self):
        if self.worker:
            self.worker.cancel.set()
        self.player.stop()
        if self.relay:
            self.relay.close()
            self.relay = None
        self.status.setText('Stopped')

    def dispose(self):
        self.stop()
        self.player.release()


class Wall(QMainWindow):
    def __init__(self, instance):
        super().__init__()
        self.vlc = instance
        self.cookie_path = ''
        self.expanded = None
        self.setWindowTitle('Streamwall')
        self.resize(1280, 850)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        tools = QHBoxLayout()
        tools.addWidget(QLabel('STREAMWALL'))
        tools.addStretch()
        tools.addWidget(QLabel('Panes'))
        self.count = QComboBox()
        self.count.addItems(['1', '2', '3', '4'])
        self.count.setCurrentIndex(3)
        self.count.currentIndexChanged.connect(self.arrange)
        tools.addWidget(self.count)
        self.solo = QCheckBox('One audio source')
        self.solo.setChecked(True)
        self.solo.toggled.connect(self.enforce_solo)
        tools.addWidget(self.solo)
        self.browser = QCheckBox('Show resolver browser')
        self.browser.setToolTip('Use when the player needs interaction or login. Browser closes after discovery.')
        tools.addWidget(self.browser)
        self.cookies = QPushButton('Import cookies…')
        self.cookies.clicked.connect(self.select_cookies)
        tools.addWidget(self.cookies)
        clear = QPushButton('Clear cookies')
        clear.clicked.connect(self.clear_cookies)
        tools.addWidget(clear)
        full = QPushButton('Fullscreen (F11)')
        full.clicked.connect(self.toggle_fullscreen)
        tools.addWidget(full)
        layout.addLayout(tools)
        self.grid = QGridLayout()
        layout.addLayout(self.grid, 1)
        self.panes = [Pane(n + 1, self) for n in range(4)]
        self.arrange()
        QShortcut(QKeySequence('F11'), self, activated=self.toggle_fullscreen)
        QShortcut(QKeySequence('Escape'), self, activated=self.restore)

    def enforce_solo(self, checked):
        if checked:
            audible = [pane for pane in self.panes if pane.audio.isChecked()]
            for pane in audible[1:]:
                pane.audio.setChecked(False)

    def select_cookies(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Select Netscape cookies.txt', '', 'Cookie files (*.txt);;All files (*)')
        if path:
            self.cookie_path = path
            self.cookies.setText('Cookies selected')
            self.cookies.setToolTip('Used on the next Load; cookie values stay in memory.')

    def clear_cookies(self):
        self.cookie_path = ''
        self.cookies.setText('Import cookies…')

    def arrange(self, *_):
        self.expanded = None
        count = self.count.currentIndex() + 1
        for pane in self.panes:
            self.grid.removeWidget(pane)
            pane.hide()
            pane.full.setText('Expand')
        for i, pane in enumerate(self.panes[:count]):
            self.grid.addWidget(pane, i // 2 if count > 2 else 0, i % 2 if count > 1 else 0)
            pane.show()
        for pane in self.panes[count:]:
            pane.stop()  # Hidden streams must not consume bandwidth or play audio.
            pane.audio.setChecked(False)
        for i in range(2):
            self.grid.setRowStretch(i, 1 if i == 0 or count > 2 else 0)
            self.grid.setColumnStretch(i, 1 if i == 0 or count > 1 else 0)

    def expand(self, pane):
        if self.expanded is pane:
            self.arrange()
            return
        self.expanded = pane
        for other in self.panes:
            self.grid.removeWidget(other)
            other.hide()
        self.grid.addWidget(pane, 0, 0, 2, 2)
        pane.full.setText('Restore grid')
        pane.show()

    def toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def restore(self):
        if self.expanded:
            self.arrange()
        if self.isFullScreen():
            self.showNormal()

    def closeEvent(self, event):
        for pane in self.panes:
            pane.stop()
        if any(pane.worker and pane.worker.isRunning() for pane in self.panes):
            event.ignore()
            self.setWindowTitle('Streamwall — closing resolver browsers…')
            QTimer.singleShot(300, self.close)
            return
        for pane in self.panes:
            pane.dispose()
        self.vlc.release()
        event.accept()


def main():
    app = QApplication(sys.argv)
    try:
        import vlc
        instance = vlc.Instance('--no-video-title-show', '--quiet')
        if instance is None:
            raise RuntimeError('libVLC initialization failed')
    except Exception:
        QMessageBox.critical(None, 'VLC required', 'Install VLC first: sudo apt install vlc libvlc5')
        return 1
    app.setStyleSheet('''
        QWidget { background: #151b29; color: #e5eaf3; font-size: 13px; }
        QFrame#pane { border: 1px solid #354159; border-radius: 8px; }
        QLineEdit, QComboBox { background: #0e1420; padding: 8px; border: 1px solid #46546d; }
        QPushButton { background: #293750; padding: 7px; border-radius: 4px; }
        QPushButton:hover { background: #3d5277; }
        QPushButton:checked { background: #236953; }
        QPushButton:disabled { color: #8791a3; }
    ''')
    wall = Wall(instance)
    wall.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
