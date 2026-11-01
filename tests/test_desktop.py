import configparser
import ctypes
import ctypes.util
from pathlib import Path
import shutil
import subprocess
import time

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from game_stream.branding import APP_ID, ICON_PATH, configure_application
from game_stream.desktop import install_launcher


def test_desktop_identity_icon_sizes_and_repeat_install(tmp_path):
    app = QApplication.instance() or QApplication([])
    configure_application(app)
    assert app.applicationName() == APP_ID
    assert app.desktopFileName() == APP_ID
    assert app.applicationDisplayName() == 'Game Stream'
    assert not app.windowIcon().isNull()
    target = install_launcher(tmp_path, refresh=False)
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(target)
    entry = parser['Desktop Entry']
    assert entry['Name'] == 'Game Stream'
    assert entry['Icon'] == entry['StartupWMClass'] == APP_ID
    assert entry['Terminal'] == 'false'
    for size in (16, 32, 64, 128, 256, 512):
        icon = QImage(str(tmp_path / f'icons/hicolor/{size}x{size}/apps/game-stream.png'))
        assert icon.width() == icon.height() == size
        assert icon.hasAlphaChannel()
    before = target.read_text()
    install_launcher(tmp_path, refresh=False)
    assert target.read_text() == before
    if shutil.which('desktop-file-validate'):
        subprocess.run(['desktop-file-validate', str(target)], check=True, capture_output=True)


def test_launcher_exec_quotes_unusual_checkout_paths(tmp_path):
    # GIO is the actual desktop-entry launcher used by GNOME. Launch a harmless
    # local fixture to verify spaces, percent codes, quotes, dollars and slashes.
    import pytest
    library = ctypes.util.find_library('gio-2.0')
    if not library:
        pytest.skip('GIO is required to test freedesktop command execution')
    root = tmp_path / 'Project % $ ` " \\ with spaces'
    (root / 'scripts').mkdir(parents=True)
    marker = tmp_path / 'launched'
    script = root / 'scripts/run.sh'
    script.write_text('#!/bin/sh\nprintf success > ' + "'" + str(marker) + "'\n")
    script.chmod(0o755)
    desktop = install_launcher(tmp_path / 'share', project=root, refresh=False)
    if shutil.which('desktop-file-validate'):
        subprocess.run(['desktop-file-validate', str(desktop)], check=True, capture_output=True)
    gio = ctypes.CDLL(library)
    gio.g_desktop_app_info_new_from_filename.argtypes = [ctypes.c_char_p]
    gio.g_desktop_app_info_new_from_filename.restype = ctypes.c_void_p
    gio.g_app_info_launch.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    gio.g_app_info_launch.restype = ctypes.c_int
    gio.g_object_unref.argtypes = [ctypes.c_void_p]
    handle = gio.g_desktop_app_info_new_from_filename(str(desktop).encode())
    assert handle, 'GIO could not parse the installed desktop entry'
    try:
        assert gio.g_app_info_launch(handle, None, None, None)
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.read_text() == 'success'
    finally:
        gio.g_object_unref(handle)
