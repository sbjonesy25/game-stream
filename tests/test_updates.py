import hashlib
import json
from pathlib import Path
import subprocess
from threading import Event
import time
from unittest.mock import Mock

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from game_stream import update
from game_stream.app import Wall
from game_stream.update_ui import UpdateController
from game_stream.recovery import SessionRecovery
from game_stream.session import SessionStore


@pytest.fixture
def package(tmp_path):
    root = tmp_path / 'package'
    (root / 'DEBIAN').mkdir(parents=True)
    (root / 'DEBIAN').chmod(0o755)
    (root / 'DEBIAN/control').write_text('Package: game-stream\nVersion: 0.2.3\nArchitecture: amd64\nMaintainer: Tests <test@example.com>\nDescription: test update\n')
    path = tmp_path / 'game-stream_0.2.3_amd64.deb'
    subprocess.run(['dpkg-deb', '--build', str(root), str(path)], check=True, capture_output=True)
    release = dict(schema=1, version='0.2.3', filename=path.name, size=path.stat().st_size,
                   sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    return path, release


def response(body):
    result = Mock()
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    result.status_code = 200
    result.iter_content.return_value = [body]
    return result


def until(predicate):
    end = time.monotonic() + 8
    while not predicate() and time.monotonic() < end:
        QTest.qWait(10)
    assert predicate()


def test_update_feed_download_validates_real_deb_and_stages_helper(package, monkeypatch):
    import requests
    path, release = package
    fetch = Mock(side_effect=[response(json.dumps(release).encode()), response(path.read_bytes())])
    monkeypatch.setattr(requests, 'get', fetch)
    actual = update.fetch_release(Event())
    assert actual == release
    progress = []
    stage = update.download_release(actual, Event(), progress.append)
    try:
        assert (stage / 'install.py').read_bytes() == Path(update.__file__).read_bytes()
        assert json.loads((stage / 'plan.json').read_text()) == release
        assert progress == [100]
        assert fetch.call_args_list[1].args == (update.RELEASE_ROOT + path.name,)
        assert fetch.call_args.kwargs['allow_redirects'] is False
        assert stage.stat().st_mode & 0o777 == 0o700
    finally:
        update.shutil.rmtree(stage)


def test_bad_checksum_cancel_and_untrusted_metadata_never_stage_install(package, tmp_path, monkeypatch):
    import requests
    path, release = package
    stage = tmp_path / 'stage'
    stage.mkdir()
    monkeypatch.setattr(update.tempfile, 'mkdtemp', lambda **kwargs: str(stage))
    monkeypatch.setattr(requests, 'get', lambda *args, **kwargs: response(b'wrong package'))
    with pytest.raises(update.UpdateError, match='size does not match'):
        update.download_release(release, Event())
    assert not stage.exists()
    stage.mkdir()
    monkeypatch.setattr(requests, 'get', lambda *args, **kwargs: response(path.read_bytes()))
    with pytest.raises(update.UpdateError, match='checksum'):
        update.download_release({**release, 'sha256': '0' * 64}, Event())
    assert not stage.exists()
    cancel = Event()
    cancel.set()
    with pytest.raises(update.UpdateError, match='cancelled'):
        update.fetch_release(cancel)
    for changed in ({'filename': '../../evil.deb'}, {'version': '1.0;echo bad'}, {'size': True}, {'sha256': 'invalid'}):
        with pytest.raises(update.UpdateError):
            update.validate_release({**release, **changed})
    # Matching bytes/checksum still must have the expected package identity.
    with pytest.raises(update.UpdateError, match='expected Game Stream'):
        update.verify_package(path, {**release, 'version': '0.2.4', 'filename': 'game-stream_0.2.4_amd64.deb'})


def test_source_install_cannot_update_system_package(monkeypatch):
    monkeypatch.delenv('GAME_STREAM_SYSTEM_PACKAGE', raising=False)
    with pytest.raises(update.UpdateError, match='source checkout'):
        update.installed_version()


@pytest.mark.parametrize('returncode', [0, 126])
def test_detached_install_waits_for_shutdown_then_reopens_as_user(package, tmp_path, monkeypatch, returncode):
    path, release = package
    stage = tmp_path / 'stage'
    stage.mkdir()
    update.shutil.copy2(path, stage / path.name)
    (stage / 'plan.json').write_text(json.dumps(release))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    events = []
    tokens = iter(['original', 'original', None])
    monkeypatch.setattr(update, 'process_token', lambda pid: next(tokens))
    monkeypatch.setattr(update, 'sleep', lambda seconds: events.append('waiting'))
    monkeypatch.setattr(update, 'notify', lambda text: events.append('notice'))
    update.verify_package(stage / path.name, release)
    monkeypatch.setattr(update, 'verify_package', Mock())
    def run(command, **kwargs):
        assert command[0] == '/usr/bin/pkexec'
        events.append('install')
        assert events[:2] == ['waiting', 'waiting']
        assert command[1:5] == ['/usr/bin/apt-get', 'install', '--yes', '--no-remove']
        return subprocess.CompletedProcess(command, returncode)
    monkeypatch.setattr(update.subprocess, 'run', run)
    popen = Mock(side_effect=lambda *args, **kwargs: events.append(('reopen', args, kwargs)))
    monkeypatch.setattr(update.subprocess, 'Popen', popen)
    assert update.apply_update(stage, 123, 'original') == (0 if returncode == 0 else 1)
    assert not stage.exists()
    assert events[-1][0] == 'reopen'
    assert popen.call_args.args[0] == ['/usr/bin/game-stream', '--restore-after-update']
    assert not popen.call_args.args[0][0].endswith('sudo')
    assert (tmp_path / 'state/game-stream/update.log').stat().st_mode & 0o777 == 0o600
    assert ('notice' in events) == (returncode != 0)


def test_handoff_uses_independent_system_python_and_preserves_desktop_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(update.shutil, 'which', lambda name, **kwargs: '/usr/bin/pkexec')
    monkeypatch.setattr(update, 'process_token', lambda pid: '1234')
    monkeypatch.setenv('PYTHONPATH', '/old/runtime')
    monkeypatch.setenv('DISPLAY', ':1')
    popen = Mock()
    monkeypatch.setattr(update.subprocess, 'Popen', popen)
    update.handoff(tmp_path)
    assert popen.call_args.args[0][:3] == ['/usr/bin/python3', '-I', str(tmp_path / 'install.py')]
    assert popen.call_args.kwargs['start_new_session'] is True
    env = popen.call_args.kwargs['env']
    assert 'PYTHONPATH' not in env and env['DISPLAY'] == ':1'


def test_tampered_staged_package_is_not_installed_and_reopens_old_version(package, tmp_path, monkeypatch):
    path, release = package
    stage = tmp_path / 'stage'
    stage.mkdir()
    (stage / path.name).write_bytes(b'tampered')
    (stage / 'plan.json').write_text(json.dumps(release))
    monkeypatch.setattr(update, 'process_token', lambda pid: None)
    install = Mock()
    reopen = Mock()
    monkeypatch.setattr(update.subprocess, 'run', install)
    monkeypatch.setattr(update.subprocess, 'Popen', reopen)
    monkeypatch.setattr(update, 'notify', Mock())
    assert update.apply_update(stage, 123, 'original') == 1
    install.assert_not_called()
    reopen.assert_called_once()
    assert not stage.exists()


def test_unclosed_app_aborts_install_without_launching_duplicate(tmp_path, monkeypatch):
    stage = tmp_path / 'stage'
    stage.mkdir()
    times = iter([0, 181])
    monkeypatch.setattr(update, 'monotonic', lambda: next(times))
    monkeypatch.setattr(update, 'process_token', lambda pid: 'original')
    monkeypatch.setattr(update, 'notify', Mock())
    install = Mock()
    reopen = Mock()
    monkeypatch.setattr(update.subprocess, 'run', install)
    monkeypatch.setattr(update.subprocess, 'Popen', reopen)
    assert update.apply_update(stage, 123, 'original') == 1
    install.assert_not_called()
    reopen.assert_not_called()


def test_update_menu_downloads_saves_active_session_then_closes(package, tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    path, release = package
    wall = Wall(Mock())
    wall.show()
    store = SessionStore(tmp_path / 'session.json')
    recovery = SessionRecovery(wall, store)
    recovery.start(ask=lambda previous: False)
    wall.panes[0].url.setText('https://example.com/last-game')
    wall.panes[0].want_playing = True
    controller = UpdateController(wall)
    stage = tmp_path / 'download'
    stage.mkdir()
    events = []
    monkeypatch.setattr(update, 'installed_version', lambda: '0.2.2')
    monkeypatch.setattr(update, 'fetch_release', lambda cancel: release)
    monkeypatch.setattr(update, 'download_release', lambda *args: events.append('download') or stage)
    def handoff(directory):
        assert store.load()['panes'][0]['active'] is True
        assert not wall.closing
        events.append('handoff')
    monkeypatch.setattr(update, 'handoff', handoff)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
    try:
        wall.update_action.trigger()
        until(lambda: wall.closing)
        assert events == ['download', 'handoff']
        assert store.load()['clean_exit'] is True
        assert store.load()['panes'][0]['active'] is True
    finally:
        controller.shutdown()
        until(lambda: not controller.busy())
        wall.close()


def test_current_version_does_not_download_and_cancel_does_not_close(package, monkeypatch):
    app = QApplication.instance() or QApplication([])
    path, release = package
    wall = Wall(Mock())
    controller = UpdateController(wall)
    monkeypatch.setattr(update, 'installed_version', lambda: release['version'])
    monkeypatch.setattr(update, 'fetch_release', lambda cancel: release)
    download = Mock()
    monkeypatch.setattr(update, 'download_release', download)
    notice = Mock()
    monkeypatch.setattr(QMessageBox, 'information', notice)
    try:
        controller.check()
        until(lambda: not controller.busy())
        assert notice.call_count == 1 and not wall.closing
        download.assert_not_called()
        controller.check()
        controller.cancel()
        until(lambda: not controller.busy())
        assert notice.call_count == 1 and not wall.closing
    finally:
        wall.close()


def fake_cli(tmp_path, monkeypatch, code):
    executable = tmp_path / 'gh'
    executable.write_text('#!/usr/bin/python3\n' + code)
    executable.chmod(0o755)
    monkeypatch.setattr(update.shutil, 'which', lambda name, **kwargs: str(executable) if name == 'gh' else None)
    return executable


def test_private_repo_update_uses_existing_cli_login_for_feed_and_deb(package, tmp_path, monkeypatch):
    import requests
    path, release = package
    args_file = tmp_path / 'arguments.json'
    fake_cli(tmp_path, monkeypatch,
        'import json,sys\n'
        f'open({str(args_file)!r}, "w").write(json.dumps(sys.argv))\n'
        f'payload = {json.dumps(release).encode()!r} if "latest.json" in sys.argv[2] else {path.read_bytes()!r}\n'
        'sys.stdout.buffer.write(payload)\n')
    denied = response(b'Not Found')
    denied.status_code = 404
    monkeypatch.setattr(requests, 'get', lambda *args, **kwargs: denied)
    assert update.fetch_release(Event()) == release
    stage = update.download_release(release, Event())
    try:
        assert (stage / path.name).read_bytes() == path.read_bytes()
        args = json.loads(args_file.read_text())
        assert args[1] == 'api'
        assert args[2] == f'repos/sbjonesy25/game-stream/contents/releases/{path.name}?ref=main'
        assert 'Accept: application/vnd.github.raw+json' in args
    finally:
        update.shutil.rmtree(stage)


def test_http_error_includes_status_and_private_repo_setup(monkeypatch):
    import requests
    denied = response(b'Not Found')
    denied.status_code = 404
    monkeypatch.setattr(requests, 'get', lambda *args, **kwargs: denied)
    monkeypatch.setattr(update.shutil, 'which', lambda *args, **kwargs: None)
    with pytest.raises(update.UpdateError, match='HTTP 404.*gh auth login'):
        update.fetch_release(Event())
    denied.status_code = 503
    with pytest.raises(update.UpdateError, match='HTTP 503'):
        update.fetch_release(Event())


def test_cli_auth_failure_does_not_expose_stderr_and_is_logged_privately(tmp_path, monkeypatch):
    fake_cli(tmp_path, monkeypatch, 'import sys\nsys.stderr.write("SECRET TEST CREDENTIAL")\nsys.exit(1)\n')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    with pytest.raises(update.UpdateError, match='gh auth login') as error:
        update.transfer_authenticated('latest.json', 16384, Event(), lambda block: None)
    assert 'SECRET' not in str(error.value)
    update.record_failure(str(error.value))
    log = tmp_path / 'state/game-stream/update.log'
    assert log.stat().st_mode & 0o777 == 0o600
    assert 'SECRET' not in log.read_text()


def test_cli_download_can_be_cancelled_and_cannot_exceed_limit(tmp_path, monkeypatch):
    from threading import Timer
    fake_cli(tmp_path, monkeypatch, 'import time\ntime.sleep(20)\n')
    spawned = []
    original_popen = subprocess.Popen
    def popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        spawned.append(process)
        return process
    monkeypatch.setattr(update.subprocess, 'Popen', popen)
    cancel = Event()
    timer = Timer(.2, cancel.set)
    timer.start()
    try:
        with pytest.raises(update.UpdateError, match='cancelled'):
            update.transfer_authenticated('latest.json', 16384, cancel, lambda block: None)
    finally:
        timer.cancel()
    assert spawned[0].poll() is not None
    fake_cli(tmp_path, monkeypatch, 'import sys\nsys.stdout.write("X" * 10000)\n')
    with pytest.raises(update.UpdateError, match='expected size'):
        update.transfer_authenticated('latest.json', 50, Event(), lambda block: None)
