# Game Stream

An Ubuntu desktop app for watching one to four HLS streams in a PySide6 window.
Paste a **web page URL** or a direct **`.m3u8` URL** into each pane. Chromium
can relay decoded MPEG-TS fragments from Hls.js/Clappr players or discover
ordinary HLS URLs, then VLC plays just the video in the wall.
The normal viewing window does not embed the source website.

## Shareable Ubuntu installer

Download **[game-stream_0.2.0_amd64.deb](releases/game-stream_0.2.0_amd64.deb)**
from `releases/` and install with:

```bash
sudo apt install ./game-stream_0.2.0_amd64.deb
```

This is the Ubuntu/Linux installer; Android APK files do not run on Ubuntu.
The package targets **Ubuntu 22.04 / 24.04 / 26.04 amd64 (x86-64)** with system
Python 3.10–3.15. It installs the app, icon, launcher and declared native libraries.
Open **Applications → Game Stream** and pin its running icon if desired.

**First launch needs internet access and about 2 GB free disk space.** A progress
dialog sets up a private Python environment and Chromium browser under
`~/.local/share/game-stream/`. Python packages come from PyPI; the browser comes
from Playwright's download service. Setup runs as the desktop user, never from
root package-maintainer scripts. Do not run the app with sudo. If setup fails,
launch again; diagnostics are in `~/.local/state/game-stream/setup.log`.
A failed update keeps the previous working runtime until a replacement is ready.
The small `.deb` is intentionally not an offline runtime bundle. SHA256 checksums
are included in `releases/SHA256SUMS`.

Build a fresh package from a configured source checkout with
`./scripts/build-deb.sh` (requires `dpkg-deb`). It writes the installer and checksum
to `releases/`. The package and its checksum are tracked in this repository for
sharing. Native apt installation on each Ubuntu release still needs a host test;
validation here builds/extracts the package, validates the launcher, checks the
bundled wheel checksum, and tests the user-runtime update/rollback logic. A real
relocated virtual environment also ran the installed wheel's Qt UI using existing
verified dependencies; a clean Ubuntu apt installation and first-run network
download still require a host test.

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
It also installs a per-user application launcher and icon. It does not change
browser profiles or dock favorites. On a Wayland desktop, the launcher uses
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

### Launch from Applications and pin to the taskbar

The app is now **Game Stream**, with the package/command and desktop ID
`game-stream`. Its window, Applications entry, dock grouping and generated icon
use that identity.

For an existing checkout with a working `.venv`, close the app and run:

```bash
git pull
./scripts/install-desktop.sh
```

Open Ubuntu **Applications**, search **Game Stream**, and launch it. Right-click
its dock icon and choose **Pin to Dash** / **Add to Favorites** (the wording varies
by Ubuntu version). You can also pin its Applications entry. This last pinning
step is done on your desktop; the installer preserves existing favorites.

The installer needs no sudo and can be rerun. It creates
`~/.local/share/applications/game-stream.desktop` and hicolor icons from 16 to
512 pixels, respecting `XDG_DATA_HOME` when configured. The shortcut uses the
checkout's absolute launcher path, so spaces are supported and a terminal is not
required. Keep the checkout and `.venv` in place; rerun the installer after moving
them. `StartupWMClass`, Qt's desktop ID, and the X11 application name are all
`game-stream`, so the running window groups under the pinned icon on Ubuntu.

`./scripts/run.sh` still works directly after `git pull`, even before reinstalling
Python package metadata. A fresh setup installs the `.venv/bin/game-stream`
command and removes the obsolete `streamwall` package/console entry if present.
The Python module is now `game_stream`.

### Restore after closing or crashing

On the next launch, **Restore last session?** offers to restore saved links,
pane count, fullscreen/expanded layout, window geometry, per-pane volumes,
audio/mute selection, Solo audio, and Show browser settings. Choose **Yes** to
reconnect only streams that were active or loading. Links in stopped panes are
restored without starting them. Choose **No** to start a fresh session.

Settings checkpoint after edits (about 300 ms), periodically (about 2 seconds),
and before shutdown. Writes use an atomic replacement and flush to disk, so a
process crash leaves the most recent complete checkpoint. Abrupt exits are
identified in the next restore prompt. A normal close keeps the active stream
list from before browser teardown, allowing it to reconnect next time too.
Edits since the last checkpoint can be lost in a crash.

State lives in `~/.local/state/game-stream/session.json`, or
`$XDG_STATE_HOME/game-stream/session.json`, with owner-only file permissions.
It contains your input URLs, which may themselves contain private tokens. Browser
cookies, passwords, resolved segment URLs, and temporary media are not stored.
If you imported a cookie file, only its file path is remembered; the file must
still exist and contain usable cookies. Browsers start fresh during recovery,
so a site may require you to press Play, log in, or refresh cookies again. Live
streams reconnect at the current live point rather than restoring a seek time.

To forget recovery history, close the app and remove the session file. A corrupt
or unsupported session displays a warning and starts empty. If saving fails,
the window title reports it and closing displays a warning rather than silently
claiming that the current session was saved.

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
   Game Stream to close the associated browser, worker, and local relay.
3. Use **Audio on/off** and each pane's volume slider. **Solo audio** automatically
   mutes other panes when you enable one. Uncheck it to mix several streams.
4. **Expand** fills the wall with one pane and selects its audio while other loaded streams keep running.
   **F11** makes the window fullscreen; combine these for a single fullscreen
   stream. **Esc** restores the grid and exits fullscreen.
5. Fullscreen removes all reserved control areas and outer margins. Videos have
   **2 logical pixels of spacing** between them. Move the mouse to reveal floating
   controls for the quadrant under the pointer; only one pane's controls appear
   at a time, preventing overlap. They fade out after about 2 seconds without
   mouse/keyboard activity. Fullscreen hides URL fields and provides a compact
   **Reload** button; leave fullscreen to edit URLs. Buttons remain compact in
   normal windowed mode. Source aspect ratios are
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

### Games menu and Preferences

Open **Preferences** and set **Sports root page**, defaulting to
`https://v2.streameast.ga/`. Choose **Games → Refresh Games**, then navigate
**sport → game → Load in quadrant 1–4**. The target pane receives the event-page
link and loads it automatically. Selecting a quadrant outside the current pane
count enables it. Selecting a hidden pane returns to the grid so you can see it.
Game selections try the source player's Play control automatically; login,
consent, popups or a changed player can still require a manual click in its browser.

The menu groups NFL, NBA, MLB, NHL, NCAAF, NCAAB, WNBA, soccer and other recognized
sports. Refresh runs in the background without stopping playing streams. Failed
refreshes keep the last successful list and show the error in **Games (!)**.
The last refresh time is displayed, since cached game links can become stale.
Changing the configured root clears the old provider's list and starts a new
refresh. **Use a browser to render the game list** is useful for JavaScript
indexes. Even with it unchecked, an empty/failed HTTP result can fall back to
browser rendering. No site challenge or access restriction is bypassed.

Preferences are saved to `~/.config/game-stream/config.json` (or
`$XDG_CONFIG_HOME/game-stream/config.json`). See [`config.example.json`](config.example.json):

```json
{
  "root_url": "https://v2.streameast.ga/",
  "catalog_browser": false,
  "refresh_on_start": true,
  "double_tap_ms": 350
}
```

You can edit the file or use the Preferences dialog. **Refresh Games** reloads
manual file changes. The cached catalog is private to your user under
`~/.cache/game-stream/catalog.json` (`XDG_CACHE_HOME` is respected). Cookies and
passwords are not stored in Preferences or the catalog. The imported cookie-file
reference from your session can be used by the browser-based index refresh.

Parsing recognizes same-site event anchors with sport/matchup paths, such as
`/nfl/team-a-vs-team-b/1`. It handles relative links, nested anchor content and
server duplicates. It does not promise support for every website or every future
layout. A challenge/login page, no scheduled events, or a changed URL structure
can produce no games. The development cloud cannot access the live configured
root, so live root-page behavior needs testing on your Ubuntu host. Local static
and JavaScript-rendered indexes are tested, and the saved site HTML supplied
previously yielded its NFL game link.

### Keyboard and double-click controls

Shortcuts work while Game Stream has focus, including its floating controls.
They do not intercept digits typed into a URL or other text field, dialogs,
modified shortcuts, or open dropdowns. Keys for panes outside the selected
1–4 pane count are ignored. Holding a key does not repeatedly toggle it.

| Key / action | Result |
| --- | --- |
| `1`, `2`, `3`, `4` | Select only that quadrant's audio, even with Solo audio unchecked. |
| Double-tap `1`, `2`, `3`, `4` | Same zoom/audio action as `5`, `6`, `7`, `8`. Single taps still select audio immediately. |
| `0` | Mute all; press again to restore the last selected audio source. |
| `5`, `6`, `7`, `8` | Zoom quadrant 1, 2, 3, or 4 and select its audio. Enter fullscreen if needed. |
| Same zoom key again | Return to the selected pane grid, staying fullscreen and preserving audio/mute. |
| Double-click video while fullscreen | Zoom that quadrant and select its audio; double-click again to restore the grid without changing audio/mute. |
| `F11` | Toggle window fullscreen. |
| `Esc` | Restore the grid and leave fullscreen. |

The default double-tap interval is 350 ms; Preferences allows 150–800 ms.
Auto-repeat from holding a key is ignored. A second quick tap converts the first
audio tap into the corresponding zoom action; zooming back out preserves the
pre-tap mute/audio state just like `5–8`.

Zooming changes the view and audio selection; it does not restart other streams.
If you muted with `0` while zoomed, returning to the grid keeps that mute state.
`0` then restores the last selected source. A source removed by reducing the pane
count is not restored by the mute shortcut.

The fullscreen input layer receives double-clicks above VLC's native X11 video
windows. Both it and the compact controls are positioned within the fullscreen
canvas, rather than being moved into a desktop dock's smaller work area. They
hide when another application takes focus.

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

## Uninstall

Close Game Stream first. For the system package, run as your normal desktop user:

```bash
game-stream-uninstall
```

For a source checkout:

```bash
./scripts/uninstall.sh
```

Use `--dry-run` to preview. The helper removes this user's app launcher/icons and
only a marked, app-owned downloaded runtime; if a `.deb` is installed, it asks
sudo to remove that package too. Preferences, saved sessions, the catalog cache,
original cookie files, source checkout and source `.venv` are kept by default.
Add `--purge-user-data` to also remove this user's known configuration/session/
catalog files. Unrecognized files/directories are preserved. The helper removes
only the Game Stream pin from GNOME favorites, preserving other favorites.

Alternatively, `sudo apt remove game-stream` removes just the system package
and keeps per-user runtime/data. No package-removal hook scans or deletes user
home directories. Reinstalling can reuse the saved session. Other users can run
the uninstall helper for their own user-specific files before package removal.

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
`chromium-browser`; set `GAME_STREAM_CHROMIUM=/path/to/chromium` to choose one.

Current development validation: **42 tests passed on Python 3.12 and 3.14** with
PySide6 6.12.0 and Playwright 1.64.1, including Chromium discovery and FFmpeg
decoding. Added tests exercise a JSON/image-labelled transport in a real browser,
continued fragment delivery after handoff, bounded relay memory, discontinuities,
Stop/reload cleanup, fullscreen 2-pixel geometry, and fading/revealing controls.
UI tests also cover number/keypad shortcuts, URL editing protection, remembered
mute state, zoom/audio round trips, fullscreen double-click input, and two-pane
hover controls that stay within their pane and never appear simultaneously.
Recovery tests cover private atomic writes, failed replacements, malformed state,
process termination, restore/decline behavior, settings checkpoints and clean
shutdown's active-stream flags. Desktop tests validate the launcher, icon sizes,
matching application IDs, repeat installation, and actual GIO execution from
paths containing spaces and special characters. Added checks cover double-tap
timing and audio/zoom parity, multi-sport parsing, rendered index discovery,
config/cache validation, target-quadrant menu actions, failed refresh retention,
in-flight root changes, safe uninstall ownership checks, package contents, and
runtime staging/failed-update rollback. All imported Qt modules are supplied by
PySide6-Essentials; unused Qt WebEngine/Addons are not required by fresh installs.
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
- With two panes fullscreen, move between them: verify only the hovered pane's
  compact controls appear, fit inside it, and remain clickable above native video.
- Test `1–4`, `0`, and `5–8`; double-click native video to zoom and return to the
  grid. Check the viewed source retains audio and explicit mute stays muted.
- Type a URL containing digits: verify shortcuts do not interrupt editing.
- Stop/reload a pane during playback and discovery; verify its browser exits.
- Set a root in Preferences, refresh the catalog, and load a real event into each
  target quadrant. Verify refreshed links and automated Play on your host.
- Double-tap `1–4`, and try slow taps: quick taps zoom; slow taps stay audio-only.
- Install the `.deb` on a clean Ubuntu amd64 desktop and complete first-run setup.
  Test the Applications entry, dock pin, update, and uninstall behavior there.
- Keep a session playing long enough to exercise the site's authorization renewal.
- Run `./scripts/install-desktop.sh`, launch Game Stream from Applications, and
  pin the running icon. Verify it groups under that icon rather than Python.
- Close with active streams and relaunch: accept restoration and verify the links,
  layout, volumes and selected audio return. Repeat after an unexpected exit.
  Source browsers may need another Play click.

No environment draft or publication settings are changed by this project.
