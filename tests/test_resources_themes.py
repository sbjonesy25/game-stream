import base64
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel
from game_stream.app import Wall
from game_stream.config import ConfigStore, DEFAULTS
from game_stream.preferences import PreferencesDialog, PreferencesController
from game_stream.proxy import Relay
from game_stream.resolver import Source
from game_stream.bridge import BrowserRelay
from game_stream.themes import PALETTES


def test_g_switches_three_pane_layout_and_h_toggles_one_help_popup(tmp_path):
    app = QApplication.instance() or QApplication([])
    wall = Wall(Mock())
    wall.show()
    store = ConfigStore(tmp_path / 'config.json')
    controller = PreferencesController(wall, store)
    controller.start()
    wall.count.setCurrentIndex(2)
    wall.select_audio(wall.panes[1])
    app.processEvents()
    try:
        assert None not in wall.owned_windows()
        QTest.keyClick(wall, Qt.Key.Key_G)
        assert wall.three_pane_layout == 'grid'
        QTest.keyClick(wall, Qt.Key.Key_F)
        QTest.keyClick(wall.tools_overlay, Qt.Key.Key_G)
        assert wall.three_pane_layout == 'focus' and wall.isFullScreen()
        assert wall.panes[1].audio.isChecked()
        assert store.load()['three_pane_layout'] == 'focus'
        QTest.keyClick(wall, Qt.Key.Key_H)
        popup = wall.shortcuts_dialog
        assert popup.isVisible()
        assert any('Alt+1' in label.text() and 'Shift+1' in label.text() and 'G' in label.text()
                   for label in popup.findChildren(QLabel))
        wall.poll_pointer()
        assert not wall.video_input.isVisible()
        QTest.keyClick(popup, Qt.Key.Key_H)
        assert not popup.isVisible()
        QTest.keyClick(wall, Qt.Key.Key_H)
        assert wall.shortcuts_dialog is popup and popup.isVisible()
        QTest.keyClick(popup, Qt.Key.Key_Escape)
        assert not popup.isVisible()
        wall.restore()
        app.processEvents()
        field = wall.panes[0].url
        field.setFocus()
        QTest.keyClicks(field, 'https://highlights.example/game')
        assert field.text() == 'https://highlights.example/game'
        assert not popup.isVisible()
    finally:
        wall.close()


def test_palettes_preserve_black_video_and_preferences_persist_theme_quality(tmp_path):
    app = QApplication.instance() or QApplication([])
    wall = Wall(Mock())
    wall.show()
    store = ConfigStore(tmp_path / 'config.json')
    try:
        for theme in ('default', 'light', 'dark'):
            wall.apply_view_preferences({**DEFAULTS, 'theme': theme})
            assert PALETTES[theme]['background'] in wall.styleSheet()
            assert PALETTES[theme]['background'] in wall.panes[0].styleSheet()
            assert '#000' in wall.panes[0].video.styleSheet()
        assert PALETTES['light']['background'] == '#e6e6e6'
        assert PALETTES['dark']['background'] == '#191919'
        dialog = PreferencesDialog(store, DEFAULTS, wall)
        dialog.theme.setCurrentIndex(dialog.theme.findData('light'))
        dialog.quality.setCurrentIndex(dialog.quality.findData(1080))
        dialog.save()
        assert store.load()['theme'] == 'light' and store.load()['browser_max_height'] == 1080
        for invalid in ({'theme': 'arbitrary css'}, {'browser_max_height': 2000}):
            with pytest.raises(ValueError):
                store.save({**DEFAULTS, **invalid})
    finally:
        wall.close()


def test_live_url_table_stays_bounded_after_thousands_of_refreshes_and_clears():
    relay = Relay(Source('https://example.com/master.m3u8'))
    relay.MAX_URLS = 32
    try:
        for i in range(10000):
            body = relay.rewrite(f'#EXTM3U\n#EXTINF:2,\nsegment-{i}.ts\n', 'https://example.com/live.m3u8')
        assert len(relay.urls) <= 32 and len(relay.reverse) <= 32
        assert relay.source.url in relay.reverse  # Original VLC master URL is pinned.
        assert 'https://example.com/segment-9999.ts' in relay.reverse
        assert 'https://example.com/segment-1.ts' not in relay.reverse
        relay.MAX_URL_CHARS = 500
        for i in range(100):
            relay.register('https://example.com/' + str(i) + 'x' * 200)
        assert relay.total_url_chars <= 500
        with pytest.raises(ValueError):
            relay.register('https://example.com/' + 'x' * 17000)
    finally:
        relay.close()
    assert not relay.urls and not relay.reverse and not relay.total_url_chars
    relay.close()


def test_four_browser_relays_keep_only_recent_fragments_and_release_them():
    relays = [BrowserRelay() for _ in range(4)]
    data = base64.b64encode((b'\x47' + bytes(187)) * 5).decode()
    try:
        for i in range(5000):
            for relay in relays:
                assert relay.push('player', dict(sequence=i, cc=0, duration=1, bytes=data))
        for relay in relays:
            assert len(relay.segments) == relay.MAX_SEGMENTS == 8
            assert relay.total_bytes == 8 * 940
            assert not relay.push('different player', {'bytes': 'not decoded'})
            assert '#EXT-X-MEDIA-SEQUENCE:4994' in requests.get(relay.url).text
    finally:
        for relay in relays:
            relay.close()
    assert all(not relay.segments and not relay.total_bytes for relay in relays)


def test_resource_report_sums_descendants_without_reading_command_lines(tmp_path):
    from game_stream.resources import memory_snapshot
    for pid, parent, memory in ((10, 1, 1024), (20, 10, 2048), (30, 20, 3072), (40, 1, 9999)):
        entry = tmp_path / str(pid)
        entry.mkdir()
        (entry / 'stat').write_text(f'{pid} (name with spaces) S {parent} 0 0')
        (entry / 'smaps_rollup').write_text(f'Pss: {memory} kB\nSwapPss: 0 kB\n')
    sample = memory_snapshot(10, tmp_path)
    assert sample['processes'] == 3 and sample['pss_mib'] == 6
    assert sample['swap_mib'] == 0 and sample['running']
    assert sample['unreadable_processes'] == 0


def test_browser_bridge_limits_hls_buffers_and_rejects_only_the_other_player():
    import shutil
    from urllib.parse import quote
    from playwright.sync_api import sync_playwright
    seen = []
    script = (Path(__file__).parent.parent / 'game_stream/browser_bridge.js').read_text()
    html = '''<video></video><script>
    window.Hls = class {
        constructor() { this.media=document.querySelector('video'); this.config={backBufferLength:Infinity};
                        this.levels=[{height:360},{height:720},{height:1080}]; this.calls=0; }
        trigger() { this.calls++; }
    };
    window.one=new Hls(); window.other=new Hls();
    window.emit=(h,sn)=>{const bytes=new Uint8Array(940);for(let i=0;i<5;i++)bytes[i*188]=0x47;
       h.trigger('hlsFragLoaded',{frag:{type:'main',sn,cc:0,duration:1},payload:bytes});};
    </script>'''
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=shutil.which('chromium') or shutil.which('chromium-browser'), headless=True)
        try:
            context = browser.new_context()
            def accept(binding, message):
                seen.append(message)
                return message['player'] == 1
            context.expose_binding('__game_stream_fragment', accept)
            page = context.new_page()
            page.set_content(html)
            page.evaluate('() => { globalThis.__game_stream_max_height=720;\n' + script + '\n}')
            page.wait_for_timeout(150)
            for player, sn in (('one', 1), ('other', 1), ('one', 2), ('other', 2), ('one', 3)):
                page.evaluate(f'emit({player},{sn})')
                page.wait_for_timeout(50)
            state = page.evaluate('({back:one.config.backBufferLength,forward:one.config.maxMaxBufferLength,size:one.config.maxBufferSize,cap:one.autoLevelCapping,calls:one.calls})')
            assert state == dict(back=10, forward=24, size=16*1024*1024, cap=1, calls=3)
            assert [(m['player'], m['sequence']) for m in seen] == [(1, 1), (2, 1), (1, 2), (1, 3)]
        finally:
            browser.close()
