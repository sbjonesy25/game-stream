"""Shortcut reference for the nonmodal H popup."""
from html import escape
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QScrollArea, QLabel, QPushButton

SHORTCUTS = [
    ('1–4', 'Select only that quadrant’s audio.'),
    ('0', 'Mute all, or restore the last audible quadrant.'),
    ('Double-tap 1–4', 'Zoom that quadrant and select its audio; repeat to restore the grid.'),
    ('5–8', 'Zoom quadrant 1–4 and select its audio; repeat to restore the grid.'),
    ('Alt+1–4', 'Show 1–4 panes. Hidden streams stop; their links remain.'),
    ('Shift+1–3', 'Select the large quadrant in three-pane mode, preserving audio.'),
    ('G', 'Toggle Grid / Large stream + two when three panes are selected.'),
    ('F / F11', 'Enter or leave fullscreen.'),
    ('Esc', 'Restore the grid and leave fullscreen. In this popup, close it.'),
    ('Double-click video', 'In fullscreen, zoom / restore that quadrant and select its audio.'),
    ('H', 'Show / hide this shortcut reference.'),
    ('Enter in URL', 'Load that quadrant’s URL.'),
]


class ShortcutDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent, Qt.WindowType.Tool)
        self.setWindowTitle('Game Stream keyboard shortcuts')
        self.resize(700, 520)
        box = QVBoxLayout(self)
        area = QScrollArea()
        area.setWidgetResizable(True)
        rows = ''.join(f'<tr><td style="padding:6px;white-space:nowrap"><b>{escape(key)}</b></td>'
                       f'<td style="padding:6px">{escape(description)}</td></tr>' for key, description in SHORTCUTS)
        label = QLabel('<table>' + rows + '</table><p>Shortcuts apply while Game Stream has focus. '
                       'Plain keys and Shift+digits remain text in URL editors. Shift+4 is unused.</p>')
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        area.setWidget(label)
        box.addWidget(area)
        close = QPushButton('Close · H / Esc')
        close.clicked.connect(parent.toggle_shortcuts)
        box.addWidget(close)
