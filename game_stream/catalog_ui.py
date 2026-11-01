"""Sports menu, asynchronous refresh, and editable preferences."""
from collections import defaultdict
from datetime import datetime
from threading import Event

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QVBoxLayout,
                              QLineEdit, QCheckBox, QSpinBox, QLabel, QMessageBox)
from .config import ConfigStore, DEFAULTS
from .catalog import CatalogCache, CatalogError, fetch_games


class RefreshWorker(QThread):
    games_ready = Signal(object)
    failed = Signal(str)

    def __init__(self, root, browser, cookies, parent):
        super().__init__(parent)
        self.args = (root, browser, cookies)
        self.cancel = Event()

    def run(self):
        try:
            games = fetch_games(*self.args, cancel=self.cancel)
            if not self.cancel.is_set():
                self.games_ready.emit(games)
        except Exception as exc:
            if not self.cancel.is_set():
                self.failed.emit(str(exc) if isinstance(exc, CatalogError) else 'Game list refresh failed. Check Preferences and connectivity.')


class PreferencesDialog(QDialog):
    def __init__(self, store, current, parent):
        super().__init__(parent)
        self.setWindowTitle('Game Stream Preferences')
        self.resize(620, 240)
        self.store = store
        self.saved = None
        box = QVBoxLayout(self)
        form = QFormLayout()
        self.root = QLineEdit(current['root_url'])
        self.root.setMaxLength(16384)
        self.browser = QCheckBox('Use a browser to render the game list')
        self.browser.setChecked(current['catalog_browser'])
        self.refresh_start = QCheckBox('Refresh games when Game Stream starts')
        self.refresh_start.setChecked(current['refresh_on_start'])
        self.interval = QSpinBox()
        self.interval.setRange(150, 800)
        self.interval.setSingleStep(50)
        self.interval.setSuffix(' ms')
        self.interval.setValue(current['double_tap_ms'])
        form.addRow('Sports root page', self.root)
        form.addRow('', self.browser)
        form.addRow('', self.refresh_start)
        form.addRow('Double-tap 1–4 interval', self.interval)
        box.addLayout(form)
        hint = QLabel('Games → sport → game → quadrant loads a stream. Refresh Games updates the list without interrupting playback.')
        hint.setWordWrap(True)
        box.addWidget(hint)
        path = QLabel(f'Configuration file: {store.path}')
        path.setWordWrap(True)
        box.addWidget(path)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)

    def save(self):
        try:
            self.saved = self.store.save(dict(root_url=self.root.text(), catalog_browser=self.browser.isChecked(),
                refresh_on_start=self.refresh_start.isChecked(), double_tap_ms=self.interval.value()))
        except (OSError, ValueError):
            QMessageBox.warning(self, 'Preferences not saved', 'Check the root URL and configuration directory permissions.')
            return
        self.accept()


class CatalogController(QObject):
    def __init__(self, wall, store=None, cache=None):
        super().__init__(wall)
        self.wall = wall
        self.store = store or ConfigStore()
        self.cache = cache or CatalogCache()
        self.preferences = dict(DEFAULTS)
        self.games = []
        self.updated = None
        self.error = ''
        self.worker = None
        self.refresh_after = False
        wall.catalog = self
        wall.games_menu.aboutToShow.connect(self.populate)
        wall.preferences_button.clicked.connect(self.open_preferences)
        self.populate()

    def start(self):
        try:
            self.apply_preferences(self.store.load())
            if not self.store.path.exists():
                self.store.save(self.preferences)
        except (OSError, ValueError):
            self.error = 'Could not read preferences; using defaults. Open Preferences to save new settings.'
        self.games, self.updated = self.cache.load(self.preferences['root_url'])
        self.populate()
        if self.preferences['refresh_on_start']:
            self.refresh()

    def apply_preferences(self, value):
        if value['root_url'] != self.preferences['root_url']:
            self.games, self.updated = self.cache.load(value['root_url'])
        self.preferences = value
        self.wall.double_tap_ms = value['double_tap_ms']

    def open_preferences(self):
        try:
            current = self.store.load()
        except (OSError, ValueError):
            current = self.preferences
        dialog = PreferencesDialog(self.store, current, self.wall)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            changed_root = dialog.saved['root_url'] != self.preferences['root_url']
            self.apply_preferences(dialog.saved)
            self.populate()
            if changed_root:
                if self.worker:
                    self.worker.cancel.set()
                    self.refresh_after = True
                else:
                    self.refresh()

    def refresh(self):
        if self.worker or self.wall.closing:
            return
        try:
            self.apply_preferences(self.store.load())
        except (OSError, ValueError):
            self.failed('Could not read the configuration file. Open Preferences to correct it.')
            return
        self.error = ''
        self.worker = RefreshWorker(self.preferences['root_url'], self.preferences['catalog_browser'],
                                    self.wall.cookie_path, self)
        self.worker.games_ready.connect(self.received)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self.finished)
        self.worker.start()
        self.populate()

    def received(self, games):
        if self.wall.closing or (self.worker and self.worker.args[0] != self.preferences['root_url']):
            return
        self.games = games
        self.error = ''
        try:
            self.updated = self.cache.save(self.preferences['root_url'], games)
        except OSError:
            self.error = 'Games refreshed, but the local cache could not be saved.'
        self.populate()

    def failed(self, message):
        if not self.wall.closing and not (self.worker and self.worker.args[0] != self.preferences['root_url']):
            self.error = message
            self.populate()

    def finished(self):
        worker, self.worker = self.worker, None
        if worker:
            worker.deleteLater()
        if not self.wall.closing:
            self.populate()
            if self.refresh_after:
                self.refresh_after = False
                self.refresh()

    def populate(self):
        menu = self.wall.games_menu
        menu.clear()
        refresh = menu.addAction('Refreshing games…' if self.worker else 'Refresh Games')
        refresh.setEnabled(self.worker is None)
        refresh.triggered.connect(self.refresh)
        menu.addAction('Preferences…', self.open_preferences)
        if self.updated:
            stamp = datetime.fromtimestamp(self.updated).strftime('%b %d, %H:%M')
            menu.addAction(f'Last refresh: {stamp} · {len(self.games)} games').setEnabled(False)
        if self.error:
            menu.addAction(self.error).setEnabled(False)
        menu.addSeparator()
        grouped = defaultdict(list)
        for game in self.games:
            grouped[game.sport].append(game)
        order = ['NFL', 'NBA', 'MLB', 'NHL', 'NCAAF', 'NCAAB', 'WNBA', 'Soccer', 'UFC']
        for sport in sorted(grouped, key=lambda s: (order.index(s) if s in order else len(order), s)):
            sports_menu = menu.addMenu(f'{sport} ({len(grouped[sport])})')
            for game in grouped[sport]:
                event = sports_menu.addMenu(game.title.replace('&', '&&'))
                for i in range(4):
                    action = event.addAction(f'Load in quadrant {i + 1}')
                    action.triggered.connect(lambda checked=False, game=game, i=i: self.load_game(game, i))
        if not grouped:
            menu.addAction('No games yet — choose Refresh Games').setEnabled(False)
        self.wall.games_button.setText('Games (!)' if self.error else 'Games ▾')
        self.wall.games_button.setToolTip(self.error or 'Choose a sport, game and target quadrant')

    def load_game(self, game, index):
        if self.wall.closing or not 0 <= index <= 3:
            return
        if self.wall.count.currentIndex() < index:
            self.wall.count.setCurrentIndex(index)
        if self.wall.expanded and self.wall.expanded is not self.wall.panes[index]:
            self.wall.arrange()
        pane = self.wall.panes[index]
        pane.url.setText(game.url)
        pane.load(auto_play=True)

    def shutdown(self):
        if self.worker:
            self.worker.cancel.set()

    def busy(self):
        return bool(self.worker and self.worker.isRunning())
