import base64
from http.server import BaseHTTPRequestHandler
from threading import Event, Thread
import json
import shutil
import subprocess
import time

import pytest
import requests

from game_stream.bridge import BrowserRelay
from game_stream.resolver import resolve
from test_streams import serve


def message(sequence=1, cc=0, duration=4, data=None):
    data = data or (b'\x47' + bytes(187)) * 5
    return dict(sequence=sequence, cc=cc, duration=duration,
                bytes=base64.b64encode(data).decode())


def test_bridge_validation_window_and_discontinuities():
    relay = BrowserRelay()
    relay.MAX_SEGMENTS = 4
    try:
        assert not relay.push('player', message(data=b'not-video'))
        assert not relay.push('player', message(duration=float('nan')))
        assert not relay.push('player', {'bytes': 'invalid'})
        for i in range(1, 6):
            assert relay.push('player', message(i))
        assert not relay.push('different-player', message(6))
        assert not relay.push('player', message(4))
        assert len(relay.segments) == 4
        assert relay.ready
        assert relay.push('player', message(8, cc=1))
        response = requests.get(relay.url)
        assert response.status_code == 200
        assert '#EXT-X-DISCONTINUITY\n' in response.text
        assert '#EXT-X-MEDIA-SEQUENCE:2' in response.text
        last = response.text.splitlines()[-1]
        segment_url = relay.url.rsplit('/', 1)[0] + '/' + last
        assert requests.get(segment_url).content == (b'\x47' + bytes(187)) * 5
        assert requests.get(relay.url.replace('/live.m3u8', '/0.ts')).status_code == 404
        assert requests.get(relay.url.replace(relay.prefix, '/wrong-key')).status_code == 404
        assert relay.total_bytes <= relay.MAX_BYTES
    finally:
        relay.close()
    assert relay.closed.is_set()
    assert not relay.push('player', message(9))
    relay.close()


def test_bridge_memory_limit_and_evicted_discontinuity():
    relay = BrowserRelay()
    relay.MAX_BYTES = 940 * 3
    try:
        relay.push('one', message(1))
        for i in range(2, 8):
            relay.push('one', message(i, cc=1))
        assert len(relay.segments) == 3
        assert relay.total_bytes == 940 * 3
        assert '#EXT-X-DISCONTINUITY-SEQUENCE:1\n' in requests.get(relay.url).text
    finally:
        relay.close()


def test_browser_decoded_fragments_continue_after_handoff(tmp_path):
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg required for media decoding')
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                    'testsrc=size=160x90:rate=10', '-f', 'lavfi', '-i', 'sine=frequency=440',
                    '-t', '8', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-threads', '1',
                    '-c:a', 'aac', '-g', '10', '-hls_time', '1', '-hls_list_size', '0',
                    str(tmp_path / 'test.m3u8')], check=True)
    segments = [p.read_bytes() for p in sorted(tmp_path.glob('*.ts'))]
    # The fixture follows the observed transport: JSON index, image-labelled
    # chunks, browser-side unpacking, then a Clappr/Hls.js fragment event.
    page = '''<video></video><button class="play-wrapper" onclick="start()">Play</button>
    <script>
    const Hls = class {
        constructor() { this.media = document.querySelector('video'); }
        trigger(event, data) { window.originalEvents = (window.originalEvents || 0) + 1; }
    };
    let player = {core: {getCurrentPlayback: () => ({_hls: hls})}};
    const hls = new Hls(); let running = false;
    async function start() {
        if (running) return; running = true;
        // A pre-roll manifest arrives before the real player's first fragment.
        await new Promise(r => setTimeout(r, 5500));
        const index = await (await fetch('/index.json')).json();
        for (let i = 0; i < index.count; i++) {
            const encoded = await (await fetch('/part/' + i + '.png')).arrayBuffer();
            const clear = encoded.slice(8);
            hls.trigger('hlsFragLoaded', {frag:{type:'main',sn:i,cc:0,duration:1},payload:clear});
            await new Promise(r => setTimeout(r, 200));
        }
    }
    fetch('/ad.m3u8');
    </script>'''

    class Site(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            if self.path == '/watch':
                body, mime = b'<iframe src="/player"></iframe>', 'text/html'
            elif self.path == '/player':
                body, mime = page.encode(), 'text/html'
            elif self.path == '/index.json':
                body, mime = json.dumps({'count': len(segments)}).encode(), 'application/json'
            elif self.path == '/ad.m3u8':
                body, mime = b'#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2,\nad.ts\n#EXT-X-ENDLIST\n', 'application/vnd.apple.mpegurl'
            elif self.path.startswith('/part/'):
                n = int(self.path.rsplit('/', 1)[-1].split('.')[0])
                body, mime = b'\x89PNG\r\n\x1a\n' + segments[n], 'image/png'
            else:
                self.send_error(404); return
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.end_headers()
            self.wfile.write(body)

    stop = Event()
    observed = []
    failures = []
    consumer = []

    def ready(source):
        assert source.relay is not None
        observed.append(source)
        def consume():
            try:
                deadline = time.monotonic() + 12
                while source.relay.next_sequence < 7 and time.monotonic() < deadline:
                    time.sleep(0.05)
                assert source.relay.next_sequence >= 7, 'Browser stopped forwarding after handoff'
                result = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error',
                                         '-i', source.url, '-t', '1', '-f', 'null', '-'],
                                        capture_output=True, timeout=15)
                assert result.returncode == 0, result.stderr.decode()
            except Exception as exc:
                failures.append(exc)
            finally:
                stop.set()
        thread = Thread(target=consume)
        thread.start()
        consumer.append(thread)

    with serve(Site) as origin:
        resolve(origin + '/watch', on_ready=ready, cancel=stop, timeout=20)
    for thread in consumer:
        thread.join(timeout=20)
    assert not failures, failures
    assert len(observed) == 1
    assert observed[0].relay.closed.is_set()
