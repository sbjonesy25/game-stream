import base64
import gzip
from http.server import SimpleHTTPRequestHandler
import json
from pathlib import Path
import shutil
import subprocess
from threading import Event, Thread
from time import monotonic, sleep

import pytest
import requests

from game_stream.cmaf import CmafRelay
from game_stream.mp4 import init_tracks, fragment_timing, boxes
from game_stream.resolver import resolve
from test_streams import serve


@pytest.fixture(scope='module')
def media(tmp_path_factory):
    if not shutil.which('ffmpeg'):
        pytest.skip('FFmpeg is required for real-media regression tests')
    root = tmp_path_factory.mktemp('cmaf-media')
    basic = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
             'testsrc2=size=320x180:rate=15', '-f', 'lavfi', '-i', 'sine=frequency=440',
             '-t', '10', '-c:v', 'libx264', '-preset', 'ultrafast', '-profile:v', 'baseline',
             '-level:v', '3.0', '-pix_fmt', 'yuv420p', '-threads', '1', '-c:a', 'aac', '-g', '15']
    for kind in ('ts', 'mp4', 'split', 'aes'):
        folder = root / kind
        folder.mkdir()
        if kind == 'split':
            options = ['-f', 'dash', '-seg_duration', '1', '-adaptation_sets', 'id=0,streams=v id=1,streams=a',
                       str(folder / 'media.mpd')]
        else:
            options = ['-hls_time', '1', '-hls_list_size', '0']
            if kind == 'mp4':
                options += ['-hls_segment_type', 'fmp4']
            if kind == 'aes':
                (folder / 'key.bin').write_bytes(b'0123456789ABCDEF')
                (folder / 'keyinfo').write_text('key.bin\n' + str(folder / 'key.bin') + '\n')
                options += ['-hls_key_info_file', str(folder / 'keyinfo')]
            options += [str(folder / 'media.m3u8')]
        subprocess.run(basic + options, check=True, timeout=30)
    return root


def packet(track, data, init=None):
    return dict(track=track, bytes=base64.b64encode(data).decode(),
                init=base64.b64encode(init).decode() if init else None)


def fill(relay, root):
    for track in range(2):
        init = (root / f'init-stream{track}.m4s').read_bytes()
        assert relay.push('player', packet(track + 1, init, init))
        for segment in sorted(root.glob(f'chunk-stream{track}-*.m4s')):
            assert relay.push('player', packet(track + 1, segment.read_bytes()))


def test_mp4_metadata_and_separate_audio_video_decode(media):
    folder = media / 'split'
    video_init = (folder / 'init-stream0.m4s').read_bytes()
    info = init_tracks(video_init)
    assert any(track['kind'] == 'video' for track in info.values())
    start, duration = fragment_timing(next(folder.glob('chunk-stream0-*.m4s')).read_bytes(), info)
    assert start >= 0 and .9 < duration < 1.1
    relay = CmafRelay()
    try:
        fill(relay, folder)
        assert relay.ready and relay.total_bytes <= relay.MAX_BYTES
        master = requests.get(relay.url, timeout=5)
        assert master.status_code == 200 and '#EXT-X-MEDIA:TYPE=AUDIO' in master.text
        result = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', relay.url,
                                 '-t', '1', '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'],
                                capture_output=True, timeout=15)
        assert result.returncode == 0, result.stderr.decode()
        assert requests.get(relay.url.replace('/master.m3u8', '/missing'), timeout=5).status_code == 404
    finally:
        relay.close()
    assert relay.total_bytes == 0 and not relay.tracks and not relay.thread.is_alive()


def test_cmaf_invalid_inputs_retries_track_windows_and_memory_bound(media):
    folder = media / 'split'
    init = (folder / 'init-stream0.m4s').read_bytes()
    parts = [p.read_bytes() for p in sorted(folder.glob('chunk-stream0-*.m4s'))]
    relay = CmafRelay()
    try:
        for value in ({}, packet(True, init, init), dict(track=1, bytes='not-base64'), packet(1, b'garbage!')):
            assert relay.push('player', value) is None
        assert relay.push('player', packet(1, init, init)) is True
        for part in parts:
            assert relay.push('player', packet(1, part)) is True
        assert len(relay.tracks['1']['segments']) == relay.MAX_SEGMENTS
        count = relay.next_sequence
        assert relay.push('player', packet(1, parts[-1])) is None
        assert relay.next_sequence == count
        assert relay.push('other-player', packet(1, parts[-1])) is False
        relay.MAX_BYTES = 10000
        relay.trim()
        assert 0 <= relay.total_bytes <= relay.MAX_BYTES
        assert not relay.ready
    finally:
        relay.close()


@pytest.mark.parametrize('data', [b'', b'1234567', b'\x00\x00\x00\x01moov', b'\xff\xff\xff\xffmoov',
                                      b'\x00\x00\x00\x08moovtrailer'])
def test_malformed_mp4_is_rejected(data):
    with pytest.raises(ValueError):
        init_tracks(data)


@pytest.mark.parametrize('kind', ['ts', 'mp4', 'mse', 'aes'])
def test_real_browser_player_reaches_relay_and_decodes_with_audio(media, tmp_path, kind):
    root = tmp_path
    bundle = gzip.decompress((Path(__file__).parent / 'fixtures/clappr-0.3.13.js.gz').read_bytes())
    (root / 'clappr.js').write_bytes(bundle)
    source = media / ('split' if kind == 'mse' else kind)
    for path in source.iterdir():
        shutil.copy2(path, root / path.name)
    if kind != 'mse':
        html = '''<div id="player"></div><script src="/clappr.js"></script><script>
        var player=new Clappr.Player({source:'/media.m3u8',parentId:'#player',autoPlay:true,mute:true,
                                     width:320,height:180});</script>'''
    else:
        # No Hls/Clappr instance is exposed: exercise the generic MediaSource fallback.
        names = [[p.name for p in sorted(root.glob(f'chunk-stream{index}-*.m4s'))] for index in range(2)]
        html = '''<video muted autoplay width="320" height="180"></video><script>
        const video=document.querySelector('video'), media=new MediaSource();
        video.src=URL.createObjectURL(media); video.play().catch(()=>{});
        media.addEventListener('sourceopen',()=>{
          const names=NAMES;
          ['video/mp4; codecs="avc1.42C01E"','audio/mp4; codecs="mp4a.40.2"'].forEach(async(mime,index)=>{
            const buffer=media.addSourceBuffer(mime);
            async function append(name){const value=await(await fetch('/'+name)).arrayBuffer();
              await new Promise((resolve,reject)=>{buffer.addEventListener('updateend',resolve,{once:true});
                buffer.addEventListener('error',reject,{once:true});buffer.appendBuffer(value);});
              await new Promise(r=>setTimeout(r,90));}
            await append('init-stream'+index+'.m4s');
            for(const name of names[index])await append(name);
          });
        },{once:true});</script>'''.replace('NAMES', json.dumps(names))
    (root / 'index.html').write_text(html)
    class Site(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs): super().__init__(*args, directory=str(root), **kwargs)
        def log_message(self, *args): pass
    stop = Event()
    observed, failures, consumers = [], [], []
    def ready(source):
        observed.append(source)
        def decode():
            try:
                deadline = monotonic() + 8
                while source.relay.next_sequence < 7 and monotonic() < deadline:
                    sleep(.03)
                result = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', source.url,
                                         '-t', '1', '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'],
                                        capture_output=True, timeout=15)
                assert result.returncode == 0, result.stderr.decode()
                if kind == 'mse':
                    assert isinstance(source.relay, CmafRelay)
            except Exception as exc:
                failures.append(exc)
            finally:
                stop.set()
        worker = Thread(target=decode)
        worker.start()
        consumers.append(worker)
    with serve(Site) as origin:
        resolve(origin + '/index.html', on_ready=ready, cancel=stop, timeout=15)
    for worker in consumers:
        worker.join(timeout=20)
    assert not failures, failures
    assert len(observed) == 1 and observed[0].relay.closed.is_set()


def test_pending_audio_from_other_frame_does_not_claim_video_or_mix_tracks(media):
    root = media / 'split'
    relay = CmafRelay()
    try:
        audio = (root / 'init-stream1.m4s').read_bytes()
        segment = next(root.glob('chunk-stream1-*.m4s')).read_bytes()
        assert relay.push('unrelated-frame', packet(1, audio, audio))
        assert relay.push('unrelated-frame', packet(1, segment))
        assert relay.selected is None and not relay.ready
        video = (root / 'init-stream0.m4s').read_bytes()
        part = next(root.glob('chunk-stream0-*.m4s')).read_bytes()
        assert relay.push('main-frame', packet(1, video, video))
        assert relay.push('main-frame', packet(1, part))
        assert relay.selected == 'main-frame'
        assert all(track['owner'] == 'main-frame' for track in relay.tracks.values())
        assert relay.push('unrelated-frame', packet(1, segment)) is False
    finally:
        relay.close()
