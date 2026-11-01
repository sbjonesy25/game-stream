import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import zipfile

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
    uninstall.remove_user_install(dry_run=True)
    assert desktop.exists() and root.exists()
    uninstall.remove_user_install()
    assert not desktop.exists() and not root.exists()
    assert store.path.exists() and notes.exists()
    uninstall.remove_user_install(purge=True)
    assert not store.path.exists() and notes.exists()
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
    wheel = tmp_path / 'game_stream-0.2.0-py3-none-any.whl'
    with zipfile.ZipFile(wheel, 'w') as z:
        z.writestr('game_stream/__init__.py', '')
    deb = builder.build(wheel, tmp_path / 'out')
    fields = subprocess.check_output(['dpkg-deb', '--field', str(deb)]).decode()
    assert 'Architecture: amd64' in fields
    assert 'python3-venv' in fields
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
