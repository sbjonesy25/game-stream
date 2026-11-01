"""Discover HLS requests in a disposable Chromium session."""
from dataclasses import dataclass, field
from http.cookiejar import MozillaCookieJar
from threading import Event
from time import monotonic
from time import time
from urllib.parse import urlsplit
import os
import shutil


@dataclass
class Source:
    url: str
    headers: dict = field(default_factory=dict)
    cookies: list = field(default_factory=list)


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


def resolve(url, cookie_file='', visible=False, cancel=None, timeout=75):
    from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout
    validate_url(url)
    cookies = load_cookies(cookie_file)
    if urlsplit(url).path.lower().endswith('.m3u8'):
        return Source(url, cookies=cookies)
    cancel = cancel or Event()
    with sync_playwright() as p:
        executable = os.environ.get('STREAMWALL_CHROMIUM')
        if not executable and not os.path.exists(p.chromium.executable_path):
            executable = shutil.which('chromium') or shutil.which('chromium-browser')
        browser = p.chromium.launch(executable_path=executable, headless=not visible,
                                    args=['--autoplay-policy=no-user-gesture-required'])
        try:
            context = browser.new_context()
            if cookies:
                context.add_cookies(cookies)
            page = context.new_page()
            # Do not allow advertising popups to accumulate.
            context.on('page', lambda popup: popup.close())
            found = []

            def inspect(response):
                mime = response.headers.get('content-type', '').lower()
                if response.ok and ('.m3u8' in urlsplit(response.url).path.lower() or
                                    'mpegurl' in mime):
                    found.append(response)

            page.on('response', inspect)
            deadline = monotonic() + timeout
            try:
                page.goto(url, wait_until='domcontentloaded', timeout=30000)
            except BrowserTimeout:
                pass  # Some live pages never finish loading; inspect their requests.
            last_click = 0
            while not cancel.is_set() and monotonic() < deadline:
                if found:
                    response = found[0]
                    headers = response.request.all_headers()
                    kept = {k: v for k, v in headers.items()
                            if k.lower() in ('referer', 'origin', 'user-agent', 'authorization')}
                    return Source(response.url, kept, context.cookies())
                # Try actual player controls, including embedded iframe players.
                if monotonic() - last_click > 5:
                    for frame in page.frames:
                        for selector in ('video', '.vjs-big-play-button', '.jw-icon-playback',
                                         'button[aria-label="Play"]'):
                            try:
                                frame.locator(selector).first.click(timeout=250)
                            except Exception:
                                pass
                    last_click = monotonic()
                page.wait_for_timeout(200)
            if cancel.is_set():
                raise RuntimeError('Cancelled')
            raise RuntimeError('No HLS stream detected. Try browser mode, import cookies, or use a direct .m3u8 URL.')
        finally:
            browser.close()
