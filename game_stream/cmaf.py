"""Bounded clear fragmented-MP4 relay for browser MediaSource playback."""
import base64
from collections import OrderedDict
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import math
import secrets
from threading import Event, Lock, Thread
from time import monotonic

from .mp4 import init_tracks, fragment_timing


class CmafRelay:
    MAX_BYTES = 48 * 1024 * 1024
    MAX_SEGMENTS = 8
    MAX_TRACKS = 4

    def __init__(self):
        self.lock = Lock()
        self.closed = Event()
        self.tracks = OrderedDict()
        self.selected = None
        self.expected_audio = False
        self.updated = monotonic()
        self.next_sequence = 0
        self.next_track = 0
        self.total_bytes = 0
        self.prefix = '/' + secrets.token_urlsafe(24)
        relay = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass

            def do_GET(self):
                with relay.lock:
                    result = relay.response(self.path)
                self.connection.settimeout(5)
                try:
                    if result is None:
                        self.send_error(404)
                        return
                    body, mime = result
                    self.send_response(200)
                    self.send_header('Content-Type', mime)
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = Thread(target=lambda: self.server.serve_forever(poll_interval=.1), daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}{self.prefix}/master.m3u8'

    def active_tracks(self):
        video = next(((key, value) for key, value in reversed(self.tracks.items()) if value['video'] and value['segments']), None)
        audio = next(((key, value) for key, value in reversed(self.tracks.items()) if not value['video'] and value['segments']), None)
        return video, audio

    @property
    def ready(self):
        with self.lock:
            video, audio = self.active_tracks()
            return bool(video and len(video[1]['segments']) >= 3 and
                        (not self.expected_audio or audio) and (not audio or len(audio[1]['segments']) >= 2))

    @property
    def segments(self):
        # Used only for lightweight health counters on the UI thread.
        return tuple(entry for track in tuple(self.tracks.values()) for entry in tuple(track['segments']))

    def push(self, player, message):
        if self.closed.is_set():
            return False
        with self.lock:
            if self.selected is not None and self.selected != player:
                return False
        try:
            track_id = message['track']
            if type(track_id) is not int or not 0 < track_id < 2**31:
                return None
            encoded = message['bytes']
            if not isinstance(encoded, str) or len(encoded) > 23 * 1024 * 1024:
                return None
            data = base64.b64decode(encoded, validate=True)
            if not 8 <= len(data) <= 16 * 1024 * 1024:
                return None
            encoded_init = message.get('init')
            if encoded_init is not None and (not isinstance(encoded_init, str) or len(encoded_init) > 1400000):
                return None
            initialization = base64.b64decode(encoded_init, validate=True) if encoded_init else None
            metadata = init_tracks(initialization) if initialization else None
            with self.lock:
                if self.closed.is_set() or (self.selected is not None and self.selected != player):
                    return False
                self.expected_audio |= message.get('audio') is True
                key, track = next(((key, track) for key, track in self.tracks.items()
                                   if track['owner'] == player and track['track_id'] == track_id), (None, None))
                if metadata:
                    init_key = hashlib.sha256(initialization).hexdigest()[:24]
                    if track is None:
                        self.next_track += 1
                        key = str(self.next_track)
                        track = dict(video=any(t['kind'] == 'video' for t in metadata.values()),
                                     segments=OrderedDict(), inits={}, last_start=None, discontinuity=False, sequence=0,
                                     owner=player, track_id=track_id, disc_sequence=0, audio=message.get('audio') is True)
                        self.tracks[key] = track
                    if init_key not in track['inits']:
                        track['inits'][init_key] = initialization
                        self.total_bytes += len(initialization)
                        track['discontinuity'] = bool(track['segments'])
                    track['init_key'], track['metadata'] = init_key, metadata
                if track is None:
                    return None
                track['audio'] |= message.get('audio') is True
                # An initialization append alone has no moof/mdat media.
                try:
                    start, duration = fragment_timing(data, track['metadata'])
                except ValueError:
                    self.trim()
                    return True if metadata else None
                if track['last_start'] is not None and start <= track['last_start'] and not track['discontinuity']:
                    return None
                if self.selected is None and track['video']:
                    self.selected = player
                    self.expected_audio = track['audio']
                    for old_key in tuple(self.tracks):
                        old = self.tracks[old_key]
                        if old['owner'] != player:
                            self.total_bytes -= sum(len(e['bytes']) for e in old['segments'].values()) + sum(map(len, old['inits'].values()))
                            del self.tracks[old_key]
                sequence = track['sequence']
                track['sequence'] += 1
                self.next_sequence += 1
                track['segments'][sequence] = dict(bytes=data, duration=duration, init=track['init_key'],
                                                   discontinuity=track['discontinuity'], disc_sequence=track['disc_sequence'])
                if track['discontinuity']:
                    track['disc_sequence'] += 1
                track['last_start'] = start
                track['discontinuity'] = False
                self.total_bytes += len(data)
                self.tracks.move_to_end(key)
                self.updated = monotonic()
                self.trim()
                return True
        except (KeyError, ValueError, TypeError, OverflowError):
            return None

    def trim(self):
        for track in self.tracks.values():
            while len(track['segments']) > self.MAX_SEGMENTS:
                _, entry = track['segments'].popitem(last=False)
                self.total_bytes -= len(entry['bytes'])
            used = {entry['init'] for entry in track['segments'].values()} | {track['init_key']}
            for key in tuple(track['inits']):
                if key not in used:
                    self.total_bytes -= len(track['inits'].pop(key))
        while len(self.tracks) > self.MAX_TRACKS:
            _, old = self.tracks.popitem(last=False)
            self.total_bytes -= sum(len(entry['bytes']) for entry in old['segments'].values()) + sum(map(len, old['inits'].values()))
        while self.total_bytes > self.MAX_BYTES:
            victim = next((track for track in self.tracks.values() if track['segments']), None)
            if victim is None:
                if self.tracks:
                    _, old = self.tracks.popitem(last=False)
                    self.total_bytes -= sum(map(len, old['inits'].values()))
                    continue
                break
            _, entry = victim['segments'].popitem(last=False)
            self.total_bytes -= len(entry['bytes'])

    def response(self, path):
        if path == self.prefix + '/master.m3u8':
            video, audio = self.active_tracks()
            if not video:
                return None
            lines = ['#EXTM3U', '#EXT-X-VERSION:7']
            if audio:
                lines.append(f'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="Audio",DEFAULT=YES,AUTOSELECT=YES,URI="{audio[0]}/live.m3u8"')
            lines.extend(['#EXT-X-STREAM-INF:BANDWIDTH=8000000' + (',AUDIO="audio"' if audio else ''), f'{video[0]}/live.m3u8'])
            return ('\n'.join(lines) + '\n').encode(), 'application/vnd.apple.mpegurl'
        if not path.startswith(self.prefix + '/'):
            return None
        parts = path[len(self.prefix) + 1:].split('/')
        if len(parts) != 2 or parts[0] not in self.tracks:
            return None
        track = self.tracks[parts[0]]
        name = parts[1]
        if name == 'live.m3u8' and track['segments']:
            rows = list(track['segments'].items())[-6:]
            lines = ['#EXTM3U', '#EXT-X-VERSION:7', f'#EXT-X-TARGETDURATION:{max(math.ceil(e["duration"]) for _, e in rows)}',
                     f'#EXT-X-MEDIA-SEQUENCE:{rows[0][0]}', f'#EXT-X-DISCONTINUITY-SEQUENCE:{rows[0][1]["disc_sequence"]}']
            previous = None
            for sn, entry in rows:
                if entry['discontinuity']:
                    lines.append('#EXT-X-DISCONTINUITY')
                if entry['init'] != previous:
                    lines.append(f'#EXT-X-MAP:URI="{entry["init"]}.mp4"')
                    previous = entry['init']
                lines.extend([f'#EXTINF:{entry["duration"]:.6f},', f'{sn}.m4s'])
            return ('\n'.join(lines) + '\n').encode(), 'application/vnd.apple.mpegurl'
        if name.endswith('.mp4') and name[:-4] in track['inits']:
            return track['inits'][name[:-4]], 'video/mp4' if track['video'] else 'audio/mp4'
        if name.endswith('.m4s') and name[:-4].isdecimal():
            entry = track['segments'].get(int(name[:-4]))
            return (entry['bytes'], 'video/mp4' if track['video'] else 'audio/mp4') if entry else None
        return None

    def close(self):
        if self.closed.is_set():
            return
        self.closed.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        with self.lock:
            self.tracks.clear()
            self.total_bytes = 0
            self.selected = None
