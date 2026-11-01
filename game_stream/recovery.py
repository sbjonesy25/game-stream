"""Qt lifecycle for opt-in restoration and crash-safe periodic checkpoints."""
from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QMessageBox
from .session import SessionStore, SessionError


class SessionRecovery(QObject):
    def __init__(self, wall, store=None):
        super().__init__(wall)
        self.wall = wall
        self.store = store or SessionStore()
        self.ready = False
        self.closing_snapshot = None
        self.last_snapshot = None
        self.save_error = False
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(300)
        self.debounce.timeout.connect(self.checkpoint)
        self.periodic = QTimer(self)
        self.periodic.setInterval(2000)
        self.periodic.timeout.connect(self.checkpoint)
        for pane in wall.panes:
            pane.url.textChanged.connect(self.changed)
            pane.volume.valueChanged.connect(self.changed)
            pane.audio.toggled.connect(self.changed)
        for signal in (wall.count.currentIndexChanged, wall.solo.toggled, wall.browser.toggled):
            signal.connect(self.changed)
        wall.recovery = self

    def start(self, ask=None):
        previous = None
        try:
            previous = self.store.load()
        except (OSError, SessionError):
            QMessageBox.warning(self.wall, 'Session unavailable',
                                'The saved session could not be read. Game Stream will start empty.')
        if previous and any(p['url'] for p in previous['panes']):
            restore = ask(previous) if ask else self.ask_restore(previous)
            if restore:
                self.wall.apply_session(previous)
        self.ready = True
        self.checkpoint(force=True)
        self.periodic.start()

    def ask_restore(self, previous):
        intro = ('The previous session did not close normally.\n\n' if not previous['clean_exit'] else '')
        count = sum(bool(p['url']) for p in previous['panes'])
        answer = QMessageBox.question(self.wall, 'Restore last session?',
            intro + f'Restore your last session ({count} saved links), layout and audio settings?\n\n'
                    'Previously active streams will reconnect. Source browsers may need Play again.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        return answer == QMessageBox.StandardButton.Yes

    def changed(self, *_):
        if self.ready and not self.wall.closing:
            self.debounce.start()

    def write(self, snapshot, clean_exit=False):
        try:
            self.store.save(snapshot, clean_exit=clean_exit)
            if self.save_error:
                self.wall.setWindowTitle('Game Stream')
            self.save_error = False
            self.last_snapshot = snapshot
        except (OSError, SessionError):
            self.save_error = True
            self.wall.setWindowTitle('Game Stream — session could not be saved')

    def checkpoint(self, force=False):
        if not self.ready or self.wall.closing:
            return
        snapshot = self.wall.session_snapshot()
        if force or snapshot != self.last_snapshot:
            self.write(snapshot)

    def before_close(self):
        self.debounce.stop()
        self.periodic.stop()
        if self.ready:
            # Capture active flags before stop() tears down browsers and relays.
            self.closing_snapshot = self.wall.session_snapshot()
            self.write(self.closing_snapshot)
            if self.save_error:
                QMessageBox.warning(self.wall, 'Session not saved',
                    'Game Stream could not save the current session. Check permissions or free disk space. '
                    'The previous successfully saved session has been kept where possible.')

    def after_close(self):
        if self.closing_snapshot is not None:
            self.write(self.closing_snapshot, clean_exit=True)
