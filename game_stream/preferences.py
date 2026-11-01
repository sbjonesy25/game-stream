"""Appearance preferences without a background catalog or site requests."""
from PySide6.QtCore import QObject
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QVBoxLayout,
                              QLineEdit, QCheckBox, QSpinBox, QLabel, QMessageBox,
                              QPushButton, QHBoxLayout, QColorDialog, QComboBox)
from PySide6.QtGui import QColor
from .config import ConfigStore, DEFAULTS


class PreferencesDialog(QDialog):
    def __init__(self, store, current, parent):
        super().__init__(parent)
        self.setWindowTitle('Game Stream Preferences')
        self.resize(620, 240)
        self.store = store
        self.saved = None
        box = QVBoxLayout(self)
        form = QFormLayout()
        self.interval = QSpinBox()
        self.interval.setRange(150, 800)
        self.interval.setSingleStep(50)
        self.interval.setSuffix(' ms')
        self.interval.setValue(current['double_tap_ms'])
        self.border_enabled = QCheckBox('Outline the selected audio source')
        self.border_enabled.setChecked(current['audio_border_enabled'])
        self.border_color = QLineEdit(current['audio_border_color'])
        self.border_color.setMaxLength(7)
        color_row = QHBoxLayout()
        color_row.addWidget(self.border_color)
        choose = QPushButton('Choose…')
        choose.clicked.connect(self.choose_color)
        color_row.addWidget(choose)
        self.overlays = QCheckBox('Show fullscreen controls on mouse movement')
        self.overlays.setChecked(current['overlay_enabled'])
        self.shortcut_overlays = QCheckBox('Also show controls after audio / fullscreen shortcuts')
        self.shortcut_overlays.setChecked(current['overlay_on_shortcuts'])
        self.overlay_delay = QSpinBox()
        self.overlay_delay.setRange(0, 30000)
        self.overlay_delay.setSingleStep(250)
        self.overlay_delay.setSuffix(' ms')
        self.overlay_delay.setSpecialValueText('Keep visible')
        self.overlay_delay.setValue(current['overlay_timeout_ms'])
        self.three_layout = QComboBox()
        self.three_layout.addItem('Large stream + two (fullscreen)', 'focus')
        self.three_layout.addItem('Grid', 'grid')
        self.three_layout.setCurrentIndex(self.three_layout.findData(current['three_pane_layout']))
        self.three_primary = QComboBox()
        self.three_primary.addItems(['Quadrant 1', 'Quadrant 2', 'Quadrant 3'])
        self.three_primary.setCurrentIndex(current['three_pane_primary'] - 1)
        form.addRow('Three-pane layout', self.three_layout)
        form.addRow('Large stream', self.three_primary)
        form.addRow('Double-tap 1–4 interval', self.interval)
        form.addRow('Audio border (1 pixel)', self.border_enabled)
        form.addRow('Border color', color_row)
        form.addRow('Fullscreen overlays', self.overlays)
        form.addRow('', self.shortcut_overlays)
        form.addRow('Overlay idle duration', self.overlay_delay)
        box.addLayout(form)
        hint = QLabel('F or F11 toggles fullscreen. With three panes, Layout chooses the larger stream. Single-stream fullscreen hides the audio border.')
        hint.setWordWrap(True)
        box.addWidget(hint)
        path = QLabel(f'Configuration file: {store.path}')
        path.setWordWrap(True)
        box.addWidget(path)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)

    def choose_color(self):
        color = QColorDialog.getColor(QColor(self.border_color.text()), self, 'Audio source border')
        if color.isValid():
            self.border_color.setText(color.name())

    def save(self):
        try:
            self.saved = self.store.save(dict(three_pane_layout=self.three_layout.currentData(),
                three_pane_primary=self.three_primary.currentIndex() + 1, double_tap_ms=self.interval.value(),
                audio_border_enabled=self.border_enabled.isChecked(), audio_border_color=self.border_color.text(),
                overlay_enabled=self.overlays.isChecked(), overlay_on_shortcuts=self.shortcut_overlays.isChecked(),
                overlay_timeout_ms=self.overlay_delay.value()))
        except ValueError as exc:
            QMessageBox.warning(self, 'Preferences not saved', str(exc))
            return
        except OSError:
            QMessageBox.warning(self, 'Preferences not saved', 'Check the configuration directory permissions and free space.')
            return
        self.accept()


class PreferencesController(QObject):
    def __init__(self, wall, store=None):
        super().__init__(wall)
        self.wall = wall
        self.store = store or ConfigStore()
        self.preferences = dict(DEFAULTS)
        wall.preferences = self
        wall.preferences_button.clicked.connect(self.open_preferences)

    def start(self):
        try:
            self.apply(self.store.load())
            if not self.store.path.exists():
                self.store.save(self.preferences)
        except (OSError, ValueError):
            QMessageBox.warning(self.wall, 'Preferences unavailable',
                'Could not read preferences; using defaults. Open Preferences to save new settings.')

    def apply(self, value):
        self.preferences = value
        self.wall.double_tap_ms = value['double_tap_ms']
        self.wall.apply_view_preferences(value)

    def save_layout(self):
        value = dict(self.preferences, three_pane_layout=self.wall.three_pane_layout,
                     three_pane_primary=self.wall.three_pane_primary)
        try:
            self.preferences = self.store.save(value)
        except (OSError, ValueError):
            QMessageBox.warning(self.wall, 'Layout not saved',
                'The layout is active, but could not be saved. Check configuration permissions and free space.')

    def open_preferences(self):
        dialog = PreferencesDialog(self.store, self.preferences, self.wall)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.apply(dialog.saved)
