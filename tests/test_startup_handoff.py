from http.server import BaseHTTPRequestHandler
from pathlib import Path
from threading import Event, Thread
from time import monotonic, sleep
import shutil

import pytest
from playwright.sync_api import sync_playwright

from game_stream import resolver
from game_stream.bridge import BrowserRelay
from game_stream.resolver import Source
from test_streams import serve


def test_duplicate_fragment_does_not_permanently_disable_browser_forwarding():
    relay = BrowserRelay()
    seen = []
    script = (Path(__file__).parent.parent / 'game_stream/browser_bridge.js').read_text()
    html = '''<video></video><script>
    window.Hls=class {constructor(){this.media=document.querySelector('video');this.config={};this.calls=0;}
                      trigger(){this.calls++;}};
    window.one=new Hls(); window.other=new Hls();
    window.emit=(h,sn)=>{let payload=new Uint8Array(940);for(let i=0;i<5;i++)payload[i*188]=0x47;
      h.trigger('hlsFragLoaded',{frag:{type:'main',sn,cc:0,duration:1},payload});};
    </script>'''
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=shutil.which('chromium') or shutil.which('chromium-browser'), headless=True)
            try:
                context = browser.new_context()
                def accept(binding, message):
                    seen.append((message['player'], message['sequence']))
                    return relay.push(message['player'], message)
                context.expose_binding('__game_stream_fragment', accept)
                page = context.new_page()
                page.set_content(html)
                page.evaluate(script)
                for player, sn in (('one', 10), ('one', 11), ('one', 12), ('one', 12),
                                   ('other', 1), ('other', 2), ('one', 13), ('one', 14)):
                    page.evaluate('([player,sn])=>emit(window[player],sn)', [player, sn])
                    page.wait_for_timeout(60)
                assert page.evaluate('one.calls') == 6
                assert relay.next_sequence == 5 and relay.last_upstream[0] == 14
                assert seen == [(1, 10), (1, 11), (1, 12), (1, 12), (2, 1), (1, 13), (1, 14)]
            finally:
                browser.close()
    finally:
        relay.close()


def test_live_hls_fallback_uses_playing_frame_and_keeps_session_alive(monkeypatch):
    monkeypatch.setattr(resolver, 'BROWSER_BRIDGE_GRACE', .3, raising=False)
    monkeypatch.setattr(resolver, 'COOKIE_REFRESH_INTERVAL', .2, raising=False)
    pulses = []
    stop = Event()
    sources, errors, consumers = [], [], []
    video = '''<video></video><script>
      const v=document.querySelector('video');
      Object.defineProperties(v,{paused:{get:()=>false},readyState:{get:()=>4}});
      fetch('/live.m3u8');setInterval(()=>fetch('/heartbeat'),100);
    </script>'''

    class Page(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            if self.path == '/watch':
                body, mime = b'<iframe src="/real"></iframe><iframe src="/other"></iframe>', 'text/html'
            elif self.path == '/real':
                body, mime = video.encode(), 'text/html'
            elif self.path == '/other':
                body, mime = b'<script>setTimeout(()=>fetch("/unrelated.m3u8"),50)</script>', 'text/html'
            elif self.path.endswith('.m3u8'):
                body, mime = b'#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2,\nvideo.ts\n', 'application/vnd.apple.mpegurl'
            elif self.path == '/heartbeat':
                pulses.append(monotonic())
                body, mime = b'ok', 'text/plain'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', mime)
            if self.path == '/heartbeat':
                self.send_header('Set-Cookie', f'epoch={len(pulses)}; Path=/; HttpOnly')
            self.end_headers()
            self.wfile.write(body)

    def ready(source):
        sources.append(source)
        at_handoff = len(pulses)
        def consume():
            try:
                deadline = monotonic() + 3
                while monotonic() < deadline:
                    epochs = [int(c['value']) for c in source.cookies if c['name'] == 'epoch']
                    if len(pulses) >= at_handoff + 3 and epochs and epochs[0] > at_handoff:
                        break
                    sleep(.03)
                else:
                    raise AssertionError('Source browser or cookie refresh stopped after direct handoff')
            except Exception as error:
                errors.append(error)
            finally:
                stop.set()
        worker = Thread(target=consume)
        worker.start()
        consumers.append(worker)

    with serve(Page) as origin:
        resolver.resolve(origin + '/watch', on_ready=ready, cancel=stop, timeout=4)
    for worker in consumers:
        worker.join(timeout=5)
    assert not errors, errors
    assert len(sources) == 1 and sources[0].url == origin + '/live.m3u8'
    assert sources[0].relay is None


def test_page_vod_ad_is_not_selected_as_live_fallback(monkeypatch):
    monkeypatch.setattr(resolver, 'BROWSER_BRIDGE_GRACE', .1, raising=False)
    observed = []

    class Page(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            self.send_response(200)
            if self.path == '/watch':
                self.send_header('Content-Type', 'text/html')
                body = b'''<video></video><script>
                  const v=document.querySelector('video');
                  Object.defineProperties(v,{paused:{get:()=>false},readyState:{get:()=>4}});
                  fetch('/ad.m3u8');</script>'''
            else:
                self.send_header('Content-Type', 'application/vnd.apple.mpegurl')
                body = b'#EXTM3U\n#EXT-X-PLAYLIST-TYPE:VOD\n#EXTINF:2,\nad.ts\n#EXT-X-ENDLIST\n'
            self.end_headers()
            self.wfile.write(body)

    with serve(Page) as origin:
        with pytest.raises(RuntimeError, match='No supported video'):
            resolver.resolve(origin + '/watch', on_ready=observed.append, timeout=1)
    assert not observed


def test_upgrade_to_browser_relay_disposes_old_relay_without_changing_audio():
    from unittest.mock import Mock
    from PySide6.QtWidgets import QApplication
    from game_stream.app import Wall
    app = QApplication.instance() or QApplication([])
    wall = Wall(Mock())
    first, second = Mock(url='http://127.0.0.1/first'), Mock(url='http://127.0.0.1/second')
    pane = wall.panes[0]
    try:
        wall.select_audio(wall.panes[1])
        pane.play(Source(first.url, relay=first))
        pane.play(Source(second.url, relay=second))
        first.close.assert_called_once()
        second.close.assert_not_called()
        assert pane.relay is second
        assert [p.audio.isChecked() for p in wall.panes] == [False, True, False, False]
    finally:
        wall.close()


def test_initial_decoder_failure_is_not_reported_as_source_ending():
    from unittest.mock import Mock
    from PySide6.QtWidgets import QApplication
    import vlc
    from game_stream.app import Wall
    app = QApplication.instance() or QApplication([])
    wall = Wall(Mock())
    pane = wall.panes[0]
    pane.relay = Mock()
    pane.player.get_state.return_value = vlc.State.Ended
    try:
        pane.check_state()
        assert 'could not start' in pane.status.text()
        assert 'Stream ended' not in pane.status.text()
        pane.playback_started.emit()
        pane.check_state()
        assert 'Player stopped' in pane.status.text()
    finally:
        wall.close()


def test_playback_diagnostics_distinguish_handoff_types_without_stream_urls(tmp_path):
    from unittest.mock import Mock
    import json
    from game_stream.debug_ui import playback_counters
    from game_stream.debug_capture import read_heartbeat
    from game_stream.config import write_json
    pane = Mock(has_played=False, relay=None)
    pane.player.get_state.return_value = 'State.Ended'
    assert playback_counters(pane) == dict(player_state='Ended', playback_started=False, relay_kind='none')
    pane.relay = Mock(url='https://example.test/CANARY_TOKEN')
    assert playback_counters(pane)['relay_kind'] == 'direct'
    relay = BrowserRelay()
    try:
        pane.relay = relay
        data = playback_counters(pane)
        assert data['accepted_segments'] == 0 and data['relay_kind'] == 'browser'
        path = tmp_path / 'heartbeat.json'
        write_json(path, {'panes': [dict(data, number=1, url='CANARY_TOKEN', player_state='Ended'),
                                    dict(number=2, player_state='CANARY_TOKEN', relay_kind='CANARY_TOKEN')]})
        result = read_heartbeat(path)
        assert 'CANARY' not in json.dumps(result)
        assert result['panes'][0]['player_state'] == 'Ended'
        assert result['panes'][0]['relay_kind'] == 'browser'
        assert result['panes'][1] == {'number': 2}
    finally:
        relay.close()
