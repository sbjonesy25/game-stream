from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread, Event
from urllib.parse import urlsplit
import shutil
import subprocess

import pytest
import requests

from streamwall.proxy import Relay
from streamwall.resolver import Source, resolve, validate_url, load_cookies


@contextmanager
def serve(handler):
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class ProtectedHLS(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        type(self).seen.append((self.path, dict(self.headers)))
        if self.headers.get('Cookie') != 'session=valid' or self.headers.get('Referer') != 'https://player.example/watch':
            self.send_error(403)
            return
        data = {
            '/master.m3u8': b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nchild/live.m3u8\n',
            '/child/live.m3u8': b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="../key"\n#EXT-X-MAP:URI="init"\n#EXTINF:2,\nsegment.ts?token=test\n',
            '/key': b'0123456789abcdef', '/child/init': b'init-data',
            '/child/segment.ts?token=test': b'segment-data',
        }.get(self.path)
        if data is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'application/vnd.apple.mpegurl' if '.m3u8' in self.path else 'application/octet-stream')
        self.end_headers()
        self.wfile.write(data)


def test_authenticated_nested_hls():
    with serve(ProtectedHLS) as origin:
        relay = Relay(Source(origin + '/master.m3u8', {'referer': 'https://player.example/watch'},
                             [{'name': 'session', 'value': 'valid', 'domain': '127.0.0.1', 'path': '/'}]))
        try:
            master = requests.get(relay.url).text
            child = requests.get(master.splitlines()[-1]).text
            import re
            for url in re.findall('URI="([^"]+)"', child):
                assert requests.get(url).status_code == 200
            segment = requests.get(child.splitlines()[-1])
            assert segment.content == b'segment-data'
            assert requests.get(relay.url + '/unknown').status_code == 404
        finally:
            relay.close()


def test_cookie_domain_isolation_and_range():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            assert 'Cookie' not in self.headers
            assert 'Authorization' not in self.headers
            assert self.headers['Range'] == 'bytes=0-2'
            self.send_response(206)
            self.send_header('Content-Range', 'bytes 0-2/6')
            self.end_headers()
            self.wfile.write(b'abc')
    with serve(Handler) as origin:
        relay = Relay(Source('https://different.example/master.m3u8', {'authorization': 'private'},
                             [{'name': 'secret', 'value': 'private', 'domain': 'different.example', 'path': '/'}]))
        try:
            r = requests.get(relay.register(origin + '/segment'), headers={'Range': 'bytes=0-2'})
            assert r.status_code == 206
            assert r.headers['Content-Range'] == 'bytes 0-2/6'
            assert r.content == b'abc'
        finally:
            relay.close()


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'ftp://host/video', 'http://user:pass@host/video', 'no-url'])
def test_bad_urls(url):
    with pytest.raises(ValueError):
        validate_url(url)


def test_browser_discovery_and_cookies():
    class Page(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            self.send_response(200)
            if self.path == '/watch':
                self.send_header('Content-Type', 'text/html')
                self.send_header('Set-Cookie', 'viewer=active; Path=/; HttpOnly')
                body = b'<html><script>fetch("/stream.m3u8")</script></html>'
            else:
                self.send_header('Content-Type', 'application/vnd.apple.mpegurl')
                body = b'#EXTM3U\n#EXTINF:1,\nsegment.ts\n'
            self.end_headers()
            self.wfile.write(body)
    with serve(Page) as origin:
        source = resolve(origin + '/watch', timeout=10)
        assert source.url == origin + '/stream.m3u8'
        assert source.headers['referer'] == origin + '/watch'
        assert any(c['name'] == 'viewer' and c['value'] == 'active' for c in source.cookies)


def test_direct_url_and_cookie_file(tmp_path):
    path = tmp_path / 'cookies.txt'
    path.write_text('# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tFALSE\t0\tsession\tvalue\n')
    source = resolve('https://example.com/live.m3u8', str(path))
    assert source.cookies[0]['name'] == 'session'


def test_relay_decodes_real_hls(tmp_path):
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg not installed')
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                    'testsrc=size=160x90:rate=10', '-t', '3', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                    '-threads', '1', '-g', '10', '-hls_time', '1', '-hls_list_size', '0',
                    str(tmp_path / 'live.m3u8')], check=True)
    from http.server import SimpleHTTPRequestHandler
    class Files(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs): super().__init__(*args, directory=str(tmp_path), **kwargs)
        def log_message(self, *args): pass
    with serve(Files) as origin:
        relay = Relay(Source(origin + '/live.m3u8'))
        try:
            r = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', relay.url,
                                '-f', 'null', '-'], capture_output=True, timeout=30)
            assert r.returncode == 0, r.stderr.decode()
        finally:
            relay.close()
