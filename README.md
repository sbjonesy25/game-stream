# Streamwall

An Ubuntu desktop app for watching one to four HLS streams in a PySide6 window.
Paste a **web page URL** or a direct **`.m3u8` URL** into each pane. Chromium
looks for HLS network responses, then VLC plays just the video in the wall.
The normal viewing window does not embed the source website.

## Install on Ubuntu

Ubuntu 22.04 / 24.04 or newer, Python 3.10–3.15, and a desktop session are the intended
host. Allow a few hundred MB for Qt, Chromium, and VLC. Four HD streams can use
substantial bandwidth and CPU/GPU resources.

From this project directory:

```bash
./scripts/setup-ubuntu.sh
./scripts/run.sh
```

Setup uses `sudo` for Ubuntu packages, creates a project `.venv`, installs the
Python dependencies, and installs Playwright Chromium with its Linux libraries.
It does not change your browser profile. On a Wayland desktop, the launcher uses
XWayland (`QT_QPA_PLATFORM=xcb`) because VLC requires an X11 embedding window.
If XWayland is unavailable, use an Ubuntu on Xorg session.

Manual equivalent:

```bash
sudo apt-get update
sudo apt-get install -y python3-venv vlc libvlc5 libxcb-cursor0 libxcb-xinerama0 libxkbcommon-x11-0
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m playwright install --with-deps chromium
./scripts/run.sh
```

### Python version / PySide6 installation errors

If setup reports `No matching distribution found for PySide6==6.8.3`, update this
checkout with `git pull` and rerun `./scripts/setup-ubuntu.sh`. That old pin did
not support Python 3.14. The project now allows PySide6 6.10.1 or newer within
Qt 6, so pip can select a release compatible with your Python version.

Setup upgrades pip inside the virtual environment and prints its Python version.
You can choose an installed interpreter explicitly:

```bash
PYTHON_BIN=python3.14 ./scripts/setup-ubuntu.sh
```

If `.venv` already uses a different Python version, setup stops before changing
system packages. Rename that virtual environment to a backup, then rerun setup
to create a fresh one. For a non-default interpreter, install its corresponding
venv package (for example `python3.14-venv`) if Ubuntu reports missing `ensurepip`.

## Use

1. Choose **1–4 panes**. Paste a link into a pane and press **Load** or Enter.
2. Each stream starts muted. Use **Audio on/off** and its volume slider.
   **One audio source** automatically mutes other panes when you enable one.
   Uncheck it to mix several streams.
3. **Expand** fills the wall with one pane while other loaded streams keep running.
   **F11** makes the window fullscreen; combine these for a single fullscreen
   stream. **Esc** restores the grid and exits fullscreen.
4. **Pause / resume** and **Stop** operate independently. Reducing the pane count
   stops and mutes removed panes. Links remain in their input fields.
5. Press **Load** again to rediscover an expired or disconnected stream.

### Cookies and player interaction

Try normal loading first. If the page requires a login, consent, or a manual
player click, enable **Show resolver browser** before loading. You can interact
with that temporary Chromium window. It closes when an HLS response is found or
after the discovery timeout (about 75 seconds). Advertising popup tabs close
automatically. The wall itself displays only video.

You can also select a **Netscape-format `cookies.txt`** file using **Import
cookies…**. Export it yourself from a browser session you control; the app does
not extract browser databases or passwords. Cookies are imported for subsequent
loads, scoped to their domains/paths, and combined with cookies acquired during
page discovery. Session cookies with expiry `0` are supported. Do not commit or
share cookie files. Imported cookies can grant account access.

Each stream gets an independent browser context and loopback relay. Captured
Referer, Origin, User-Agent and same-origin Authorization headers accompany
playback requests. Playlists (including nested variants), media segments, HLS
keys, and initialization segments pass through the relay. Signed query strings
are preserved. Cookies are domain scoped; Authorization is not forwarded to
other origins. Relays bind only to `127.0.0.1` on random ports and use random
resource identifiers. Cookie values and signed URLs are not written to app logs.
Credentials remain in memory until the stream is stopped. **Clear cookies**
clears the selected import file for future loads; stop existing streams to discard
their active sessions. The original cookie file is not deleted or modified.

### Compatibility limits

This is a first implementation, not a guarantee that every streaming website
works. The resolver chooses the first successful HLS response; sites that serve
an HLS advertisement first may need site-specific selection logic. It does not
support DRM/EME video, DASH-only players, CAPTCHA solving, paywall bypasses, or
browser-bound authentication that cannot be replayed by an HTTP client. TLS
verification remains enabled. Use streams you are authorized to access.

Some players require ongoing browser heartbeats, fresh cookies, rotating tokens,
or anti-bot browser fingerprints. The browser closes after discovery, so those
sources may fail or expire; reload to get a fresh session. Pausing a live stream
for a long time can also outlive the server's live window. Browser mode lets you
complete ordinary login/player interactions but cannot guarantee compatibility.
A page may be offline, geo-restricted, or no longer associated with a live event.

The supplied `v2.streameast.ga` example was **not validated**: the development
cloud's egress proxy returned HTTP 403 before reaching the site. No environment
network settings were changed. Try that URL on your Ubuntu host; a failure there
needs diagnosis from the actual site's response.

## Development and validation

```bash
.venv/bin/python -m pip install -e '.[test]'
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
```

The tests exercise a real Chromium page that requests HLS, cookie/header-protected
nested playlists and segments, key/init URI rewriting, cookie isolation, byte
ranges, invalid URLs, Qt layouts/audio/fullscreen controls, and real HLS decoding
through the relay with FFmpeg. The FFmpeg test explicitly skips when FFmpeg is
absent. The Chromium test requires Playwright's browser or a system `chromium` /
`chromium-browser`; set `STREAMWALL_CHROMIUM=/path/to/chromium` to choose one.

Current development validation: **10 tests passed on Python 3.12 and 3.14** with
PySide6 6.12.0, including Chromium discovery and FFmpeg decoding. Qt control tests
use a mock VLC instance: native VLC video
embedding, real audio output, and GPU playback still need the Ubuntu host smoke
test below. This cloud machine lacks libVLC and cannot install system packages.

Host smoke test:

- Launch the app, load a known-working HLS URL, and confirm moving video.
- Load four independent streams, switch audible panes, and verify volume/mute.
- Expand one pane, press F11, then Esc; check the grid returns.
- Reduce from four panes to one; check hidden streams stop and are silent.
- Try your source page with browser mode and cookies as needed; stop/close the
  app during resolution and confirm the temporary browsers exit.

No environment draft or publication settings are changed by this project.
