"""User initiated update checks and verified-download handoff."""
from pathlib import Path
import shutil
from threading import Event

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QMessageBox, QProgressDialog
from . import update


class UpdateWorker(QThread):
    ready = Signal(object)
    failed = Signal(str)
    progress = Signal(int)

    def __init__(self, parent, release=None):
        super().__init__(parent)
        self.release = release
        self.cancel = Event()

    def run(self):
        try:
            result = (update.fetch_release(self.cancel) if self.release is None else
                      update.download_release(self.release, self.cancel, self.progress.emit))
            self.ready.emit(result)
        except Exception as exc:
            if not self.cancel.is_set():
                self.failed.emit(str(exc) if isinstance(exc, update.UpdateError) else
                                 'Update could not finish. Check internet access, disk space and permissions.')


class UpdateController(QObject):
    def __init__(self, wall):
        super().__init__(wall)
        self.wall = wall
        self.worker = None
        self.dialog = None
        self.current = None
        self.result = None
        self.error = None
        wall.updater = self
        wall.update_action.triggered.connect(self.check)

    def check(self):
        if self.worker or self.wall.closing:
            return
        try:
            self.current = update.installed_version()
        except (update.UpdateError, OSError, ValueError) as exc:
            QMessageBox.information(self.wall, 'Game Stream updates', str(exc))
            return
        self.start_worker()

    def start_worker(self, release=None):
        self.result = self.error = None
        self.wall.update_action.setEnabled(False)
        self.dialog = QProgressDialog('Downloading and verifying the update…' if release else 'Checking GitHub for updates…',
                                      'Cancel', 0, 100 if release else 0, self.wall)
        self.dialog.setWindowTitle('Game Stream update')
        self.dialog.setAutoClose(False)
        self.dialog.setAutoReset(False)
        self.dialog.setMinimumDuration(0)
        self.worker = UpdateWorker(self, release)
        self.dialog.canceled.connect(self.cancel)
        self.worker.ready.connect(self.received)
        self.worker.failed.connect(self.failed)
        self.worker.progress.connect(self.progress)
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def progress(self, value):
        if self.dialog and not self.worker.cancel.is_set():
            self.dialog.setValue(value)

    def received(self, result):
        self.result = result

    def handle_result(self, result, cancelled):
        if isinstance(result, Path):
            if cancelled:
                shutil.rmtree(result)
                return
            try:
                if self.wall.recovery:
                    self.wall.recovery.checkpoint(force=True)
                    if self.wall.recovery.save_error:
                        raise update.UpdateError('Your session could not be saved. Correct the save error before updating.')
                update.handoff(result)
            except (OSError, update.UpdateError) as exc:
                shutil.rmtree(result)
                QMessageBox.warning(self.wall, 'Game Stream update', str(exc))
                return
            self.wall.close()
        elif not cancelled:
            if update.version_tuple(result['version']) <= update.version_tuple(self.current):
                QMessageBox.information(self.wall, 'Game Stream is up to date', f'Installed version: {self.current}')
            elif QMessageBox.question(self.wall, 'Update Game Stream?',
                    f'Version {result["version"]} is available (installed: {self.current}).\n\n'
                    'Download the update, save this session, close Game Stream and install it? '
                    'Ubuntu will request administrator authorization. Game Stream will reopen with this session afterward.',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes) == QMessageBox.StandardButton.Yes:
                self.start_worker(result)

    def failed(self, message):
        self.error = message

    def close_dialog(self):
        if self.dialog:
            self.dialog.close()
            self.dialog.deleteLater()
            self.dialog = None

    def finished(self):
        worker = self.worker
        cancelled = self.wall.closing or worker.cancel.is_set()
        result, error = self.result, self.error
        self.close_dialog()
        self.worker = None
        if worker:
            worker.deleteLater()
        self.wall.update_action.setEnabled(True)
        if result is not None:
            self.handle_result(result, cancelled)
        elif error and not cancelled:
            QMessageBox.warning(self.wall, 'Game Stream update', error)

    def cancel(self):
        if self.worker:
            self.worker.cancel.set()

    def shutdown(self):
        self.cancel()
        self.close_dialog()

    def busy(self):
        return self.worker is not None
