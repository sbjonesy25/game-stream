"""Loopback HLS relay: preserve request context for manifests, keys and segments."""
import re
import secrets
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread, Event
from urllib.parse import urljoin, urlsplit

import requests
from .resolver import validate_url

URI = re.compile(r'URI="([^"]+)"')


class Relay:
    MAX_URLS = 2048
    MAX_URL_CHARS = 4 * 1024 * 1024

    def __init__(self, source):
        self.source = source
        self.urls = OrderedDict()
        self.reverse = {}
        self.total_url_chars = 0
        self.lock = Lock()
        self.closed = Event()
        relay = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.0'

            def log_message(self, *args):
                pass  # URLs and query strings can contain credentials.

            def do_GET(self):
                with relay.lock:
                    upstream = relay.urls.get(self.path)
                    if upstream:
                        relay.urls.move_to_end(self.path)
                if not upstream:
                    self.send_error(404)
                    return
                started = False
                try:
                    with requests.Session() as session:
                        for cookie in relay.source.cookies:
                            session.cookies.set(cookie['name'], cookie['value'],
                                                domain=cookie['domain'], path=cookie.get('path', '/'),
                                                secure=cookie.get('secure', False),
                                                expires=cookie.get('expires'))
                        headers = dict(relay.source.headers)
                        if urlsplit(upstream).netloc != urlsplit(relay.source.url).netloc:
                            headers.pop('authorization', None)
                        if self.headers.get('Range'):
                            headers['Range'] = self.headers['Range']
                        with session.get(upstream, headers=headers, stream=True, timeout=(10, 25)) as response:
                            response.raise_for_status()
                            mime = response.headers.get('Content-Type', '')
                            if 'mpegurl' in mime.lower() or urlsplit(response.url).path.lower().endswith('.m3u8'):
                                # Bound playlist reads; segments are streamed without buffering.
                                data = bytearray()
                                for chunk in response.iter_content(65536):
                                    data.extend(chunk)
                                    if len(data) > 4 * 1024 * 1024:
                                        raise ValueError('Playlist too large')
                                body = relay.rewrite(data.decode('utf-8-sig'), response.url).encode()
                                started = True
                                self.send_response(200)
                                self.send_header('Content-Type', 'application/vnd.apple.mpegurl')
                                self.send_header('Content-Length', str(len(body)))
                                self.end_headers()
                                self.wfile.write(body)
                            else:
                                started = True
                                self.send_response(response.status_code)
                                for name in ('Content-Type', 'Content-Range', 'Accept-Ranges'):
                                    if name in response.headers:
                                        self.send_header(name, response.headers[name])
                                self.end_headers()
                                for chunk in response.iter_content(65536):
                                    if relay.closed.is_set():
                                        return
                                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception:
                    # Do not expose signed URLs or cookies in UI/server errors.
                    if not started and not self.wfile.closed:
                        try:
                            self.send_error(502, 'Upstream stream unavailable')
                        except OSError:
                            pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = Thread(target=lambda: self.server.serve_forever(poll_interval=.1), daemon=True)
        self.thread.start()
        self.url = self.register(source.url)

    def register(self, url):
        validate_url(url)
        if len(url) > 16384:
            raise ValueError('Upstream URL too long')
        with self.lock:
            if self.closed.is_set():
                raise RuntimeError('Relay closed')
            if url not in self.reverse:
                suffix = urlsplit(url).path.rsplit('/', 1)[-1].rsplit('.', 1)[-1].lower()
                suffix = '.' + suffix if suffix in ('m3u8', 'ts', 'mp4', 'm4s', 'aac', 'mp3', 'vtt', 'key') else ''
                path = '/' + secrets.token_urlsafe(24) + suffix
                self.reverse[url] = path
                self.urls[path] = url
                self.total_url_chars += len(url)
            self.urls.move_to_end(self.reverse[url])
            while len(self.urls) > self.MAX_URLS or self.total_url_chars > self.MAX_URL_CHARS:
                old_path, old_url = self.urls.popitem(last=False)
                if old_url == self.source.url:
                    self.urls[old_path] = old_url  # Keep the original VLC URL valid.
                    continue
                del self.reverse[old_url]
                self.total_url_chars -= len(old_url)
            return f'http://127.0.0.1:{self.server.server_port}{self.reverse[url]}'

    def rewrite(self, playlist, base):
        if not playlist.lstrip().startswith('#EXTM3U'):
            raise ValueError('Not an HLS playlist')
        lines = []
        for line in playlist.splitlines():
            if line.startswith('#'):
                line = URI.sub(lambda m: 'URI="' + self.register(urljoin(base, m[1])) + '"', line)
            elif line.strip():
                line = self.register(urljoin(base, line.strip()))
            lines.append(line)
        return '\n'.join(lines) + '\n'

    def close(self):
        if self.closed.is_set():
            return
        self.closed.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        with self.lock:
            self.urls.clear()
            self.reverse.clear()
            self.total_url_chars = 0
