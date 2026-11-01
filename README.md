# Streamwall

An Ubuntu desktop app for watching one to four HLS streams in a PySide6 window.
Paste a **web page URL** or a direct **`.m3u8` URL** into each pane. Chromium
can relay decoded MPEG-TS fragments from Hls.js/Clappr players or discover
ordinary HLS URLs, then VLC plays just the video in the wall.
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

### Playwright browser installation on Ubuntu 26.04

If Python packages install but browser setup reports `Playwright does not support
chromium on ubuntu26.04-x64`, run `git pull` and rerun
`./scripts/setup-ubuntu.sh`. The original Playwright 1.51.0 pin predates this OS;
the project now uses 1.64.1, which includes Ubuntu 26.04 browser and dependency
definitions. Setup prints the installed Playwright version before installing
Chromium. Keep the existing `.venv`; rerunning setup upgrades the package and
installs the browser version that matches it.

## Use

1. Leave **Show browser** enabled (the default), choose **1–4 panes**, paste a
   page link, and press **Load** or Enter.
2. In the Chromium window, complete ordinary login/consent prompts and press
   **Play**. Wait for a few video segments to buffer (usually 10–20 seconds).
   With a supported Hls.js/Clappr MPEG-TS player, video starts in the app and the
   source browser minimizes automatically when the window manager permits it.
   **Keep that browser open**: it handles stream renewals and decoding the site's
   transport. The browser is muted; VLC owns the audible output. Stop or close
   Streamwall to close the associated browser, worker, and local relay.
3. Use **Audio on/off** and each pane's volume slider. **Solo audio** automatically
   mutes other panes when you enable one. Uncheck it to mix several streams.
4. **Expand** fills the wall with one pane while other loaded streams keep running.
   **F11** makes the window fullscreen; combine these for a single fullscreen
   stream. **Esc** restores the grid and exits fullscreen.
5. Fullscreen removes all reserved control areas and outer margins. Videos have
   **2 logical pixels of spacing** between them. Move the mouse to reveal floating
   controls; they fade out after about 2 seconds without mouse/keyboard activity.
   Buttons remain compact in normal windowed mode. Source aspect ratios are
   preserved, so VLC may still letterbox video within its pane.
6. **Pause** toggles pause/resume for VLC; the source browser keeps the live feed
   current. A long pause may outlive the rolling buffer, so Load again to reconnect
   if needed. **Stop** stops the pane and its source browser. Reducing the pane
   count also stops and mutes removed panes. Links stay in their input fields.
7. Press **Load** again to restart an expired/disconnected session. A replacement
   browser starts after the previous worker has finished shutting down.

For a direct `.m3u8` URL, no browser is needed. Ordinary detected HLS URLs can
also use the HTTP relay, allowing the browser to close after discovery. Uncheck
**Show browser** to try automatic playback without a visible window. Some sites
need a real player click, so visible browser mode is recommended first.

### How browser-assisted playback works

The supplied network log showed a JSON live index and image-labelled media
responses that the site's own player unwraps into MPEG-TS. There is no normal
`.m3u8` network request for the original resolver to detect. Cookie import alone
does not address that transport.

An init script runs in the source page and its frames, observes Hls.js/Clappr
clear MPEG-TS fragment events, and copies complete segments before the player
transfers their buffers. The browser continues fetching and decoding with its
own session. A bounded in-memory loopback relay exposes those segments as a
normal rolling HLS stream for VLC. It does not copy the website into the app,
reverse-engineer the site's decoder, record streams to disk, or extract DRM keys.
Each pane has an independent browser and relay. The first supported media player
in that session supplies its relay; other players cannot mix fragments into it.
The buffer retains at most 12 segments and 64 MiB per pane.

This mode keeps both Chromium and VLC active, so four HD streams can use more
CPU/GPU memory than four direct HLS URLs. Minimize the source windows rather
than closing them. The app disables browser background throttling and keeps
source video muted to avoid duplicate audio.

### Importing cookies

Select a **Netscape-format `cookies.txt`** file using **Cookies…**. Export it
yourself from a browser session you control; the app does not extract browser
databases or passwords. Cookies apply to subsequent loads, scoped to their
paths/domains. Brave's profile is not shared automatically. Session cookies with
expiry `0` are supported. Do not commit or share cookie files.

For ordinary HLS, captured Referer, Origin, User-Agent and same-origin
Authorization headers accompany relay requests. Nested playlists, media, keys,
and initialization segments pass through that relay. Signed query strings are
preserved, cookies stay domain scoped, and Authorization does not cross origins.
Both relay types bind only to `127.0.0.1` on random ports with random resource
paths. Cookie values and signed upstream URLs are not written to app logs.
**Clear cookies** clears the selected import file for future loads; stop existing
streams to discard active sessions. The original file stays untouched.

### Compatibility limits

Browser bridging currently supports **Hls.js/Clappr players emitting clear,
complete MPEG-TS fragments**. It does not handle DRM/EME, DASH-only players,
CMAF/fMP4 fragments, or separate audio-only tracks through the browser bridge.
Ordinary HLS still uses the existing HTTP relay where compatible. No CAPTCHA
solving or paywall bypass is provided. TLS verification remains enabled.

A page with an HLS advertisement before its main player may select the wrong
source. Pages can change their player APIs, lose connectivity, expire their
session, or require another interaction in the source browser. When the source
stalls, restore its browser and check playback, or press Load again. Closing the
source browser stops the relay. Use streams you are authorized to access.

Live access to the supplied site remains blocked by the development cloud's
egress policy. **Offline replay of the user's captured responses succeeded:**
Chromium ran the recorded player, the bridge delivered changing segments, and
FFmpeg decoded 16 seconds / 954 video frames through the new relay. The private
HAR, tokens, media, and site decoder code are not part of this repository.
This demonstrates the recorded transport, not a live test of current URLs or
long-running session renewal. No environment network settings were changed.

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

Current development validation: **18 tests passed on Python 3.12 and 3.14** with
PySide6 6.12.0 and Playwright 1.64.1, including Chromium discovery and FFmpeg
decoding. Added tests exercise a JSON/image-labelled transport in a real browser,
continued fragment delivery after handoff, bounded relay memory, discontinuities,
Stop/reload cleanup, fullscreen 2-pixel geometry, and fading/revealing controls.
Browser install dry runs verify download selection for Ubuntu 22.04,
24.04 and 26.04 x64 without downloading browsers or changing system packages.
This does not constitute a full install on each Ubuntu version. Qt control tests
use a mock VLC instance: native VLC video
embedding, real audio output, and GPU playback still need the Ubuntu host smoke
test below. This cloud machine lacks libVLC and cannot install system packages.

Host smoke test:

- Launch the app, load a known-working HLS URL, and confirm moving video.
- Load four independent streams, switch audible panes, and verify volume/mute.
- Expand one pane, press F11, then Esc; check the grid returns.
- Reduce from four panes to one; check hidden streams stop and are silent.
- With **Show browser** enabled, load your source page and press Play. Confirm
  video reaches the app, the source window minimizes, and audio comes only from
  the chosen VLC pane. Restore the source browser if another interaction is needed.
- Move the mouse in fullscreen, wait about 2 seconds, and check that controls fade
  without moving/resizing the video. Test F11/Esc while a floating control has focus.
- Stop/reload a pane during playback and discovery; verify its browser exits.
- Keep a session playing long enough to exercise the site's authorization renewal.

No environment draft or publication settings are changed by this project.
