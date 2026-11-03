import os
import sys
from threading import Event
from time import monotonic

# libVLC embeds into an X11 window; XWayland supports this on Ubuntu Wayland.
os.environ.setdefault('QT_QPA_PLATFORM', 'xcb')
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QPropertyAnimation, QVariantAnimation, QEvent, QPoint, QRect, QByteArray
from PySide6.QtGui import QKeySequence, QShortcut, QCursor, QPainter, QColor, QIcon, QPen, QActionGroup, QAction
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QSlider, QVBoxLayout, QWidget, QTextEdit, QPlainTextEdit, QAbstractSpinBox, QMenu)

from .proxy import Relay
from .bridge import BrowserRelay
from .resolver import resolve, validate_url
from .branding import APP_ID, DISPLAY_NAME, ICON_PATH, configure_application
from .config import DEFAULTS
from .flow_layout import FlowLayout
from .themes import PALETTES, stylesheet
from .shortcuts import ShortcutDialog
from .quadrant_labels import NumberBadge, label_font, label_size, label_rect, paint_label


class FloatingBar(QFrame):
    """A native tool window stays above VLC's embedded native video surface."""
    def __init__(self, wall):
        super().__init__(wall, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.BypassWindowManagerHint)
        self.setObjectName('hoverBar')
        self.wall = wall
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
        self.setWindowOpacity(self.wall.overlay_opacity_percent / 100)
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
        if self.wall.audio_border_enabled and not self.wall.single_stream_fullscreen():
            painter.setPen(QPen(QColor(self.wall.audio_border_color), 1))
            for pane in self.wall.panes:
                if pane.isVisible() and pane.audio.isChecked():
                    rect = QRect(self.mapFromGlobal(pane.mapToGlobal(QPoint())), pane.size())
                    painter.drawRect(rect.adjusted(0, 0, -1, -1))
        if self.wall.quadrant_labels_enabled:
            painter.setOpacity(self.wall.number_opacity())
            font = label_font(self.wall.quadrant_label_size)
            size = label_size(font)
            for pane in self.wall.panes:
                if pane.isVisible():
                    video = QRect(self.mapFromGlobal(pane.video.mapToGlobal(QPoint())), pane.video.size())
                    paint_label(painter, label_rect(video, size, self.wall.quadrant_label_corner,
                                self.wall.quadrant_label_offset_x, self.wall.quadrant_label_offset_y), pane.number, font)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.wall.immersive:
            self.wall.zoom_at(event.globalPosition().toPoint())
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)


class ResolverWorker(QThread):
    resolved = Signal(object, int)
    failed = Signal(str, int)
    status = Signal(str, int)

    def __init__(self, url, cookies, visible, parent, auto_play=False, generation=0, max_height=720):
        super().__init__(parent)
        self.args = (url, cookies, visible)
        self.cancel = Event()
        self.auto_play = auto_play
        self.generation = generation
        self.max_height = max_height

    def run(self):
        try:
            resolve(*self.args, cancel=self.cancel,
                    on_ready=lambda source: self.resolved.emit(source, self.generation),
                    on_status=lambda message: self.status.emit(message, self.generation), auto_play=self.auto_play,
                    max_height=self.max_height)
        except Exception as exc:
            if self.cancel.is_set():
                return
            # Browser exceptions can embed signed URLs; show an actionable summary.
            if isinstance(exc, (ValueError, RuntimeError)):
                self.failed.emit(str(exc), self.generation)
            else:
                self.failed.emit('Resolution failed. Check the URL, browser installation, network and cookies.', self.generation)


class Pane(QFrame):
    playback_started = Signal()

    def __init__(self, number, wall):
        super().__init__()
        self.wall = wall
        self.number = number
        self.worker = None
        self.pending_reload = False
        self.pending_auto_play = False
        self._request_generation = 0
        self._pending_request = None
        self.want_playing = False
        self.has_played = False
        self._disposed = False
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
        self.number_badge = NumberBadge(wall, number)
        self.entry = QWidget()
        entry = QHBoxLayout(self.entry)
        entry.setContentsMargins(0, 0, 0, 0)
        entry.setSpacing(4)
        entry.addWidget(QLabel(f'{number}'))
        self.full = QPushButton('Expand')
        self.full.clicked.connect(lambda: wall.expand(self))
        self.url = QLineEdit()
        self.url.setMaxLength(16384)
        self.url.setPlaceholderText('Paste a page link or direct .m3u8 URL')
        self.url.returnPressed.connect(self.load)
        entry.addWidget(self.url, 1)
        self.load_button = QPushButton('Load')
        self.load_button.clicked.connect(self.load)
        entry.addWidget(self.load_button)
        self.paste_button = QPushButton('Paste && Load')
        self.paste_button.setToolTip('Paste the clipboard URL and load it into this quadrant')
        self.paste_button.clicked.connect(self.paste_and_load)
        entry.addWidget(self.paste_button)
        chrome_layout.addWidget(self.entry)
        controls = FlowLayout()
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
        controls.addWidget(self.status)
        chrome_layout.addLayout(controls)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_state)
        self.timer.start(750)
        self.playback_started.connect(self.on_playback_started)
        self._vlc_playing_callback = lambda event: self.playback_started.emit()
        import vlc
        self.player.event_manager().event_attach(vlc.EventType.MediaPlayerPlaying, self._vlc_playing_callback)

    def update_audio_border(self):
        active = self.wall.audio_border_enabled and self.audio.isChecked()
        # Fullscreen draws above the native VLC window without changing its
        # geometry. Windowed mode leaves a one-pixel frame around the pane.
        margin = 1 if active and not self.wall.immersive else 0
        self.layout.setContentsMargins(margin, margin, margin, margin)
        color = self.wall.audio_border_color if active and not self.wall.immersive else PALETTES[self.wall.theme]['background']
        self.setStyleSheet(f'QFrame#pane {{ background-color: {color}; border: 0; }}')

    def sync_audio(self):
        if self._disposed:
            return
        self.player.audio_set_volume(self.volume.value())
        self.player.audio_set_mute(not self.audio.isChecked())

    def on_playback_started(self):
        if not self._disposed:
            self.has_played = True
            self.sync_audio()

    def paste_and_load(self):
        text = QApplication.clipboard().text().strip()
        try:
            validate_url(text)
        except ValueError:
            self.status.setText('Clipboard does not contain a valid HTTP or HTTPS link')
            return
        self.url.setText(text)
        self.load()

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
        self.wall.refresh_audio_borders()

    def load(self, auto_play=False, take_audio=True):
        try:
            validate_url(self.url.text().strip())
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.stop()
        if take_audio:
            self.wall.select_audio(self)
        self.want_playing = True
        self.wall.session_changed()
        request = (self._request_generation, self.url.text().strip(), auto_play)
        if self.worker is not None:
            self.pending_reload = True
            self._pending_request = request
            self.load_button.setEnabled(False)
            self.status.setText('Closing the previous source browser…')
            return
        self.begin_request(request)

    def begin_request(self, request):
        generation, url, auto_play = request
        if (generation != self._request_generation or self.wall.closing or
                not self.want_playing or self.worker is not None):
            return
        self.status.setText('Open the source player and press Play. Waiting for video…')
        self.load_button.setEnabled(False)
        self.worker = ResolverWorker(url, self.wall.cookie_path,
                                     self.wall.browser.isChecked(), self, auto_play=auto_play, generation=generation,
                                     max_height=self.wall.browser_max_height)
        # Bound QWidget slots keep all widget/VLC operations on the GUI thread.
        self.worker.resolved.connect(self.source_ready)
        self.worker.failed.connect(self.source_failed)
        self.worker.status.connect(self.source_status)
        self.worker.finished.connect(self.resolver_finished)
        self.worker.start()

    def source_ready(self, source, generation):
        if generation == self._request_generation:
            self.play(source)

    def source_failed(self, message, generation):
        if generation == self._request_generation:
            self.failed(message)

    def source_status(self, message, generation):
        if generation == self._request_generation:
            self.status.setText(message)

    def resolver_finished(self):
        worker = self.worker
        self.worker = None
        self.load_button.setEnabled(True)
        if worker:
            worker.deleteLater()
        if self.pending_reload and not self.wall.closing:
            self.pending_reload = False
            request, self._pending_request = self._pending_request, None
            if request:
                QTimer.singleShot(0, lambda: self.begin_request(request))

    def failed(self, message):
        self.stop()
        self.status.setText(message)

    def play(self, source):
        if self._disposed or self.wall.closing:
            return
        if self.worker and self.worker.cancel.is_set():
            return
        try:
            relay = source.relay or Relay(source)
            if self.relay is not None and self.relay is not relay:
                self.player.stop()
                self.relay.close()
            self.relay = relay
            self.has_played = False
            self.player.set_xwindow(int(self.video.winId()))
            media = self.wall.vlc.media_new(self.relay.url)
            media.add_option(':network-caching=1500')
            self.player.set_media(media)
            media.release()
            self.player.audio_set_volume(self.volume.value())
            self.player.audio_set_mute(not self.audio.isChecked())
            self.player.play()
            self.sync_audio()
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
        if state == vlc.State.Playing:
            self.sync_audio()
        labels = {vlc.State.Playing: 'Playing', vlc.State.Paused: 'Paused',
                  vlc.State.Buffering: 'Buffering…',
                  vlc.State.Ended: ('Player stopped — Load to reconnect' if self.has_played else
                                    'Player could not start — source handoff failed. Try Load.'),
                  vlc.State.Error: 'Playback failed — stream may have expired. Try Load or browser mode.'}
        if state in labels:
            self.status.setText(labels[state])

    def stop(self):
        if self._disposed:
            return
        self._request_generation += 1
        self._pending_request = None
        self.want_playing = False
        self.pending_reload = False
        if self.worker:
            self.worker.cancel.set()
        self.player.stop()
        if self.relay:
            self.relay.close()
            self.relay = None
        self.status.setText('Stopped')
        self.wall.session_changed()

    def dispose(self):
        if self._disposed:
            return
        self.stop()
        self._disposed = True
        self.timer.stop()
        import vlc
        self.player.event_manager().event_detach(vlc.EventType.MediaPlayerPlaying)
        self.player.release()


class Wall(QMainWindow):
    def __init__(self, instance):
        super().__init__()
        self.vlc = instance
        self.recovery = None
        self.preferences = None
        self.debug_capture = None
        self.restart_requested = False
        self.shortcuts_dialog = None
        self.theme = DEFAULTS['theme']
        self.browser_max_height = DEFAULTS['browser_max_height']
        self.quadrant_labels_enabled = DEFAULTS['quadrant_labels_enabled']
        self.quadrant_label_size = DEFAULTS['quadrant_label_size']
        self.quadrant_label_corner = DEFAULTS['quadrant_label_corner']
        for key in ('quadrant_label_offset_x', 'quadrant_label_offset_y', 'quadrant_label_opacity_percent',
                    'quadrant_label_display', 'quadrant_label_timeout_ms'):
            setattr(self, key, DEFAULTS[key])
        self.number_alpha = 1.0
        self.number_idle = QTimer(self)
        self.number_idle.setSingleShot(True)
        self.number_idle.timeout.connect(self.fade_numbers)
        self.number_fade = QVariantAnimation(self)
        self.number_fade.setDuration(250)
        self.number_fade.valueChanged.connect(self.set_number_alpha)
        self.setStyleSheet(stylesheet(self.theme))
        self.updater = None
        self.double_tap_ms = 350
        self.audio_border_enabled = DEFAULTS['audio_border_enabled']
        self.audio_border_color = DEFAULTS['audio_border_color']
        self.overlay_enabled = DEFAULTS['overlay_enabled']
        self.overlay_on_shortcuts = DEFAULTS['overlay_on_shortcuts']
        self.overlay_timeout_ms = DEFAULTS['overlay_timeout_ms']
        self.overlay_opacity_percent = DEFAULTS['overlay_opacity_percent']
        self.button_text_size = DEFAULTS['button_text_size']
        self.three_pane_layout = DEFAULTS['three_pane_layout']
        self.three_pane_primary = DEFAULTS['three_pane_primary']
        self._number_tap = None
        self.key_clock = monotonic
        self.cookie_path = ''
        self.expanded = None
        self.closing = False
        self.immersive = False
        self.last_audio = None
        self.hovered_pane = None
        self.setWindowTitle(DISPLAY_NAME)
        self.setWindowIcon(QIcon(str(ICON_PATH)))
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
        tools.addWidget(QLabel('Game Stream'))
        self.update_action = QAction('Check for Updates…', self)
        self.preferences_button = QPushButton('Preferences')
        tools.addWidget(self.preferences_button)
        self.layout_button = QPushButton('Layout ▾')
        self.layout_menu = QMenu(self.layout_button)
        self.layout_button.setMenu(self.layout_menu)
        tools.addWidget(self.layout_button)
        layout_group = QActionGroup(self)
        self.layout_actions = {}
        for title, mode in [('Large stream + two (fullscreen)', 'focus'), ('Grid', 'grid')]:
            action = self.layout_menu.addAction(title)
            action.setCheckable(True)
            layout_group.addAction(action)
            action.triggered.connect(lambda checked=False, mode=mode: self.choose_three_layout(mode=mode))
            self.layout_actions[mode] = action
        primary_menu = self.layout_menu.addMenu('Large stream')
        primary_group = QActionGroup(self)
        self.primary_actions = []
        for number in range(1, 4):
            action = primary_menu.addAction(f'Quadrant {number}')
            action.setCheckable(True)
            primary_group.addAction(action)
            action.triggered.connect(lambda checked=False, number=number: self.choose_three_layout(primary=number))
            self.primary_actions.append(action)
        tools.addStretch()
        tools.addWidget(QLabel('Panes'))
        self.count = QComboBox()
        self.count.addItems(['1', '2', '3', '4'])
        self.count.setCurrentIndex(3)
        self.count.currentIndexChanged.connect(self.arrange)
        tools.addWidget(self.count)
        self.solo = QCheckBox('Solo audio', self)
        self.solo.setChecked(True)
        self.solo.toggled.connect(self.enforce_solo)
        self.solo.hide()
        self.browser = QCheckBox('Show browser', self)
        self.browser.setChecked(True)
        self.browser.setToolTip('Press Play in the browser. Custom player sessions stay running, muted and minimized.')
        self.browser.hide()
        full = QPushButton('Fullscreen · F / F11')
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
        self.apply_control_style()
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

    def session_changed(self):
        if self.recovery:
            self.recovery.changed()

    def refresh_audio_borders(self):
        for pane in self.panes:
            pane.update_audio_border()
        self.video_input.update()
        self.reveal_numbers()

    def number_opacity(self):
        return self.number_alpha * self.quadrant_label_opacity_percent / 100

    def set_number_alpha(self, value):
        self.number_alpha = float(value)
        self.video_input.update()
        self.position_number_badges()

    def reveal_numbers(self):
        self.number_idle.stop()
        self.number_fade.stop()
        self.set_number_alpha(1.0)
        if self.quadrant_labels_enabled and self.quadrant_label_display == 'timed' and not self.closing:
            self.number_idle.start(self.quadrant_label_timeout_ms)

    def fade_numbers(self):
        self.number_fade.setStartValue(self.number_alpha)
        self.number_fade.setEndValue(0.0)
        self.number_fade.start()

    def apply_view_preferences(self, value):
        theme_changed = self.theme != value['theme']
        layout_changed = (self.three_pane_layout != value['three_pane_layout'] or
                          self.three_pane_primary != value['three_pane_primary'])
        for key in ('audio_border_enabled', 'audio_border_color', 'overlay_enabled',
                    'overlay_on_shortcuts', 'overlay_timeout_ms', 'three_pane_layout', 'three_pane_primary',
                    'overlay_opacity_percent', 'button_text_size', 'theme', 'browser_max_height',
                    'quadrant_labels_enabled', 'quadrant_label_size', 'quadrant_label_corner',
                    'quadrant_label_offset_x', 'quadrant_label_offset_y', 'quadrant_label_opacity_percent',
                    'quadrant_label_display', 'quadrant_label_timeout_ms'):
            setattr(self, key, value[key])
        if theme_changed:
            self.setStyleSheet(stylesheet(self.theme))
        for pane in self.panes:
            pane.number_badge.configure(self.quadrant_label_size)
        self.idle.setInterval(self.overlay_timeout_ms or 2200)
        if not self.overlay_enabled or self.overlay_timeout_ms == 0:
            self.idle.stop()
        elif self.tools_overlay.isVisible():
            self.idle.start()
        if not self.overlay_enabled:
            self.hide_controls()
            self.root.unsetCursor()
            self.video_input.unsetCursor()
        self.refresh_audio_borders()
        self.apply_control_style()
        if layout_changed:
            self.layout_panes()

    def apply_control_style(self):
        style = f'QPushButton {{ font-size: {self.button_text_size}px; }}'
        self.tools.setStyleSheet(style)
        for pane in self.panes:
            pane.chrome.setStyleSheet(style)
        for bar in [self.tools_overlay, *[p.overlay for p in self.panes]]:
            if bar.isVisible():
                bar.animation.stop()
                bar.setWindowOpacity(self.overlay_opacity_percent / 100)
        if self.immersive:
            QTimer.singleShot(0, self.align_video_input)

    def single_stream_fullscreen(self):
        return self.immersive and (self.expanded is not None or self.count.currentIndex() == 0)

    def toggle_shortcuts(self):
        if self.shortcuts_dialog is None:
            self.shortcuts_dialog = ShortcutDialog(self)
        if self.shortcuts_dialog.isVisible():
            self.shortcuts_dialog.hide()
            self.activateWindow()
            self.setFocus()
        else:
            self.hide_controls()
            self.video_input.hide()
            self.shortcuts_dialog.show()
            self.shortcuts_dialog.raise_()
            self.shortcuts_dialog.activateWindow()

    def choose_three_layout(self, mode=None, primary=None):
        self.expanded = None
        if mode is not None:
            self.three_pane_layout = mode
        if primary is not None:
            self.three_pane_primary = primary
            self.three_pane_layout = 'focus'
        self.layout_panes()
        if self.preferences:
            self.preferences.save_layout()

    def session_snapshot(self):
        return dict(version=1, pane_count=self.count.currentIndex() + 1,
            panes=[dict(url=p.url.text(), volume=p.volume.value(), audio=p.audio.isChecked(),
                        active=p.want_playing) for p in self.panes],
            solo_audio=self.solo.isChecked(), show_browser=self.browser.isChecked(),
            last_audio=self.panes.index(self.last_audio) if self.last_audio else None,
            expanded=self.panes.index(self.expanded) if self.expanded else None,
            fullscreen=self.isFullScreen(), geometry=bytes(self.saveGeometry().toBase64()).decode('ascii'),
            cookie_file=self.cookie_path)

    def apply_session(self, snapshot):
        from pathlib import Path
        self.count.setCurrentIndex(snapshot['pane_count'] - 1)
        self.solo.setChecked(False)
        self.browser.setChecked(snapshot['show_browser'])
        for pane, row in zip(self.panes, snapshot['panes']):
            pane.url.setText(row['url'])
            pane.volume.setValue(row['volume'])
            pane.audio.setChecked(row['audio'])
        self.last_audio = self.panes[snapshot['last_audio']] if snapshot['last_audio'] is not None else None
        self.solo.setChecked(snapshot['solo_audio'])
        if snapshot['cookie_file'] and Path(snapshot['cookie_file']).is_file():
            self.cookie_path = snapshot['cookie_file']
        if snapshot['geometry']:
            self.restoreGeometry(QByteArray.fromBase64(snapshot['geometry'].encode('ascii')))
        if snapshot['fullscreen']:
            self.showFullScreen()
        elif self.isFullScreen():
            self.showNormal()
        if snapshot['expanded'] is not None:
            self.expand(self.panes[snapshot['expanded']], select_audio=False)
        for pane, row in zip(self.panes[:snapshot['pane_count']], snapshot['panes']):
            if row['active'] and row['url'].strip():
                pane.load(take_audio=False)

    def eventFilter(self, watched, event):
        if watched in (self, self.root) and event.type() in (QEvent.Type.Move, QEvent.Type.Resize):
            QTimer.singleShot(0, self.position_number_badges)
        if watched is self.root and self.immersive and event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
            QTimer.singleShot(0, self.align_video_input)
        owned = isinstance(watched, QWidget) and watched.window() in self.owned_windows()
        if owned:
            if event.type() == QEvent.Type.KeyPress and self.handle_shortcut_key(event, watched):
                return True
            if (event.type() == QEvent.Type.MouseButtonDblClick and self.immersive and
                    event.button() == Qt.MouseButton.LeftButton and watched in [p.video for p in self.panes]):
                self.zoom_at(event.globalPosition().toPoint())
                return True
        if owned and self.immersive:
            if event.type() == QEvent.Type.KeyPress:
                self.show_controls(reason='shortcut')
            elif event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.Wheel) and watched is not self.video_input:
                self.show_controls()
        return super().eventFilter(watched, event)

    def owned_windows(self):
        return [window for window in [self, self.video_input, self.tools_overlay,
                self.shortcuts_dialog, *[p.overlay for p in self.panes],
                *[p.number_badge for p in self.panes]] if window is not None]

    def handle_shortcut_key(self, event, receiver=None):
        # Numbers typed into URLs (including the fullscreen toolbar's dialogs)
        # must remain text, and modified keys retain their normal meaning.
        focus = QApplication.focusWidget()
        editors = (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)
        if QApplication.activeModalWidget() or QApplication.activePopupWidget():
            self._number_tap = None
            return False
        modifiers = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        digit = event.key() - Qt.Key.Key_0
        if modifiers == Qt.KeyboardModifier.AltModifier and 1 <= digit <= 4:
            self._number_tap = None
            if not event.isAutoRepeat():
                if self.count.currentIndex() == digit - 1:
                    self.arrange()
                else:
                    self.count.setCurrentIndex(digit - 1)
            if self.immersive:
                self.show_controls(reason='shortcut')
            return True
        if isinstance(focus, editors) or isinstance(receiver, editors):
            self._number_tap = None
            return False
        shifted = {Qt.Key.Key_Exclam: 1, Qt.Key.Key_At: 2, Qt.Key.Key_NumberSign: 3, Qt.Key.Key_Dollar: 4}
        shifted_digit = shifted.get(event.key(), digit)
        if modifiers == Qt.KeyboardModifier.ShiftModifier and 1 <= shifted_digit <= 4:
            self._number_tap = None
            if not event.isAutoRepeat() and shifted_digit <= 3 and self.count.currentIndex() == 2:
                self.choose_three_layout(primary=shifted_digit)
                if self.immersive:
                    self.show_controls(reason='shortcut')
            return True
        if modifiers:
            self._number_tap = None
            return False
        if event.key() in (Qt.Key.Key_G, Qt.Key.Key_H):
            self._number_tap = None
            if not event.isAutoRepeat():
                if event.key() == Qt.Key.Key_H:
                    self.toggle_shortcuts()
                elif self.count.currentIndex() == 2:
                    self.choose_three_layout(mode='grid' if self.three_pane_layout == 'focus' else 'focus')
            return True
        if event.key() == Qt.Key.Key_F:
            self._number_tap = None
            if not event.isAutoRepeat():
                self.toggle_fullscreen()
            return True
        key = event.key() - Qt.Key.Key_0
        if not 0 <= key <= 8:
            self._number_tap = None
            return False
        if not event.isAutoRepeat():
            if key == 0:
                self._number_tap = None
                self.toggle_audio_mute()
            elif key <= 4:
                pane = self.panes[key - 1]
                audio_before = [p.audio.isChecked() for p in self.panes]
                last_before = self.last_audio
                self.select_audio(pane)
                now = self.key_clock()
                previous = self._number_tap
                self._number_tap = (key, now, audio_before, last_before) if self.pane_enabled(pane) else None
                if (self.pane_enabled(pane) and previous and previous[0] == key and
                        0 <= now - previous[1] <= self.double_tap_ms / 1000):
                    self._number_tap = None
                    # The first tap acts immediately. Roll back that tentative
                    # audio choice before the zoom action so zoom-out preserves
                    # mute/last audio exactly like keys 5–8.
                    for p in self.panes:
                        p.audio.setChecked(False)
                    for p, enabled in zip(self.panes, previous[2]):
                        if enabled and self.pane_enabled(p):
                            p.audio.setChecked(True)
                    self.last_audio = previous[3]
                    self.zoom_shortcut(pane)
            else:
                self._number_tap = None
                self.zoom_shortcut(self.panes[key - 5])
        if self.immersive:
            self.show_controls(reason='shortcut')
        return True

    def zoom_shortcut(self, pane):
        if self.pane_enabled(pane):
            if not self.isFullScreen():
                self.showFullScreen()
            self.expand(pane)

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
        self.layout_panes()
        if enabled:
            self.position_video_input()
            QTimer.singleShot(0, lambda: self.show_controls(reason='view'))

    def poll_pointer(self):
        if self.closing:
            return
        self.position_number_badges()
        point = QCursor.pos()
        if (point != self.last_pointer and self.frameGeometry().contains(point) and
                QApplication.activeWindow() in self.owned_windows()):
            self.reveal_numbers()
        if self.shortcuts_dialog and self.shortcuts_dialog.isVisible():
            self.video_input.hide()
            self.hide_controls()
            self.last_pointer = point
            return
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

    def position_number_badges(self):
        visible = (self.quadrant_labels_enabled and self.number_opacity() > 0 and not self.immersive and not self.closing and
                   not self.isMinimized() and QApplication.activeWindow() in self.owned_windows() and
                   not QApplication.activeModalWidget() and not QApplication.activePopupWidget() and
                   not (self.shortcuts_dialog and self.shortcuts_dialog.isVisible()))
        for pane in self.panes:
            badge = pane.number_badge
            badge.set_opacity(self.number_opacity())
            if not visible or not pane.isVisible():
                badge.hide()
                continue
            rect = label_rect(QRect(pane.video.mapToGlobal(QPoint()), pane.video.size()),
                              badge.size(), self.quadrant_label_corner,
                              self.quadrant_label_offset_x, self.quadrant_label_offset_y)
            if badge.geometry() != rect:
                badge.setGeometry(rect)
            if not badge.isVisible():
                badge.show()
                badge.raise_()

    def position_video_input(self):
        if self.shortcuts_dialog and self.shortcuts_dialog.isVisible():
            self.video_input.hide()
            return
        target = self.root.mapToGlobal(QPoint())
        self.video_input.setGeometry(QRect(target, self.root.size()))
        self.video_input.show()
        offset = self.video_input.mapToGlobal(QPoint()) - target
        if not offset.isNull():
            self.video_input.move(self.video_input.pos() - offset)
        self.video_input.raise_()

    def align_video_input(self):
        if not self.immersive or self.closing:
            return
        if self.video_input.isVisible() or QApplication.activeWindow() in self.owned_windows():
            self.position_video_input()
            self.position_controls()
            for bar in [self.tools_overlay, *[p.overlay for p in self.panes]]:
                if bar.isVisible():
                    bar.raise_()

    def position_controls(self):
        self.tools_overlay.resize(self.width() - 16, self.tools.sizeHint().height() + 8)
        self.tools_overlay.move(self.root.mapToGlobal(QPoint(8, 8)))
        for pane in self.panes:
            if pane.isVisible():
                # Fullscreen omits the URL row: each HUD fits comfortably within
                # its video, including two-pane views and larger UI font scales.
                width = min(pane.width() - 12, 620)
                pane.overlay.setFixedWidth(width)
                height = pane.chrome.heightForWidth(width - 8) if pane.chrome.hasHeightForWidth() else pane.chrome.sizeHint().height()
                height += 8
                pane.overlay.resize(width, height)
                position = pane.mapToGlobal(pane.rect().bottomLeft())
                pane.overlay.move(position.x() + (pane.width() - width) // 2, position.y() - height - 5)

    def hide_controls(self):
        self.idle.stop()
        for bar in [self.tools_overlay, *[p.overlay for p in self.panes]]:
            bar.animation.stop()
            bar.hide()

    def show_controls(self, reason='pointer'):
        self.reveal_numbers()
        if (not self.immersive or self.closing or QApplication.activeModalWidget() or
                (self.shortcuts_dialog and self.shortcuts_dialog.isVisible())):
            return
        if not self.overlay_enabled:
            self.hide_controls()
            self.root.unsetCursor()
            self.video_input.unsetCursor()
            return
        if reason in ('shortcut', 'view') and not self.overlay_on_shortcuts:
            return
        self.root.unsetCursor()
        self.video_input.unsetCursor()
        self.position_video_input()
        self.position_controls()
        self.tools_overlay.reveal()
        point = QCursor.pos()
        self.last_pointer = point
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
        if self.overlay_timeout_ms:
            self.idle.start()
        else:
            self.idle.stop()

    def fade_controls(self):
        # Key presses restart the idle timer; an open dropdown or held mouse
        # button must finish its interaction before controls disappear.
        if QApplication.activePopupWidget() or QApplication.mouseButtons() != Qt.MouseButton.NoButton:
            if self.overlay_timeout_ms:
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

    def arrange(self, *_):
        self._number_tap = None
        self.expanded = None
        count = self.count.currentIndex() + 1
        self.layout_panes()
        for pane in self.panes[count:]:
            pane.stop()  # Hidden streams must not consume bandwidth or play audio.
            pane.audio.setChecked(False)
        if self.last_audio is not None and not self.pane_enabled(self.last_audio):
            self.last_audio = None

    def layout_panes(self):
        count = self.count.currentIndex() + 1
        self.layout_button.setVisible(count == 3)
        for mode, action in self.layout_actions.items():
            action.setChecked(self.three_pane_layout == mode)
        for i, action in enumerate(self.primary_actions, 1):
            action.setChecked(self.three_pane_primary == i)
        for pane in self.panes:
            self.grid.removeWidget(pane)
            pane.hide()
            pane.full.setText('Expand')
        focus = count == 3 and self.immersive and self.three_pane_layout == 'focus'
        if self.expanded:
            self.grid.addWidget(self.expanded, 0, 0, 2, 2)
            self.expanded.full.setText('Restore grid')
            self.expanded.show()
        elif focus:
            primary = self.panes[self.three_pane_primary - 1]
            self.grid.addWidget(primary, 0, 0, 2, 1)
            primary.show()
            others = [p for p in self.panes[:3] if p is not primary]
            for row, pane in enumerate(others):
                self.grid.addWidget(pane, row, 1)
                pane.show()
        else:
            for i, pane in enumerate(self.panes[:count]):
                self.grid.addWidget(pane, i // 2 if count > 2 else 0, i % 2 if count > 1 else 0)
                pane.show()
        for i in range(2):
            self.grid.setRowStretch(i, 1 if i == 0 or count > 2 else 0)
            self.grid.setColumnStretch(i, (2 if i == 0 else 1) if focus and not self.expanded else
                                       (1 if i == 0 or count > 1 else 0))
        self.refresh_audio_borders()
        if self.immersive:
            QTimer.singleShot(0, lambda: self.show_controls(reason='view'))

    def expand(self, pane, select_audio=True):
        if not self.pane_enabled(pane):
            return
        if self.expanded is pane:
            self.arrange()
            return
        if select_audio:
            self.select_audio(pane)
        self.expanded = pane
        self.layout_panes()

    def toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def restore(self):
        if self.shortcuts_dialog and self.shortcuts_dialog.isVisible():
            self.toggle_shortcuts()
            return
        if self.expanded:
            self.arrange()
        if self.isFullScreen():
            self.showNormal()

    def closeEvent(self, event):
        if not self.closing and self.recovery:
            self.recovery.before_close()
        self.closing = True
        if self.shortcuts_dialog:
            self.shortcuts_dialog.hide()
        if self.updater:
            self.updater.shutdown()
        self.video_input.hide()
        self.tools_overlay.hide()
        for pane in self.panes:
            pane.overlay.hide()
            pane.number_badge.hide()
        for pane in self.panes:
            pane.stop()
        if (any(pane.worker and pane.worker.isRunning() for pane in self.panes) or
                (self.updater and self.updater.busy())):
            event.ignore()
            self.setWindowTitle('Game Stream — closing source browsers…')
            QTimer.singleShot(300, self.close)
            return
        for pane in self.panes:
            pane.dispose()
        QApplication.instance().removeEventFilter(self)
        self.pointer_timer.stop()
        self.idle.stop()
        self.number_idle.stop()
        self.number_fade.stop()
        if self.recovery:
            self.recovery.after_close()
        self.vlc.release()
        event.accept()

    def restart_for_debug(self):
        if self.recovery:
            self.recovery.checkpoint(force=True)
            if self.recovery.save_error:
                QMessageBox.warning(self, 'Restart cancelled', 'The session could not be saved. Check free space and permissions.')
                return
        self.restart_requested = True
        self.close()


def main():
    os.environ.setdefault('RESOURCE_NAME', APP_ID)
    restore_flags = ('--restore-after-update', '--restore-after-restart')
    restore_after_update = any(flag in sys.argv[1:] for flag in restore_flags)
    arguments = [a for a in sys.argv[1:] if a not in restore_flags]
    app = QApplication([sys.argv[0], '-name', APP_ID, *arguments])
    configure_application(app)
    try:
        import vlc
        instance = vlc.Instance('--no-video-title-show', '--quiet')
        if instance is None:
            raise RuntimeError('libVLC initialization failed')
    except Exception:
        QMessageBox.critical(None, 'VLC required', 'Install VLC first: sudo apt install vlc libvlc5')
        return 1
    wall = Wall(instance)
    from .debug_ui import DebugController
    debug = DebugController(wall)
    debug.startup()
    wall.show()
    from .recovery import SessionRecovery
    recovery = SessionRecovery(wall)
    from .preferences import PreferencesController
    preferences = PreferencesController(wall)
    from .update_ui import UpdateController
    updater = UpdateController(wall)
    def startup():
        preferences.start()
        recovery.start(ask=(lambda previous: True) if restore_after_update else None)
    QTimer.singleShot(0, startup)
    code = app.exec()
    debug.stop()
    if wall.restart_requested and not recovery.save_error:
        from .debug_capture import module_command
        import subprocess
        try:
            subprocess.Popen(module_command('game_stream.app', '--restore-after-restart'),
                             stdin=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            QMessageBox.warning(None, 'Restart unavailable',
                                'Your session is saved. Open Game Stream again to begin startup diagnostics.')
    return code


if __name__ == '__main__':
    sys.exit(main())
