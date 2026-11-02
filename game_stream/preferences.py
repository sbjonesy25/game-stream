"""Appearance preferences without a background catalog or site requests."""
from PySide6.QtCore import QObject
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QVBoxLayout,
                              QLineEdit, QCheckBox, QSpinBox, QLabel, QMessageBox,
                              QPushButton, QHBoxLayout, QColorDialog, QComboBox, QTabWidget, QWidget)
from PySide6.QtGui import QColor
from .config import ConfigStore, DEFAULTS


class PreferencesDialog(QDialog):
    def __init__(self, store, current, parent):
        super().__init__(parent)
        self.setWindowTitle('Game Stream Preferences')
        self.resize(620, 510)
        self.store = store
        self.saved = None
        self.update_requested = False
        box = QVBoxLayout(self)
        tabs = QTabWidget()
        playback = QWidget()
        appearance = QWidget()
        updates = QWidget()
        playback_form = QFormLayout(playback)
        form = QFormLayout(appearance)
        updates_box = QVBoxLayout(updates)
        tabs.addTab(playback, 'Playback')
        tabs.addTab(appearance, 'Appearance')
        tabs.addTab(updates, 'Updates')
        box.addWidget(tabs)
        self.browser = QCheckBox('Show the source browser while resolving a stream')
        self.browser.setChecked(current['show_browser'])
        self.solo = QCheckBox('Mute other quadrants when selecting audio')
        self.solo.setChecked(current['solo_audio'])
        playback_form.addRow('Show browser', self.browser)
        playback_form.addRow('Solo audio', self.solo)
        self.quality = QComboBox()
        for label, height in [('720p (lower resource use)', 720), ('1080p', 1080), ('Automatic', 0)]:
            self.quality.addItem(label, height)
        self.quality.setCurrentIndex(self.quality.findData(current['browser_max_height']))
        self.quality.setToolTip('Applies to new browser bridge loads when the provider offers multiple qualities. Direct HLS is unchanged.')
        playback_form.addRow('Browser source quality', self.quality)
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
        self.opacity = QSpinBox()
        self.opacity.setRange(25, 100)
        self.opacity.setSingleStep(5)
        self.opacity.setSuffix(' %')
        self.opacity.setValue(current['overlay_opacity_percent'])
        self.text_size = QSpinBox()
        self.text_size.setRange(9, 24)
        self.text_size.setSuffix(' px')
        self.text_size.setValue(current['button_text_size'])
        self.theme = QComboBox()
        for name in ('default', 'light', 'dark'):
            self.theme.addItem(name.title(), name)
        self.theme.setCurrentIndex(self.theme.findData(current['theme']))
        self.number_labels = QCheckBox('Show quadrant numbers over video')
        self.number_labels.setChecked(current['quadrant_labels_enabled'])
        self.number_size = QSpinBox()
        self.number_size.setRange(10, 64)
        self.number_size.setSuffix(' px')
        self.number_size.setValue(current['quadrant_label_size'])
        self.number_corner = QComboBox()
        for label, corner in [('Top left', 'top-left'), ('Top right', 'top-right'),
                              ('Bottom left', 'bottom-left'), ('Bottom right', 'bottom-right')]:
            self.number_corner.addItem(label, corner)
        self.number_corner.setCurrentIndex(self.number_corner.findData(current['quadrant_label_corner']))
        self.three_layout = QComboBox()
        self.three_layout.addItem('Large stream + two (fullscreen)', 'focus')
        self.three_layout.addItem('Grid', 'grid')
        self.three_layout.setCurrentIndex(self.three_layout.findData(current['three_pane_layout']))
        self.three_primary = QComboBox()
        self.three_primary.addItems(['Quadrant 1', 'Quadrant 2', 'Quadrant 3'])
        self.three_primary.setCurrentIndex(current['three_pane_primary'] - 1)
        playback_form.addRow('Three-pane layout', self.three_layout)
        playback_form.addRow('Large stream', self.three_primary)
        playback_form.addRow('Double-tap 1–4 interval', self.interval)
        form.addRow('Theme', self.theme)
        form.addRow('Audio border (1 pixel)', self.border_enabled)
        form.addRow('Border color', color_row)
        form.addRow('Fullscreen overlays', self.overlays)
        form.addRow('', self.shortcut_overlays)
        form.addRow('Overlay idle duration', self.overlay_delay)
        form.addRow('Overlay opacity', self.opacity)
        form.addRow('Button text size', self.text_size)
        form.addRow('Quadrant numbers', self.number_labels)
        form.addRow('Number size', self.number_size)
        form.addRow('Number corner', self.number_corner)
        hint = QLabel('Alt+1–4: visible pane count. Shift+1–3: large stream in three-pane mode. '
                      'F / F11: fullscreen. Reducing the pane count stops hidden streams; their links are kept.')
        hint.setWordWrap(True)
        playback_form.addRow(hint)
        update_hint = QLabel('Check for a newer version, then choose whether to download and install it. '
                            'Game Stream saves your session, closes, installs and reopens. '
                            'Private-repository updates use your existing GitHub CLI login (gh auth login).')
        update_hint.setWordWrap(True)
        updates_box.addWidget(update_hint)
        self.update_button = QPushButton('Save && check for updates…')
        self.update_button.setEnabled(bool(getattr(parent, 'updater', None)) and parent.update_action.isEnabled())
        self.update_button.clicked.connect(self.check_updates)
        updates_box.addWidget(self.update_button)
        updates_box.addStretch()
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
                overlay_timeout_ms=self.overlay_delay.value(), overlay_opacity_percent=self.opacity.value(),
                button_text_size=self.text_size.value(), solo_audio=self.solo.isChecked(), show_browser=self.browser.isChecked(),
                theme=self.theme.currentData(), browser_max_height=self.quality.currentData(),
                quadrant_labels_enabled=self.number_labels.isChecked(), quadrant_label_size=self.number_size.value(),
                quadrant_label_corner=self.number_corner.currentData()))
        except ValueError as exc:
            QMessageBox.warning(self, 'Preferences not saved', str(exc))
            return False
        except OSError:
            QMessageBox.warning(self, 'Preferences not saved', 'Check the configuration directory permissions and free space.')
            return False
        self.accept()
        return True

    def check_updates(self):
        self.update_requested = True
        if not self.save():
            self.update_requested = False


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
        self.wall.solo.setChecked(value['solo_audio'])
        self.wall.browser.setChecked(value['show_browser'])

    def current_values(self):
        # Session restoration may change these after preferences were loaded.
        return dict(self.preferences, three_pane_layout=self.wall.three_pane_layout,
                    three_pane_primary=self.wall.three_pane_primary,
                    solo_audio=self.wall.solo.isChecked(), show_browser=self.wall.browser.isChecked())

    def save_layout(self):
        value = self.current_values()
        try:
            self.preferences = self.store.save(value)
        except (OSError, ValueError):
            QMessageBox.warning(self.wall, 'Layout not saved',
                'The layout is active, but could not be saved. Check configuration permissions and free space.')

    def open_preferences(self):
        dialog = PreferencesDialog(self.store, self.current_values(), self.wall)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        saved, requested = dialog.saved, dialog.update_requested
        dialog.deleteLater()
        if accepted:
            self.apply(saved)
            if requested:
                # Launch only after the preferences modal has closed.
                self.wall.update_action.trigger()
