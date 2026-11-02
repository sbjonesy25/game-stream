#!/usr/bin/env python3
"""Four local 720p Hls.js players + FFmpeg readers; run separately from the app."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from threading import Event, Lock, Thread
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from game_stream.config import write_json
from game_stream.resources import memory_snapshot
from game_stream.resolver import resolve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hls-js', type=Path, required=True, help='Local hls.min.js, e.g. Hls.js 1.6.16')
    parser.add_argument('--seconds', type=int, default=600)
    parser.add_argument('--output', type=Path, default=Path('browser-resource-soak.json'))
    args = parser.parse_args()
    if args.seconds < 30 or not args.hls_js.is_file():
        parser.error('Supply hls.min.js and a duration of at least 30 seconds')
    with tempfile.TemporaryDirectory(prefix='game-stream-soak-') as temporary:
        root = Path(temporary)
        shutil.copy2(args.hls_js, root / 'hls.min.js')
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
            'testsrc2=size=1280x720:rate=15', '-f', 'lavfi', '-i', 'sine=frequency=440', '-t', '8',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-threads', '2', '-c:a', 'aac',
            '-g', '15', '-hls_time', '1', '-hls_list_size', '0', str(root / 'media.m3u8')], check=True)
        run_fixture(root, args.seconds, args.output)


def run_fixture(root, duration, output):
    import os
    started = monotonic()
    stop, lock = Event(), Lock()
    states, relays, decoders, failures, samples = {}, [], [], [], []
    # Worker disabled only in this synthetic fixture: keep complete TS payloads
    # at fragLoaded, matching the custom transport supported by the app bridge.
    html = b'''<video muted autoplay></video><script src="/hls.js"></script><script>
    const hls = new Hls({backBufferLength:Infinity, enableWorker:false, progressive:false});
    setTimeout(()=>{hls.attachMedia(document.querySelector('video')); hls.loadSource('/index.json');},300);
    hls.on(Hls.Events.MANIFEST_PARSED,()=>hls.media.play().catch(()=>{}));
    setInterval(()=>{let buffered=0;const b=hls.media.buffered;
      for(let i=0;i<b.length;i++)buffered+=b.end(i)-b.start(i);
      fetch('/stats?id='+location.search.slice(1), {method:'POST',body:JSON.stringify({buffered,
        back:hls.config.backBufferLength,forward:hls.config.maxBufferLength,time:hls.media.currentTime})});
    },5000);</script>'''

    class Site(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            with lock:
                states[self.path] = data
            self.send_response(204)
            self.end_headers()

        def do_GET(self):
            if self.path.startswith('/watch'):
                body, mime = html, 'text/html'
            elif self.path == '/hls.js':
                body, mime = (root / 'hls.min.js').read_bytes(), 'application/javascript'
            elif self.path == '/index.json':
                first = max(0, int(monotonic() - started) - 5)
                rows = ['#EXTM3U', '#EXT-X-VERSION:3', '#EXT-X-TARGETDURATION:1',
                        f'#EXT-X-MEDIA-SEQUENCE:{first}', f'#EXT-X-DISCONTINUITY-SEQUENCE:{(first + 7) // 8}']
                for i in range(first, first + 6):
                    if i % 8 == 0:
                        rows.append('#EXT-X-DISCONTINUITY')
                    rows += ['#EXTINF:1,', f'/segment/{i}.ts']
                # JSON MIME/path intentionally exercise the browser bridge.
                body, mime = ('\n'.join(rows) + '\n').encode(), 'application/json'
            elif self.path.startswith('/segment/'):
                number = int(self.path.rsplit('/', 1)[-1].split('.')[0])
                body, mime = (root / f'media{number % 8}.ts').read_bytes(), 'video/mp2t'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Site)
    Thread(target=server.serve_forever, daemon=True).start()

    def ready(source):
        if source.relay is None:
            raise RuntimeError('Expected browser bridge')
        decoder = subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'fatal', '-i', source.url,
            '-f', 'null', '-'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with lock:
            relays.append(source.relay)
            decoders.append(decoder)

    def run(index):
        try:
            resolve(f'http://127.0.0.1:{server.server_port}/watch?{index}', cancel=stop,
                    on_ready=ready, timeout=45)
        except Exception as exc:
            with lock:
                failures.append(type(exc).__name__ + ': ' + str(exc))

    threads = [Thread(target=run, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    try:
        while True:
            sample = dict(seconds=round(monotonic() - started), **memory_snapshot(os.getpid()))
            with lock:
                sample.update(relays=len(relays), segments=[r.next_sequence for r in relays],
                    cache_bytes=sum(r.total_bytes for r in relays), states=dict(states), failures=list(failures),
                    decoders_running=sum(p.poll() is None for p in decoders))
            samples.append(sample)
            write_json(output, dict(fixture='4 x 720p15 Hls.js players with complete TS events + FFmpeg readers', samples=samples))
            print(json.dumps(sample), flush=True)
            if failures or monotonic() - started >= duration:
                break
            stop.wait(min(15, max(0, duration - (monotonic() - started))))
    finally:
        stop.set()
        for decoder in decoders:
            if decoder.poll() is None:
                decoder.terminate()
        for thread in threads:
            thread.join(40)
        for decoder in decoders:
            try:
                decoder.wait(10)
            except subprocess.TimeoutExpired:
                decoder.kill()
                decoder.wait()
        server.shutdown()
        server.server_close()
        print('After cleanup: ' + json.dumps(memory_snapshot(os.getpid())), flush=True)
    if failures or len(relays) != 4 or samples[-1]['decoders_running'] != 4:
        raise SystemExit('Soak failed: check its report')


if __name__ == '__main__':
    main()
