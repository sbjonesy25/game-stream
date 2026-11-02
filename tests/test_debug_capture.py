import json
import os
from pathlib import Path
import time
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from game_stream.app import Wall
from game_stream.config import ConfigStore, DEFAULTS, write_json
from game_stream import debug_capture as capture
from game_stream.debug_ui import DebugController
from game_stream.preferences import PreferencesController, PreferencesDialog
from game_stream.quadrant_labels import label_rect


def wait_for(predicate, seconds=8):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        time.sleep(.02)
    assert predicate()


def test_label_insets_opacity_and_independent_idle_fade(monkeypatch):
    app = QApplication.instance() or QApplication([])
    wall = Wall(Mock())
    wall.show()
    monkeypatch.setattr(QApplication, 'activeWindow', lambda: wall)
    app.processEvents()
    wall.pointer_timer.stop()
    original_handles = [int(p.video.winId()) for p in wall.panes]
    try:
        wall.apply_view_preferences(dict(DEFAULTS, quadrant_label_corner='bottom-right',
            quadrant_label_offset_x=37, quadrant_label_offset_y=29, quadrant_label_opacity_percent=40,
            quadrant_label_display='timed', quadrant_label_timeout_ms=250, overlay_enabled=False))
        for pane in wall.panes:
            badge = pane.number_badge
            video = QRect(pane.video.mapToGlobal(QPoint()), pane.video.size())
            assert badge.geometry() == label_rect(video, badge.size(), 'bottom-right', 37, 29)
            assert badge.opacity == .4
            image = badge.grab().toImage()
            assert max(image.pixelColor(x, y).alpha() for x in range(image.width()) for y in range(image.height())) <= 103
        QTest.qWait(600)
        assert wall.number_alpha == 0
        assert not any(p.number_badge.isVisible() for p in wall.panes)
        wall.select_audio(wall.panes[1])
        assert wall.number_alpha == 1 and wall.number_idle.isActive()
        wall.toggle_fullscreen()
        app.processEvents()
        wall.reveal_numbers()
        assert wall.number_opacity() == .4
        assert not wall.tools_overlay.isVisible()
        QTest.qWait(600)
        assert wall.number_opacity() == 0
        # Always-on cancels fading; unrelated HUD timers never hide the label.
        wall.apply_view_preferences(dict(DEFAULTS, quadrant_label_offset_x=500, quadrant_label_offset_y=500))
        QTest.qWait(600)
        assert wall.number_opacity() == 1 and not wall.number_idle.isActive()
        assert [int(p.video.winId()) for p in wall.panes] == original_handles
        video = QRect(0, 0, 240, 135)
        assert video.contains(label_rect(video, wall.panes[0].number_badge.size(), 'top-left', 500, 500))
    finally:
        wall.close()


def test_new_preferences_save_validate_and_restart_prompt(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    wall = Wall(Mock())
    store = ConfigStore(tmp_path / 'config.json')
    preferences = PreferencesController(wall, store)
    debug = DebugController(wall, capture=Mock())
    debug.capture.process = None
    debug.capture.root = tmp_path / 'debug'
    restart = Mock()
    wall.restart_for_debug = restart
    try:
        dialog = PreferencesDialog(store, DEFAULTS, wall)
        dialog.number_x.setValue(30)
        dialog.number_y.setValue(40)
        dialog.number_opacity.setValue(65)
        dialog.number_display.setCurrentIndex(1)
        dialog.number_delay.setValue(1250)
        dialog.debug_enabled.setChecked(True)
        assert dialog.save()
        dialog.deleteLater()
        saved = store.load()
        assert saved['quadrant_label_offset_x'] == 30 and saved['quadrant_label_offset_y'] == 40
        assert saved['quadrant_label_display'] == 'timed' and saved['quadrant_label_timeout_ms'] == 1250
        assert saved['quadrant_label_opacity_percent'] == 65 and saved['debug_enabled']
        for bad in ({'quadrant_label_offset_x': True}, {'quadrant_label_offset_y': 501},
                    {'quadrant_label_opacity_percent': -1}, {'quadrant_label_display': 'never'},
                    {'quadrant_label_timeout_ms': 0}, {'debug_enabled': 'true'}):
            with pytest.raises(ValueError):
                store.validate(dict(DEFAULTS, **bad))
        # Enabling offers a restart only after saving and closing Preferences.
        monkeypatch.setattr(PreferencesDialog, 'exec', lambda self: setattr(self, 'saved', saved) or QDialog.DialogCode.Accepted)
        monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.No)
        preferences.open_preferences()
        restart.assert_not_called()
        debug.capture.start.assert_not_called()
        assert preferences.preferences['debug_enabled']
        monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Yes)
        debug.changed(True)
        restart.assert_called_once()
        debug.changed(False)
        debug.capture.stop.assert_called_once()
    finally:
        wall.close()


def test_capture_retention_clear_preserves_unknown_paths_symlinks_and_live_runs(tmp_path, monkeypatch):
    root = tmp_path / 'debug'
    unrelated = tmp_path / 'important'
    unrelated.mkdir()
    (unrelated / 'keep').write_text('keep')
    root.symlink_to(unrelated, target_is_directory=True)
    with pytest.raises(ValueError):
        capture.new_capture(root)
    root.unlink()
    capture.ensure_root(root)
    (root / 'unmarked').mkdir()
    (root / 'unmarked/keep').write_text('keep')
    (root / 'external').symlink_to(unrelated, target_is_directory=True)
    first = capture.new_capture(root)
    write_json(first / capture.MARKER, dict(kind=capture.KIND, monitor_pid=os.getpid(), monitor_start_ticks=capture.start_ticks(os.getpid())))
    for _ in range(8):
        capture.new_capture(root)
    assert first.is_dir()  # Active capture protected even beyond the retention limit.
    assert len(capture.runs(root)) <= capture.RETAIN_RUNS + 1
    capture.clear_captures(root)
    assert capture.runs(root) == [(first, True)]
    assert (root / 'unmarked/keep').read_text() == 'keep'
    assert (unrelated / 'keep').read_text() == 'keep'
    assert (root / 'external').is_symlink()
    (first / capture.MARKER).unlink()
    (first / capture.MARKER).symlink_to(unrelated / 'keep')
    assert capture.clear_captures(root) == 0


def test_capture_rotates_logs_and_whitelists_heartbeat(tmp_path, monkeypatch):
    directory = capture.new_capture(tmp_path / 'debug')
    monkeypatch.setattr(capture, 'MAX_BYTES', 10)
    for index in range(9):
        capture.append_sample(directory, {'index': index})
    assert len(list(directory.glob('memory*jsonl'))) == 4
    assert json.loads((directory / 'memory.jsonl').read_text()) == {'index': 8}
    assert json.loads((directory / 'memory.3.jsonl').read_text()) == {'index': 5}
    write_json(directory / 'heartbeat.json', {'pid': 123, 'show_browser': True, 'url': 'CANARY',
        'visible_panes': 'CANARY', 'panes': [{'number': 1, 'audio': True, 'cookie': 'CANARY', 'worker_running': 'CANARY'}]})
    heartbeat = capture.read_heartbeat(directory / 'heartbeat.json')
    assert heartbeat == {'pid': 123, 'show_browser': True, 'panes': [{'number': 1, 'audio': True}]}
    assert 'CANARY' not in json.dumps(heartbeat)
    for path in directory.glob('*.json*'):
        assert path.stat().st_mode & 0o777 == 0o600


def test_real_separate_sampler_records_startup_and_stops_without_gui(tmp_path, monkeypatch):
    config = tmp_path / 'config/game-stream/config.json'
    monkeypatch.setenv('XDG_CONFIG_HOME', str(config.parent.parent))
    write_json(config, dict(DEFAULTS, debug_enabled=True))
    process = capture.CaptureProcess(tmp_path / 'debug')
    try:
        directory = process.start()
        write_json(directory / 'heartbeat.json', {'pid': os.getpid(), 'visible_panes': 4})
        wait_for(lambda: (directory / 'memory.jsonl').exists())
        assert process.process.poll() is None
        sample = json.loads((directory / 'memory.jsonl').read_text().splitlines()[0])
        assert sample['running'] and sample['pid'] == os.getpid()
        assert sample['ui_heartbeat']['visible_panes'] == 4
        assert 'diagnostic_monitor' in sample['by_role']
        assert json.loads((directory / 'metadata.json').read_text())['settings']['debug_enabled']
        process.stop()
        assert json.loads((directory / 'status.json').read_text())['reason'] == 'stopped'
        fresh = process.start()
        assert fresh != directory and (directory / 'startup.json').exists()
    finally:
        process.stop()


def test_sampler_stops_when_root_pid_reused_and_reports_failure_safely(tmp_path, monkeypatch):
    directory = capture.new_capture(tmp_path / 'debug')
    base = dict(running=True, pss_mib=1, swap_mib=0, processes=1)
    monkeypatch.setattr(capture, 'memory_snapshot', Mock(side_effect=[dict(base, root_start_ticks=1), dict(base, root_start_ticks=2)]))
    monkeypatch.setattr(capture, 'diagnostic_metadata', lambda: {})
    capture.run_capture(123, directory, interval=0)
    assert json.loads((directory / 'status.json').read_text())['reason'] == 'pid_reused'
    assert len((directory / 'memory.jsonl').read_text().splitlines()) == 2
    unmarked = tmp_path / 'unmarked'
    unmarked.mkdir()
    monkeypatch.setattr(capture.sys, 'argv', ['capture', '--pid', '123', '--directory', str(unmarked)])
    assert capture.main() == 1
    assert not list(unmarked.iterdir())


def test_debug_heartbeat_has_no_links_and_clear_resumes_capture(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    wall = Wall(Mock())
    process = Mock()
    process.root = tmp_path / 'debug'
    process.directory = capture.new_capture(process.root)
    process.process.poll.return_value = None
    controller = DebugController(wall, process)
    try:
        wall.panes[0].url.setText('https://example.test/CANARY?token=CANARY')
        controller.heartbeat()
        raw = (process.directory / 'heartbeat.json').read_text()
        assert 'CANARY' not in raw
        assert json.loads(raw)['panes'][0]['has_link']
        monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Yes)
        # Clear runs after the sampler has stopped, then starts a fresh capture.
        old_directory = process.directory
        def start_fresh():
            assert not old_directory.exists()
            process.directory = capture.new_capture(process.root)
        process.start.side_effect = start_fresh
        controller.clear()
        process.stop.assert_called_once()
        process.start.assert_called_once()
        assert not old_directory.exists() and process.directory.exists()
    finally:
        controller.timer.stop()
        wall.close()


def test_restart_preserves_session_before_teardown_and_aborts_on_save_failure(monkeypatch):
    app = QApplication.instance() or QApplication([])
    wall = Wall(Mock())
    wall.recovery = Mock(save_error=True)
    close = Mock()
    wall.close = close
    monkeypatch.setattr(QMessageBox, 'warning', Mock())
    try:
        wall.restart_for_debug()
        wall.recovery.checkpoint.assert_called_once_with(force=True)
        assert not wall.restart_requested
        close.assert_not_called()
        wall.recovery.save_error = False
        wall.restart_for_debug()
        assert wall.restart_requested
        close.assert_called_once()
    finally:
        wall.recovery = None
        Wall.close(wall)


def test_startup_recording_is_opt_in_and_uses_saved_settings(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    store = ConfigStore()
    wall = Wall(Mock())
    process = Mock()
    process.directory = capture.new_capture(tmp_path / 'debug')
    process.process.poll.return_value = None
    controller = DebugController(wall, process)
    try:
        controller.startup()
        process.start.assert_not_called()
        store.save(dict(DEFAULTS, debug_enabled=True))
        controller.startup()
        process.start.assert_called_once()
        assert controller.timer.isActive()
        assert (process.directory / 'heartbeat.json').exists()
        controller.stop()
        assert not controller.timer.isActive()
    finally:
        controller.timer.stop()
        wall.close()


def test_uninstaller_purge_only_removes_marked_inactive_debug_captures(tmp_path, monkeypatch):
    from game_stream.uninstall import purge_debug
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path))
    root = tmp_path / 'game-stream/debug'
    directory = capture.new_capture(root)
    live = capture.new_capture(root)
    write_json(live / capture.MARKER, dict(kind=capture.KIND, monitor_pid=os.getpid(), monitor_start_ticks=capture.start_ticks(os.getpid())))
    (root / 'keep').mkdir()
    (root / 'keep/important').write_text('keep')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'important').write_text('keep')
    (root / 'linked').symlink_to(outside, target_is_directory=True)
    purge_debug(dry_run=True)
    assert directory.exists()
    purge_debug()
    assert not directory.exists() and live.exists()
    assert (root / 'keep/important').read_text() == 'keep'
    assert (outside / 'important').read_text() == 'keep'
    assert (root / 'linked').is_symlink()
