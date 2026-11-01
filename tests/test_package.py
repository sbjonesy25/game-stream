import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import zipfile
import tarfile

import pytest
from game_stream import bootstrap, uninstall
from game_stream.config import ConfigStore, DEFAULTS
from game_stream.desktop import install_launcher


def test_runtime_integrity_and_failed_update_keeps_previous(tmp_path, monkeypatch):
    root = tmp_path / 'runtime-root'
    bootstrap.prepare_root(root)
    wheel = tmp_path / 'app.whl'
    wheel.write_bytes(b'fixture')
    manifest = {'sha256': 'one'}
    def fake_step(command, env, log, message, progress):
        if command[1:3] == ['-m', 'venv']:
            stage = Path(command[3])
            (stage / 'bin').mkdir()
            (stage / 'bin/python').symlink_to(sys.executable)
            script = stage / 'bin/fixture'
            script.write_text('#!' + str(stage / 'bin/python') + '\n')
    monkeypatch.setattr(bootstrap, 'run_step', fake_step)
    runtime = bootstrap.build_runtime(root, wheel, manifest, {}, io.BytesIO())
    assert bootstrap.current_runtime(root, manifest) == runtime
    assert (runtime / 'bin/fixture').read_text().splitlines()[0] == '#!' + str(runtime / 'bin/python')
    old = (runtime / 'runtime.json').read_text()
    def fail(*args):
        raise RuntimeError('Fixture install failed')
    monkeypatch.setattr(bootstrap, 'run_step', fail)
    with pytest.raises(RuntimeError):
        bootstrap.build_runtime(root, wheel, {'sha256': 'two'}, {}, io.BytesIO())
    assert (runtime / 'runtime.json').read_text() == old
    assert not list(root.glob('runtime-build-*'))


def test_uninstaller_preserves_settings_and_unknown_files(tmp_path, monkeypatch):
    for key, folder in [('XDG_DATA_HOME', 'data'), ('XDG_CONFIG_HOME', 'config'),
                        ('XDG_STATE_HOME', 'state'), ('XDG_CACHE_HOME', 'cache')]:
        monkeypatch.setenv(key, str(tmp_path / folder))
    monkeypatch.setattr(uninstall.shutil, 'which', lambda name: None)
    desktop = install_launcher(tmp_path / 'data', refresh=False)
    root = tmp_path / 'data/game-stream'
    bootstrap.prepare_root(root)
    (root / 'runtime').mkdir()
    store = ConfigStore()
    store.save(DEFAULTS)
    notes = store.path.parent / 'unrelated-notes.txt'
    notes.write_text('keep')
    update_log = tmp_path / 'state/game-stream/update.log'
    update_log.parent.mkdir(parents=True)
    update_log.write_text('Example update diagnostic')
    uninstall.remove_user_install(dry_run=True)
    assert desktop.exists() and root.exists()
    uninstall.remove_user_install()
    assert not desktop.exists() and not root.exists()
    assert store.path.exists() and notes.exists()
    assert update_log.exists()
    uninstall.remove_user_install(purge=True)
    assert not store.path.exists() and notes.exists()
    assert not update_log.exists()
    # An arbitrary directory without the managed marker must never be removed.
    root.mkdir()
    (root / 'personal.txt').write_text('keep')
    uninstall.remove_user_install(purge=True)
    assert (root / 'personal.txt').exists()


def test_deb_structure_icons_manifest_and_no_root_pip(tmp_path):
    import shutil
    if not shutil.which('dpkg-deb'):
        pytest.skip('dpkg-deb required')
    project = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location('deb_builder', project / 'scripts/build-deb.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    wheel = tmp_path / 'game_stream-0.2.3-py3-none-any.whl'
    with zipfile.ZipFile(wheel, 'w') as z:
        z.writestr('game_stream/__init__.py', '')
    deb = builder.build(wheel, tmp_path / 'out')
    from game_stream.update import validate_release
    feed = validate_release(json.loads((deb.parent / 'latest.json').read_text()))
    assert feed['filename'] == deb.name
    assert feed['size'] == deb.stat().st_size
    standalone = deb.parent / 'uninstall-game-stream.sh'
    bundle = next(deb.parent.glob('game-stream-uninstaller_*.tar.gz'))
    with tarfile.open(bundle) as archive:
        assert archive.extractfile('uninstall-game-stream.sh').read() == standalone.read_bytes()
    result = subprocess.run(['bash', str(standalone), '--dry-run'], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'Dry run complete' in result.stdout
    fields = subprocess.check_output(['dpkg-deb', '--field', str(deb)]).decode()
    assert 'Architecture: amd64' in fields
    assert 'python3-venv' in fields
    assert 'pkexec | policykit-1' in fields
    extracted = tmp_path / 'unpacked'
    subprocess.run(['dpkg-deb', '--extract', str(deb), str(extracted)], check=True)
    control = tmp_path / 'control'
    subprocess.run(['dpkg-deb', '--control', str(deb), str(control)], check=True)
    for script in ('postinst', 'postrm'):
        assert 'pip' not in (control / script).read_text()
        assert 'http' not in (control / script).read_text()
    assert (extracted / 'usr/bin/game-stream').stat().st_mode & 0o111
    assert (extracted / 'usr/bin/game-stream-uninstall').is_file()
    assert (extracted / 'usr/share/icons/hicolor/256x256/apps/game-stream.png').is_file()
    package = extracted / 'usr/share/game-stream'
    manifest, packaged_wheel = bootstrap.read_manifest(package)
    assert packaged_wheel.name == wheel.name
    packaged_wheel.write_bytes(b'tampered')
    with pytest.raises(RuntimeError, match='checksum'):
        bootstrap.read_manifest(package)


def test_runtime_root_refuses_unowned_data(tmp_path):
    (tmp_path / 'important.txt').write_text('keep')
    with pytest.raises(RuntimeError):
        bootstrap.prepare_root(tmp_path)
    assert (tmp_path / 'important.txt').read_text() == 'keep'


def test_cleanup_current_setup_removes_only_its_editable_console_package(tmp_path):
    import shutil
    project = Path(__file__).resolve().parent.parent
    checkout = tmp_path / 'old checkout'
    (checkout / 'scripts').mkdir(parents=True)
    script = checkout / 'scripts/uninstall-current-setup.sh'
    shutil.copy2(project / 'scripts/uninstall-current-setup.sh', script)
    helper = checkout / 'scripts/uninstall.sh'
    helper.write_text('#!/bin/sh\nexit 0\n')
    helper.chmod(0o755)
    venv = checkout / '.venv'
    subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
    site = next((venv / 'lib').glob('python*/site-packages'))
    metadata = site / 'game_stream-0.1.0.dist-info'
    metadata.mkdir()
    (metadata / 'METADATA').write_text('Metadata-Version: 2.1\nName: game-stream\nVersion: 0.1.0\n')
    (metadata / 'direct_url.json').write_text(json.dumps({'url': checkout.as_uri(), 'dir_info': {'editable': True}}))
    cli = venv / 'bin/game-stream'
    cli.write_text('#!/bin/sh\nexit 0\n')
    (metadata / 'RECORD').write_text('\n'.join([
        'game_stream-0.1.0.dist-info/METADATA,,', 'game_stream-0.1.0.dist-info/direct_url.json,,',
        'game_stream-0.1.0.dist-info/RECORD,,', '../../../bin/game-stream,,', '']))
    preview = subprocess.run(['bash', str(script), '--dry-run'], capture_output=True, text=True, check=True)
    assert 'Would remove this checkout' in preview.stdout
    assert cli.exists()
    subprocess.run(['bash', str(script)], capture_output=True, text=True, check=True)
    assert not cli.exists()
    assert (venv / 'bin/python').exists()
    assert (checkout / 'scripts/uninstall-current-setup.sh').exists()
