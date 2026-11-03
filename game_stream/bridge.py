"""Bounded in-memory live HLS from fragments decoded by a running browser."""
import base64
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import math
import secrets
from threading import Event, Lock, Thread
from time import monotonic


class BrowserRelay:
    MAX_BYTES = 48 * 1024 * 1024
    MAX_SEGMENTS = 8

    def __init__(self):
        self.lock = Lock()
        self.closed = Event()
        self.segments = OrderedDict()
        self.total_bytes = 0
        self.next_sequence = 0
        self.last_upstream = None
        self.selected = None
        self.discontinuities = 0
        self.target_duration = 1
        self.updated = monotonic()
        self.prefix = '/' + secrets.token_urlsafe(24)
        relay = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                error = None
                with relay.lock:
                    if self.path == relay.prefix + '/live.m3u8':
                        if not relay.segments:
                            error = 503
                        else:
                            body = relay.playlist().encode()
                        mime = 'application/vnd.apple.mpegurl'
                    elif self.path.startswith(relay.prefix + '/'):
                        name = self.path[len(relay.prefix) + 1:]
                        key = name[:-3] if name.endswith('.ts') else ''
                        entry = relay.segments.get(int(key)) if key.isdecimal() else None
                        if entry is None:
                            error = 404
                        else:
                            body, mime = entry['bytes'], 'video/mp2t'
                    else:
                        error = 404
                # Never write to a client while holding the fragment cache lock.
                self.connection.settimeout(5)
                if error:
                    self.send_error(error)
                    return
                try:
                    self.send_response(200)
                    self.send_header('Content-Type', mime)
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = Thread(target=lambda: self.server.serve_forever(poll_interval=0.1), daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}{self.prefix}/live.m3u8'

    @property
    def ready(self):
        with self.lock:
            return len(self.segments) >= 3

    def push(self, player, fragment):
        """True = accepted, None = skip fragment, False = stop this player."""
        if self.closed.is_set():
            return False
        with self.lock:
            if self.selected is not None and self.selected != player:
                return False  # Avoid decoding/copying fragments from other players.
        try:
            sn, cc = fragment['sequence'], fragment['cc']
            duration = float(fragment['duration'])
            encoded = fragment['bytes']
            if (type(sn) is not int or type(cc) is not int or not 0 <= sn < 2**53 or
                    not 0 <= cc < 2**31 or not math.isfinite(duration) or not 0 < duration <= 60 or
                    not isinstance(encoded, str) or len(encoded) > 23 * 1024 * 1024):
                return None
            data = base64.b64decode(encoded, validate=True)
            if (not 940 <= len(data) <= 16 * 1024 * 1024 or len(data) % 188 or
                    any(data[i * 188] != 0x47 for i in range(5))):
                return None
        except (KeyError, ValueError, TypeError):
            return None
        with self.lock:
            if self.closed.is_set() or (self.selected is not None and self.selected != player):
                return False
            self.selected = player
            if self.last_upstream and cc == self.last_upstream[1] and sn <= self.last_upstream[0]:
                return None  # Skip a retry/duplicate, without blacklisting this player.
            discontinuity = bool(self.last_upstream and (cc != self.last_upstream[1] or sn != self.last_upstream[0] + 1))
            self.segments[self.next_sequence] = dict(bytes=data, duration=duration, discontinuity=discontinuity,
                                                     disc_sequence=self.discontinuities)
            self.target_duration = max(self.target_duration, math.ceil(duration))
            if discontinuity:
                self.discontinuities += 1
            self.next_sequence += 1
            self.last_upstream = (sn, cc)
            self.total_bytes += len(data)
            while len(self.segments) > self.MAX_SEGMENTS or self.total_bytes > self.MAX_BYTES:
                _, old = self.segments.popitem(last=False)
                self.total_bytes -= len(old['bytes'])
            self.updated = monotonic()
            return True

    def playlist(self):
        # Caller holds lock. Keep a short live window while retaining older
        # fragments for VLC requests that race a playlist refresh.
        rows = list(self.segments.items())[-6:]
        target = self.target_duration
        lines = ['#EXTM3U', '#EXT-X-VERSION:3', f'#EXT-X-TARGETDURATION:{target}',
                 f'#EXT-X-MEDIA-SEQUENCE:{rows[0][0]}',
                 f"#EXT-X-DISCONTINUITY-SEQUENCE:{rows[0][1]['disc_sequence']}"]
        for sn, entry in rows:
            if entry['discontinuity']:
                lines.append('#EXT-X-DISCONTINUITY')
            lines.extend([f"#EXTINF:{entry['duration']:.6f},", f'{sn}.ts'])
        return '\n'.join(lines) + '\n'

    def close(self):
        if self.closed.is_set():
            return
        self.closed.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        with self.lock:
            self.segments.clear()
            self.total_bytes = 0
            self.selected = None
