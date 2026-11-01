from pathlib import Path
from PySide6.QtGui import QIcon

APP_ID = 'game-stream'
DISPLAY_NAME = 'Game Stream'
ICON_PATH = Path(__file__).with_name('assets') / 'game-stream.png'


def configure_application(app):
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName(DISPLAY_NAME)
    app.setOrganizationName(APP_ID)
    app.setDesktopFileName(APP_ID)
    app.setWindowIcon(QIcon(str(ICON_PATH)))
