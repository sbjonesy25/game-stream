"""Opt-in capture controls; sampling and growing logs stay out of the GUI process."""
import os
from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox
from .config import ConfigStore, write_json
from .debug_capture import CaptureProcess, clear_captures, ensure_root


class DebugController(QObject):
    def __init__(self, wall, capture=None):
        super().__init__(wall)
        self.wall = wall
        self.capture = capture or CaptureProcess()
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.heartbeat)
        wall.debug_capture = self

    def startup(self):
        try:
            enabled = ConfigStore().load()['debug_enabled']
        except (OSError, ValueError):
            enabled = False
        if enabled:
            self.start()

    def start(self):
        try:
            self.capture.start()
            self.heartbeat()
            self.timer.start()
        except (OSError, ValueError):
            self.timer.stop()
            QMessageBox.warning(self.wall, 'Debug capture unavailable',
                'Could not start diagnostics. Check free space and permissions in the debug folder.')

    def heartbeat(self):
        if not self.capture.process or self.capture.process.poll() is not None:
            self.timer.stop()
            return
        wall = self.wall
        data = dict(pid=os.getpid(), closing=wall.closing, fullscreen=wall.immersive,
                    visible_panes=wall.count.currentIndex() + 1,
                    expanded_pane=wall.expanded.number if wall.expanded else 0,
                    solo_audio=wall.solo.isChecked(), show_browser=wall.browser.isChecked(),
                    panes=[dict(number=p.number, audio=p.audio.isChecked(), has_link=bool(p.url.text()),
                                worker_running=bool(p.worker and p.worker.isRunning()),
                                relay_running=p.relay is not None) for p in wall.panes])
        try:
            write_json(self.capture.directory / 'heartbeat.json', data)
        except OSError:
            self.timer.stop()

    def stop(self):
        self.timer.stop()
        try:
            self.capture.stop()
        except OSError:
            pass

    def open_folder(self):
        try:
            root = ensure_root(self.capture.root)
        except (OSError, ValueError):
            QMessageBox.warning(self.wall, 'Debug folder unavailable', 'The debug folder could not be opened.')
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(root)))

    def fresh_capture(self):
        self.start()

    def clear(self):
        answer = QMessageBox.question(self.wall, 'Clear debug captures?',
            'Delete saved Game Stream diagnostic captures? Settings, links and browser cookies are kept. '
            'If recording is active, a new empty capture will start. Captures used by another running instance are kept.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        resume = bool(self.capture.process and self.capture.process.poll() is None)
        self.stop()
        try:
            clear_captures(self.capture.root)
        except (OSError, ValueError):
            QMessageBox.warning(self.wall, 'Debug captures not cleared',
                                'Some captures could not be removed. Check directory permissions.')
        if resume:
            self.start()

    def changed(self, enabled):
        if not enabled:
            self.stop()
            return
        answer = QMessageBox.question(self.wall, 'Restart for startup diagnostics?',
            'Debug mode is saved. Restart now to capture from startup and restore your session? '
            'Source browsers may need Play again. Choose No to begin on your next launch.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes)
        if answer == QMessageBox.StandardButton.Yes:
            self.wall.restart_for_debug()
