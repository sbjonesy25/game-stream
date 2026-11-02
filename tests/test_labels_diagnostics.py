import importlib.util
import json
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from game_stream.app import Wall
from game_stream.config import ConfigStore, DEFAULTS
from game_stream.preferences import PreferencesDialog
from game_stream.quadrant_labels import CORNERS, label_font, label_size, label_rect
from game_stream.resources import memory_snapshot, diagnostic_metadata


def settle(app):
    app.processEvents()
    QTest.qWait(15)
    app.processEvents()


def test_number_badges_use_each_corner_and_preserve_native_video_geometry(monkeypatch):
    app = QApplication.instance() or QApplication([])
    wall = Wall(Mock())
    wall.show()
    monkeypatch.setattr(QApplication, 'activeWindow', lambda: wall)
    settle(app)
    wall.pointer_timer.stop()
    handles = [int(p.video.winId()) for p in wall.panes]
    identities = [id(p.number_badge) for p in wall.panes]
    try:
        before = [p.video.geometry() for p in wall.panes]
        for corner in CORNERS:
            for size in (10, 64):
                wall.apply_view_preferences({**DEFAULTS, 'quadrant_label_corner': corner, 'quadrant_label_size': size})
                settle(app)
                wall.position_number_badges()
                for pane in wall.panes:
                    video = QRect(pane.video.mapToGlobal(QPoint()), pane.video.size())
                    badge = pane.number_badge
                    assert badge.isVisible()
                    assert badge.geometry() == label_rect(video, label_size(label_font(size)), corner)
                    assert video.contains(badge.geometry())
                    assert badge.windowFlags() & Qt.WindowType.WindowTransparentForInput
                assert [p.video.geometry() for p in wall.panes] == before
        assert [int(p.video.winId()) for p in wall.panes] == handles
        assert [id(p.number_badge) for p in wall.panes] == identities
        wall.count.setCurrentIndex(1)
        settle(app)
        wall.position_number_badges()
        assert [p.number_badge.isVisible() for p in wall.panes] == [True, True, False, False]
        monkeypatch.setattr(QApplication, 'activeWindow', lambda: None)
        wall.position_number_badges()
        assert not any(p.number_badge.isVisible() for p in wall.panes)
    finally:
        wall.close()


def test_fullscreen_labels_remain_after_controls_hide_and_retain_original_number(monkeypatch):
    import game_stream.app as module
    app = QApplication.instance() or QApplication([])
    wall = Wall(Mock())
    wall.show()
    wall.toggle_fullscreen()
    settle(app)
    wall.pointer_timer.stop()
    seen = []
    original = module.paint_label
    def paint(painter, rect, number, font):
        seen.append(number)
        return original(painter, rect, number, font)
    monkeypatch.setattr(module, 'paint_label', paint)
    try:
        wall.apply_view_preferences({**DEFAULTS, 'overlay_enabled': False})
        wall.position_video_input()
        image = wall.video_input.grab().toImage()
        assert set(seen) == {1, 2, 3, 4}
        assert not wall.tools_overlay.isVisible()
        assert not any(p.number_badge.isVisible() for p in wall.panes)
        rect = label_rect(QRect(wall.video_input.mapFromGlobal(wall.panes[0].video.mapToGlobal(QPoint())),
                                wall.panes[0].video.size()), label_size(label_font(20)), 'top-left')
        assert any(image.pixelColor(x, y).red() > 200 and image.pixelColor(x, y).alpha() > 100
                   for x in range(rect.left(), rect.right()) for y in range(rect.top(), rect.bottom()))
        wall.expand(wall.panes[2])
        settle(app)
        seen.clear()
        wall.video_input.grab()
        assert set(seen) == {3}
        wall.apply_view_preferences({**DEFAULTS, 'quadrant_labels_enabled': False})
        seen.clear()
        wall.video_input.grab()
        assert not seen
    finally:
        wall.close()


def test_number_preferences_persist_and_validate(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / 'config.json')
    dialog = PreferencesDialog(store, DEFAULTS, None)
    dialog.number_size.setValue(34)
    dialog.number_corner.setCurrentIndex(dialog.number_corner.findData('bottom-right'))
    dialog.number_labels.setChecked(False)
    dialog.save()
    value = store.load()
    assert value['quadrant_label_size'] == 34 and value['quadrant_label_corner'] == 'bottom-right'
    assert value['quadrant_labels_enabled'] is False
    for invalid in ({'quadrant_label_size': 65}, {'quadrant_label_corner': 'center'}, {'quadrant_labels_enabled': 'yes'}):
        with pytest.raises(ValueError):
            store.save({**DEFAULTS, **invalid})


def fake_process(proc, pid, parent, name, pss, swap=0):
    directory = proc / str(pid)
    directory.mkdir()
    fields = ['0'] * 22
    fields[0], fields[1] = 'S', str(parent)
    fields[9], fields[11], fields[12], fields[17], fields[19] = '5', '100', '50', '7', str(pid * 100)
    (directory / 'stat').write_text(f'{pid} ({name}) ' + ' '.join(fields))
    (directory / 'comm').write_text(name)
    (directory / 'smaps_rollup').write_text(f'Pss: {pss} kB\nRss: {pss*2} kB\nSwapPss: {swap} kB\nPrivate_Clean: 256 kB\nPrivate_Dirty: 512 kB\n')
    (directory / 'cmdline').write_text('https://private.example/CANARY_TOKEN')
    (directory / 'environ').write_text('COOKIE=CANARY_TOKEN')
    (directory / 'fd').mkdir()
    (directory / 'fd/1').symlink_to('/not-read/CANARY_TOKEN')
    return directory


def test_report_separates_app_browser_driver_and_system_pressure_without_secrets(tmp_path):
    fake_process(tmp_path, 10, 1, 'python', 1024, 128)
    fake_process(tmp_path, 20, 10, 'MainThread', 2048)
    fake_process(tmp_path, 30, 20, 'chromium', 3072, 256)
    fake_process(tmp_path, 40, 1, 'CANARY_TOKEN', 99999)
    (tmp_path / 'meminfo').write_text('MemTotal: 16777216 kB\nMemAvailable: 1048576 kB\nSwapTotal: 2097152 kB\nSwapFree: 1048576 kB\nCached: 204800 kB\n')
    (tmp_path / 'vmstat').write_text('pswpin 123\npswpout 456\n')
    (tmp_path / 'pressure').mkdir()
    (tmp_path / 'pressure/memory').write_text('some avg10=2.5 avg60=1.0 avg300=0.1 total=10000\nfull avg10=1.5 avg60=0.5 avg300=0.1 total=5000\n')
    report = memory_snapshot(10, tmp_path)
    assert report['processes'] == 3 and report['pss_mib'] == 6
    assert report['by_role']['app_vlc']['pss_mib'] == 1
    assert report['by_role']['automation']['pss_mib'] == 2
    assert report['by_role']['browser']['pss_mib'] == 3
    assert report['system_memory']['swap_used_mib'] == 1024
    assert report['system_memory']['pressure']['full']['avg10'] == 1.5
    row = report['process_details'][0]
    assert row['start_ticks'] == 1000 and row['threads'] == 7 and row['file_descriptors'] == 1
    assert row['private_mib'] == .75 and row['cpu_seconds'] > 0
    assert 'CANARY' not in json.dumps(report)
    (tmp_path / '30/smaps_rollup').unlink()
    report = memory_snapshot(10, tmp_path)
    assert report['unreadable_processes'] == 1
    assert report['process_details'][-1]['pss_mib'] is None


def test_report_metadata_whitelists_settings_and_does_not_capture_legacy_urls(tmp_path, monkeypatch):
    from game_stream import resources
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({**DEFAULTS, 'root_url': 'https://private.example/CANARY_TOKEN', 'cookie_file': '/CANARY_TOKEN'}))
    monkeypatch.setattr(resources.shutil, 'which', lambda *args: '/usr/bin/dpkg-query')
    monkeypatch.setattr(resources.subprocess, 'run', lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, '0.2.5', ''))
    monkeypatch.setenv('XDG_SESSION_TYPE', 'wayland')
    metadata = diagnostic_metadata(path)
    assert metadata['installed_app_version'] == '0.2.5'
    assert metadata['desktop_session'] == 'wayland'
    assert metadata['settings'] == DEFAULTS
    assert 'CANARY' not in json.dumps(metadata)


def test_monitor_stops_on_pid_reuse_and_writes_private_json(tmp_path, monkeypatch):
    path = Path(__file__).parent.parent / 'scripts/monitor-resources.py'
    spec = importlib.util.spec_from_file_location('monitor', path)
    monitor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(monitor)
    output = tmp_path / 'memory.json'
    monkeypatch.setattr(monitor.sys, 'argv', ['monitor', '--pid', '10', '--seconds', '100', '--output', str(output)])
    base = dict(running=True, pss_mib=1, swap_mib=0, processes=1)
    monkeypatch.setattr(monitor, 'memory_snapshot', Mock(side_effect=[{**base, 'root_start_ticks': 10}, {**base, 'root_start_ticks': 20}]))
    monkeypatch.setattr(monitor, 'diagnostic_metadata', lambda: {'schema': 2})
    monkeypatch.setattr(monitor, 'sleep', lambda seconds: None)
    monitor.main()
    data = json.loads(output.read_text())
    assert len(data['samples']) == 2 and data['samples'][-1]['root_process_changed']
    assert output.stat().st_mode & 0o777 == 0o600
