import os
import sys
from threading import Event
from time import monotonic

# libVLC embeds into an X11 window; XWayland supports this on Ubuntu Wayland.
os.environ.setdefault('QT_QPA_PLATFORM', 'xcb')
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QPropertyAnimation, QEvent, QPoint, QRect
from PySide6.QtGui import QKeySequence, QShortcut, QCursor, QPainter, QColor
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QSlider, QVBoxLayout, QWidget, QTextEdit, QPlainTextEdit, QAbstractSpinBox)

from .proxy import Relay
from .bridge import BrowserRelay
from .resolver import resolve, validate_url


class FloatingBar(QFrame):
    """A native tool window stays above VLC's embedded native video surface."""
    def __init__(self, wall):
        super().__init__(wall, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.BypassWindowManagerHint)
        self.setObjectName('hoverBar')
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(4, 4, 4, 4)
        self.animation = QPropertyAnimation(self, b'windowOpacity', self)
        self.animation.setDuration(180)
        self.animation.finished.connect(self.faded)
        QShortcut(QKeySequence('F11'), self, activated=wall.toggle_fullscreen)
        QShortcut(QKeySequence('Escape'), self, activated=wall.restore)

    def reveal(self):
        self.animation.stop()
        self.setWindowOpacity(1)
        self.show()
        self.raise_()

    def fade(self):
        if not self.isVisible() or self.animation.state() == QPropertyAnimation.State.Running:
            return
        self.animation.setStartValue(self.windowOpacity())
        self.animation.setEndValue(0.0)
        self.animation.start()

    def faded(self):
        if self.windowOpacity() < 0.01:
            self.hide()


class VideoInputLayer(QWidget):
    """Catch clicks above libVLC's native X11 children without covering video.

    A plain Qt event filter cannot reliably see events sent to VLC's X11 window.
    This transparent input window follows the fullscreen canvas. Override-redirect
    prevents the window manager moving it into the desktop's smaller work area.
    It is hidden whenever another application takes focus.
    """
    def __init__(self, wall):
        super().__init__(wall, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.BypassWindowManagerHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.wall = wall
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet('background: transparent; border: none;')
        QShortcut(QKeySequence('F11'), self, activated=wall.toggle_fullscreen)
        QShortcut(QKeySequence('Escape'), self, activated=wall.restore)

    def paintEvent(self, event):
        # Nonzero alpha also receives input on compositors that pass clicks
        # through fully transparent pixels. This does not reserve video space.
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 1))

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.wall.immersive:
            self.wall.zoom_at(event.globalPosition().toPoint())
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)


class ResolverWorker(QThread):
    resolved = Signal(object)
    failed = Signal(str)
    status = Signal(str)

    def __init__(self, url, cookies, visible, parent):
        super().__init__(parent)
        self.args = (url, cookies, visible)
        self.cancel = Event()

    def run(self):
        try:
            resolve(*self.args, cancel=self.cancel, on_ready=self.resolved.emit,
                    on_status=self.status.emit)
        except Exception as exc:
            if self.cancel.is_set():
                return
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
        self.pending_reload = False
        self.relay = None
        self.player = wall.vlc.media_player_new()
        self.player.audio_set_mute(True)
        self.player.video_set_mouse_input(False)
        self.player.video_set_key_input(False)
        self.setObjectName('pane')
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)
        self.video = QFrame()
        self.video.setStyleSheet('background: #000;')
        self.video.setMinimumSize(240, 135)
        self.video.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.layout.addWidget(self.video, 1)
        self.chrome = QWidget()
        chrome_layout = QVBoxLayout(self.chrome)
        chrome_layout.setContentsMargins(4, 3, 4, 3)
        chrome_layout.setSpacing(2)
        self.layout.addWidget(self.chrome)
        self.overlay = FloatingBar(wall)
        self.entry = QWidget()
        entry = QHBoxLayout(self.entry)
        entry.setContentsMargins(0, 0, 0, 0)
        entry.setSpacing(4)
        entry.addWidget(QLabel(f'{number}'))
        self.full = QPushButton('Expand')
        self.full.clicked.connect(lambda: wall.expand(self))
        self.url = QLineEdit()
        self.url.setPlaceholderText('Paste a page link or direct .m3u8 URL')
        self.url.returnPressed.connect(self.load)
        entry.addWidget(self.url, 1)
        self.load_button = QPushButton('Load')
        self.load_button.clicked.connect(self.load)
        entry.addWidget(self.load_button)
        chrome_layout.addWidget(self.entry)
        controls = QHBoxLayout()
        controls.setSpacing(4)
        pause = QPushButton('Pause')
        pause.setToolTip('Pause / resume this stream')
        pause.clicked.connect(lambda: self.player.pause())
        controls.addWidget(pause)
        stop = QPushButton('Stop')
        stop.clicked.connect(self.stop)
        controls.addWidget(stop)
        self.audio = QPushButton('Audio off')
        self.audio.setCheckable(True)
        self.audio.setToolTip(f'{number}: select this audio · 0: mute / restore last audio')
        self.full.setToolTip(f'{number + 4}: toggle this stream fullscreen · double-click video to zoom / restore')
        self.audio.toggled.connect(self.set_audio)
        controls.addWidget(self.audio)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setFixedWidth(65)
        self.volume.setToolTip('Volume')
        self.volume.valueChanged.connect(self.player.audio_set_volume)
        controls.addWidget(self.volume)
        controls.addWidget(self.full)
        self.reload_button = QPushButton('Reload')
        self.reload_button.clicked.connect(self.load)
        self.reload_button.hide()
        controls.addWidget(self.reload_button)
        self.status = QLabel('Ready — paste a link above')
        self.status.setMinimumWidth(0)
        self.status.setWordWrap(True)
        controls.addWidget(self.status, 1)
        chrome_layout.addLayout(controls)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_state)
        self.timer.start(750)

    def set_immersive(self, enabled):
        self.entry.setVisible(not enabled)
        self.reload_button.setVisible(enabled)
        if enabled and self.chrome.parent() is not self.overlay:
            self.layout.removeWidget(self.chrome)
            self.overlay.box.addWidget(self.chrome)
            self.chrome.show()
        elif not enabled and self.chrome.parent() is self.overlay:
            self.overlay.animation.stop()
            self.overlay.box.removeWidget(self.chrome)
            self.layout.addWidget(self.chrome)
            self.chrome.show()
            self.overlay.hide()

    def set_audio(self, enabled):
        if enabled:
            self.wall.last_audio = self
        if enabled and self.wall.solo.isChecked():
            for pane in self.wall.panes:
                if pane is not self:
                    pane.audio.setChecked(False)
        self.player.audio_set_mute(not enabled)
        self.audio.setText('Audio on' if enabled else 'Audio off')

    def load(self):
        if self.worker is not None:
            self.stop()
            self.pending_reload = True
            self.load_button.setEnabled(False)
            self.status.setText('Restarting source browser…')
            return
        try:
            validate_url(self.url.text().strip())
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.stop()
        self.status.setText('Open the source player and press Play. Waiting for video…')
        self.load_button.setEnabled(False)
        self.worker = ResolverWorker(self.url.text().strip(), self.wall.cookie_path,
                                     self.wall.browser.isChecked(), self)
        self.worker.resolved.connect(self.play)
        self.worker.failed.connect(self.failed)
        self.worker.status.connect(self.status.setText)
        self.worker.finished.connect(self.resolver_finished)
        self.worker.start()

    def resolver_finished(self):
        worker = self.worker
        self.worker = None
        self.load_button.setEnabled(True)
        if worker:
            worker.deleteLater()
        if self.pending_reload and not self.wall.closing:
            self.pending_reload = False
            QTimer.singleShot(0, self.load)

    def failed(self, message):
        self.stop()
        self.status.setText(message)

    def play(self, source):
        if self.worker and self.worker.cancel.is_set():
            return
        try:
            self.relay = source.relay or Relay(source)
            self.player.set_xwindow(int(self.video.winId()))
            media = self.wall.vlc.media_new(self.relay.url)
            media.add_option(':network-caching=1500')
            self.player.set_media(media)
            media.release()
            self.player.audio_set_volume(self.volume.value())
            self.player.audio_set_mute(not self.audio.isChecked())
            self.player.play()
            self.status.setText('Buffering…')
            self.load_button.setEnabled(True)
        except Exception:
            self.stop()
            self.status.setText('Could not start playback. Check VLC installation and stream availability.')

    def check_state(self):
        import vlc
        if not self.relay:
            return
        if isinstance(self.relay, BrowserRelay) and monotonic() - self.relay.updated > 30:
            self.status.setText('Source stalled — check its browser or Load again')
            return
        state = self.player.get_state()
        labels = {vlc.State.Playing: 'Playing', vlc.State.Paused: 'Paused',
                  vlc.State.Buffering: 'Buffering…', vlc.State.Ended: 'Stream ended — Load to reconnect',
                  vlc.State.Error: 'Playback failed — stream may have expired. Try Load or browser mode.'}
        if state in labels:
            self.status.setText(labels[state])

    def stop(self):
        self.pending_reload = False
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
        self.closing = False
        self.immersive = False
        self.last_audio = None
        self.hovered_pane = None
        self.setWindowTitle('Streamwall')
        self.resize(1280, 850)
        self.root = QWidget()
        self.setCentralWidget(self.root)
        self.layout = QVBoxLayout(self.root)
        self.layout.setContentsMargins(4, 4, 4, 4)
        self.layout.setSpacing(2)
        self.tools = QWidget()
        tools = QHBoxLayout(self.tools)
        tools.setContentsMargins(2, 2, 2, 2)
        tools.setSpacing(6)
        tools.addWidget(QLabel('Streamwall'))
        tools.addStretch()
        tools.addWidget(QLabel('Panes'))
        self.count = QComboBox()
        self.count.addItems(['1', '2', '3', '4'])
        self.count.setCurrentIndex(3)
        self.count.currentIndexChanged.connect(self.arrange)
        tools.addWidget(self.count)
        self.solo = QCheckBox('Solo audio')
        self.solo.setChecked(True)
        self.solo.toggled.connect(self.enforce_solo)
        tools.addWidget(self.solo)
        self.browser = QCheckBox('Show browser')
        self.browser.setChecked(True)
        self.browser.setToolTip('Press Play in the browser. Custom player sessions stay running, muted and minimized.')
        tools.addWidget(self.browser)
        self.cookies = QPushButton('Cookies…')
        self.cookies.clicked.connect(self.select_cookies)
        tools.addWidget(self.cookies)
        clear = QPushButton('Clear cookies')
        clear.clicked.connect(self.clear_cookies)
        tools.addWidget(clear)
        full = QPushButton('Fullscreen · F11')
        full.clicked.connect(self.toggle_fullscreen)
        tools.addWidget(full)
        self.layout.addWidget(self.tools)
        self.tools_overlay = FloatingBar(self)
        self.video_input = VideoInputLayer(self)
        self.grid = QGridLayout()
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(2)
        self.layout.addLayout(self.grid, 1)
        self.panes = [Pane(n + 1, self) for n in range(4)]
        self.arrange()
        QShortcut(QKeySequence('F11'), self, activated=self.toggle_fullscreen)
        QShortcut(QKeySequence('Escape'), self, activated=self.restore)
        self.last_pointer = QCursor.pos()
        self.idle = QTimer(self)
        self.idle.setSingleShot(True)
        self.idle.setInterval(2200)
        self.idle.timeout.connect(self.fade_controls)
        self.pointer_timer = QTimer(self)
        self.pointer_timer.timeout.connect(self.poll_pointer)
        self.pointer_timer.start(100)
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, watched, event):
        owned = isinstance(watched, QWidget) and watched.window() in self.owned_windows()
        if owned:
            if event.type() == QEvent.Type.KeyPress and self.handle_number_key(event):
                return True
            if (event.type() == QEvent.Type.MouseButtonDblClick and self.immersive and
                    event.button() == Qt.MouseButton.LeftButton and watched in [p.video for p in self.panes]):
                self.zoom_at(event.globalPosition().toPoint())
                return True
        if owned and self.immersive and event.type() in (QEvent.Type.KeyPress, QEvent.Type.MouseButtonPress,
                                               QEvent.Type.Wheel):
            self.show_controls()
        return super().eventFilter(watched, event)

    def owned_windows(self):
        return [self, self.video_input, self.tools_overlay, *[p.overlay for p in self.panes]]

    def handle_number_key(self, event):
        # Numbers typed into URLs (including the fullscreen toolbar's dialogs)
        # must remain text, and modified keys retain their normal meaning.
        focus = QApplication.focusWidget()
        if (isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)) or
                QApplication.activeModalWidget() or QApplication.activePopupWidget() or
                event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier):
            return False
        key = event.key() - Qt.Key.Key_0
        if not 0 <= key <= 8:
            return False
        if not event.isAutoRepeat():
            if key == 0:
                self.toggle_audio_mute()
            elif key <= 4:
                self.select_audio(self.panes[key - 1])
            else:
                pane = self.panes[key - 5]
                if self.pane_enabled(pane):
                    if not self.isFullScreen():
                        self.showFullScreen()
                    self.expand(pane)
        if self.immersive:
            self.show_controls()
        return True

    def pane_enabled(self, pane):
        return pane in self.panes[:self.count.currentIndex() + 1]

    def select_audio(self, pane):
        if not self.pane_enabled(pane):
            return
        for other in self.panes:
            if other is not pane:
                other.audio.setChecked(False)
        pane.audio.setChecked(True)
        self.last_audio = pane

    def toggle_audio_mute(self):
        audible = [p for p in self.panes if p.audio.isChecked()]
        if audible:
            if self.last_audio not in audible:
                self.last_audio = audible[-1]
            for pane in audible:
                pane.audio.setChecked(False)
        elif self.last_audio is not None:
            self.select_audio(self.last_audio)

    def zoom_at(self, point):
        for pane in self.panes:
            if pane.isVisible() and QRect(pane.mapToGlobal(QPoint()), pane.size()).contains(point):
                self.expand(pane)
                return

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, 'pointer_timer'):
            self.set_immersive(self.isFullScreen())

    def set_immersive(self, enabled):
        if enabled == self.immersive:
            return
        self.immersive = enabled
        margin = 0 if enabled else 4
        self.layout.setContentsMargins(margin, margin, margin, margin)
        if enabled:
            self.layout.removeWidget(self.tools)
            self.tools_overlay.box.addWidget(self.tools)
            self.tools.show()
        else:
            self.video_input.hide()
            self.tools_overlay.animation.stop()
            self.tools_overlay.box.removeWidget(self.tools)
            self.layout.insertWidget(0, self.tools)
            self.tools.show()
            self.tools_overlay.hide()
            self.idle.stop()
            self.root.unsetCursor()
        for pane in self.panes:
            pane.set_immersive(enabled)
            if enabled:
                pane.url.clearFocus()
        if enabled:
            QTimer.singleShot(0, self.show_controls)

    def poll_pointer(self):
        if self.closing:
            return
        point = QCursor.pos()
        if self.immersive and QApplication.activeWindow() not in self.owned_windows():
            self.video_input.hide()
            for bar in [self.tools_overlay, *[p.overlay for p in self.panes]]:
                bar.hide()
            self.last_pointer = point
            return
        if self.immersive and not self.video_input.isVisible():
            self.position_video_input()
        if self.immersive and point != self.last_pointer and self.frameGeometry().contains(point):
            self.show_controls()
        self.last_pointer = point

    def position_video_input(self):
        self.video_input.setGeometry(QRect(self.root.mapToGlobal(QPoint()), self.root.size()))
        self.video_input.show()
        self.video_input.raise_()

    def position_controls(self):
        self.tools_overlay.resize(self.width() - 16, self.tools.sizeHint().height() + 8)
        self.tools_overlay.move(self.root.mapToGlobal(QPoint(8, 8)))
        for pane in self.panes:
            if pane.isVisible():
                # Fullscreen omits the URL row: each HUD fits comfortably within
                # its video, including two-pane views and larger UI font scales.
                width = min(pane.width() - 12, 620)
                height = pane.chrome.sizeHint().height() + 8
                pane.overlay.setFixedWidth(width)
                pane.overlay.resize(width, height)
                position = pane.mapToGlobal(pane.rect().bottomLeft())
                pane.overlay.move(position.x() + (pane.width() - width) // 2, position.y() - height - 5)

    def show_controls(self):
        if not self.immersive or self.closing or QApplication.activeModalWidget():
            return
        self.root.unsetCursor()
        self.video_input.unsetCursor()
        self.position_video_input()
        self.position_controls()
        self.tools_overlay.reveal()
        point = QCursor.pos()
        # Show just the pane under the pointer. Native tool windows can be
        # repositioned by desktop work-area constraints; never let two HUDs
        # obscure one another, even with a dock or fractional display scaling.
        hovered = next((p for p in self.panes if p.overlay.isVisible() and p.overlay.geometry().contains(point)), None)
        if hovered is None:
            hovered = next((p for p in self.panes if p.isVisible() and
                            QRect(p.mapToGlobal(QPoint()), p.size()).contains(point)), None)
        self.hovered_pane = hovered
        for pane in self.panes:
            if pane.isVisible() and pane is hovered:
                pane.overlay.reveal()
            else:
                pane.overlay.hide()
        self.idle.start()

    def fade_controls(self):
        # Key presses restart the idle timer; an open dropdown or held mouse
        # button must finish its interaction before controls disappear.
        if QApplication.activePopupWidget() or QApplication.mouseButtons() != Qt.MouseButton.NoButton:
            self.idle.start()
            return
        self.tools_overlay.fade()
        for pane in self.panes:
            pane.overlay.fade()
        self.root.setCursor(Qt.CursorShape.BlankCursor)
        self.video_input.setCursor(Qt.CursorShape.BlankCursor)

    def enforce_solo(self, checked):
        if checked:
            audible = [pane for pane in self.panes if pane.audio.isChecked()]
            if audible:
                self.select_audio(self.last_audio if self.last_audio in audible else audible[-1])

    def select_cookies(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Select Netscape cookies.txt', '', 'Cookie files (*.txt);;All files (*)')
        if path:
            self.cookie_path = path
            self.cookies.setText('Cookies selected')
            self.cookies.setToolTip('Used on the next Load; cookie values stay in memory.')

    def clear_cookies(self):
        self.cookie_path = ''
        self.cookies.setText('Cookies…')

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
        if self.last_audio is not None and not self.pane_enabled(self.last_audio):
            self.last_audio = None
        for i in range(2):
            self.grid.setRowStretch(i, 1 if i == 0 or count > 2 else 0)
            self.grid.setColumnStretch(i, 1 if i == 0 or count > 1 else 0)
        if self.immersive:
            QTimer.singleShot(0, self.show_controls)

    def expand(self, pane):
        if not self.pane_enabled(pane):
            return
        if self.expanded is pane:
            self.arrange()
            return
        self.select_audio(pane)
        self.expanded = pane
        for other in self.panes:
            self.grid.removeWidget(other)
            other.hide()
        self.grid.addWidget(pane, 0, 0, 2, 2)
        pane.full.setText('Restore grid')
        pane.show()
        if self.immersive:
            QTimer.singleShot(0, self.show_controls)

    def toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def restore(self):
        if self.expanded:
            self.arrange()
        if self.isFullScreen():
            self.showNormal()

    def closeEvent(self, event):
        self.closing = True
        self.video_input.hide()
        self.tools_overlay.hide()
        for pane in self.panes:
            pane.overlay.hide()
        for pane in self.panes:
            pane.stop()
        if any(pane.worker and pane.worker.isRunning() for pane in self.panes):
            event.ignore()
            self.setWindowTitle('Streamwall — closing resolver browsers…')
            QTimer.singleShot(300, self.close)
            return
        for pane in self.panes:
            pane.dispose()
        QApplication.instance().removeEventFilter(self)
        self.pointer_timer.stop()
        self.idle.stop()
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
        QWidget { background: #151b29; color: #e5eaf3; font-size: 12px; }
        QFrame#pane { border: 0; border-radius: 0; }
        QFrame#hoverBar { background: #151b29; border: 1px solid #354159; border-radius: 4px; }
        QLineEdit, QComboBox { background: #0e1420; padding: 3px; border: 1px solid #46546d; }
        QPushButton { background: #293750; padding: 3px 5px; border-radius: 3px; }
        QPushButton:hover { background: #3d5277; }
        QPushButton:checked { background: #236953; }
        QPushButton:disabled { color: #8791a3; }
    ''')
    wall = Wall(instance)
    wall.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
