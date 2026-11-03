"""Discover ordinary HLS or keep a browser session relaying decoded media."""
from dataclasses import dataclass, field
from http.cookiejar import MozillaCookieJar
from threading import Event
from time import monotonic
from time import time
from urllib.parse import urlsplit
import os
import shutil
from pathlib import Path

BROWSER_BRIDGE_GRACE = 15
COOKIE_REFRESH_INTERVAL = 5


def live_manifest(response):
    """A page's incidental/VOD playlist is not proof of a playable live source."""
    try:
        if response.request.timing.get('responseEnd', -1) < 0:
            return None  # Do not block the resolver on an unfinished response body.
        length = response.headers.get('content-length', '')
        if length.isdecimal() and int(length) > 256 * 1024:
            return False
        body = response.body()
        if len(body) > 256 * 1024:
            return False
        text = body.decode('utf-8-sig').strip()
        return (text.startswith('#EXTM3U') and '#EXT-X-ENDLIST' not in text and
                '#EXT-X-PLAYLIST-TYPE:VOD' not in text and
                ('#EXTINF:' in text or '#EXT-X-STREAM-INF:' in text))
    except Exception:
        return False


def response_source(response, context):
    headers = response.request.all_headers()
    kept = {k: v for k, v in headers.items()
            if k.lower() in ('referer', 'origin', 'user-agent', 'authorization')}
    return Source(response.url, kept, context.cookies())


@dataclass
class Source:
    url: str
    headers: dict = field(default_factory=dict)
    cookies: list = field(default_factory=list)
    relay: object = field(default=None, repr=False)


def validate_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Enter an HTTP or HTTPS URL without embedded credentials.')
    return url


def load_cookies(path):
    if not path:
        return []
    jar = MozillaCookieJar(path)
    jar.load(ignore_discard=True, ignore_expires=True)
    result = []
    for cookie in jar:
        if cookie.expires and cookie.expires < time():
            continue
        item = dict(name=cookie.name, value=cookie.value, domain=cookie.domain,
                    path=cookie.path or '/', secure=cookie.secure)
        if cookie.expires:
            item['expires'] = cookie.expires
        result.append(item)
    return result


def resolve(url, cookie_file='', visible=False, cancel=None, timeout=150,
            on_ready=None, on_status=None, auto_play=False, max_height=720):
    """With on_ready, run a browser bridge until cancellation; otherwise discover HLS."""
    validate_url(url)
    if type(max_height) is not int or max_height not in (0, 720, 1080):
        raise ValueError('Invalid browser source quality')
    cookies = load_cookies(cookie_file)
    if urlsplit(url).path.lower().endswith('.m3u8'):
        source = Source(url, cookies=cookies)
        if on_ready:
            on_ready(source)
        return source
    from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout
    from .bridge import BrowserRelay
    cancel = cancel or Event()
    # Four independent automation drivers must not each wait for a multi-GB
    # Node heap before collecting transient base64/JSON fragment messages.
    # Preserve any explicit user Node configuration.
    os.environ.setdefault('NODE_OPTIONS', '--max-old-space-size=512')
    with sync_playwright() as p:
        executable = os.environ.get('GAME_STREAM_CHROMIUM') or os.environ.get('STREAMWALL_CHROMIUM')
        if not executable and not os.path.exists(p.chromium.executable_path):
            executable = shutil.which('chromium') or shutil.which('chromium-browser')
        browser = p.chromium.launch(executable_path=executable, headless=not visible,
            args=['--mute-audio', '--autoplay-policy=no-user-gesture-required', '--disable-background-timer-throttling',
                  '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows'])
        bridge = None
        announced = False
        ordinary_source = None
        cookie_refresh = 0
        try:
            context = browser.new_context()
            if cookies:
                context.add_cookies(cookies)
            if on_ready:
                def fragment(binding, message):
                    nonlocal bridge
                    if cancel.is_set() or not isinstance(message, dict):
                        return
                    if bridge is None:
                        bridge = BrowserRelay()
                    return bridge.push((binding['frame'], message.get('player')), message)
                context.expose_binding('__game_stream_fragment', fragment)
                script = Path(__file__).with_name('browser_bridge.js').read_text()
                context.add_init_script(script=f'globalThis.__game_stream_max_height = {max_height};\n' + script)
            page = context.new_page()
            context.on('page', lambda popup: popup.close())
            found = []

            def inspect(response):
                mime = response.headers.get('content-type', '').lower()
                if response.ok and ('.m3u8' in urlsplit(response.url).path.lower() or
                                    'mpegurl' in mime):
                    found.append(dict(response=response, live=None))
                    del found[:-8]  # Keep candidate response objects bounded.

            page.on('response', inspect)
            deadline = monotonic() + timeout
            try:
                page.goto(url, wait_until='domcontentloaded', timeout=30000)
            except BrowserTimeout:
                pass
            last_click = 0
            playing_since = None
            previous_frames = []
            while not cancel.is_set():
                if page.is_closed():
                    raise RuntimeError('The source browser was closed. Load again and leave it running during playback.')
                if bridge and bridge.ready and announced != 'bridge':
                    if not announced:
                        page.remove_listener('response', inspect)
                    announced = 'bridge'
                    ordinary_source = None
                    found.clear()
                    source = Source(bridge.url, relay=bridge)
                    on_ready(source)
                    if on_status:
                        on_status('Browser relay connected — keep the source browser running')
                    if visible:
                        try:
                            session = context.new_cdp_session(page)
                            window = session.send('Browser.getWindowForTarget')
                            session.send('Browser.setWindowBounds', {
                                'windowId': window['windowId'], 'bounds': {'windowState': 'minimized'}})
                            session.detach()
                        except Exception:
                            pass  # Window managers may not support minimizing via CDP.
                if not announced and found and not on_ready:
                    return response_source(found[0]['response'], context)
                if not announced and on_ready:
                    active_frames = []
                    for frame in page.frames:
                        try:
                            if frame.evaluate("""() => [...document.querySelectorAll('video')].some(
                                    v => !v.paused && !v.ended && v.readyState >= 2)"""):
                                active_frames.append(frame)
                        except Exception:
                            pass
                    if not active_frames:
                        playing_since = None
                    elif active_frames != previous_frames or playing_since is None:
                        playing_since = monotonic()
                    previous_frames = active_frames
                    if (playing_since is not None and monotonic() - playing_since >= BROWSER_BRIDGE_GRACE
                            and (bridge is None or not bridge.selected)):
                        for candidate in tuple(reversed(found)):
                            response = candidate['response']
                            try:
                                if response.request.frame not in active_frames:
                                    continue
                            except Exception:
                                continue
                            if candidate['live'] is None:
                                candidate['live'] = live_manifest(response)
                            if not candidate['live']:
                                continue
                            ordinary_source = response_source(response, context)
                            announced = 'direct'
                            page.remove_listener('response', inspect)
                            found.clear()
                            on_ready(ordinary_source)
                            cookie_refresh = monotonic()
                            if on_status:
                                on_status('Live HLS handoff — keeping the source browser open')
                            break
                if ordinary_source and monotonic() - cookie_refresh >= COOKIE_REFRESH_INTERVAL:
                    ordinary_source.cookies = context.cookies()
                    cookie_refresh = monotonic()
                if not announced and monotonic() > deadline:
                    raise RuntimeError('No supported video detected. In browser mode, press Play and wait for video. '
                                       'Supported players: HLS URLs and Hls.js/Clappr MPEG-TS. DRM and DASH are unsupported.')
                # Never click a playing video: that would pause the source.
                # Explicit auto_play callers can try Play in visible mode too.
                if (not visible or auto_play) and not announced and monotonic() - last_click > 5:
                    for frame in page.frames:
                        try:
                            frame.evaluate('() => globalThis.__game_stream_scan?.()')
                            paused = frame.evaluate("""() => ![...document.querySelectorAll('video')].some(v => !v.paused)""")
                            if not paused:
                                continue
                        except Exception:
                            continue
                        clicked = False
                        for selector in ('.vjs-big-play-button', '.jw-icon-playback[aria-label="Play"]',
                                         '.play-wrapper', 'button[aria-label="Play"]'):
                            try:
                                control = frame.locator(selector).first
                                if control.is_visible():
                                    control.click(timeout=150)
                                    clicked = True
                                    break
                            except Exception:
                                pass
                        if not clicked:
                            try:
                                frame.evaluate("""() => { for (const v of document.querySelectorAll('video')) {
                                    v.muted = true; if (v.paused) v.play().catch(() => {});
                                }}""")
                            except Exception:
                                pass
                    last_click = monotonic()
                page.wait_for_timeout(500 if announced else 150)
            if not announced:
                raise RuntimeError('Cancelled')
        finally:
            try:
                browser.close()
            finally:
                if bridge:
                    bridge.close()
