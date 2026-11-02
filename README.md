# Game Stream

An Ubuntu desktop app for watching one to four HLS streams in a PySide6 window.
Paste a **web page URL** or a direct **`.m3u8` URL** into each pane. Chromium
can relay decoded MPEG-TS fragments from Hls.js/Clappr players or discover
ordinary HLS URLs, then VLC plays just the video in the wall.
The normal viewing window does not embed the source website.

## Shareable Ubuntu installer

Download **[game-stream_0.2.5_amd64.deb](releases/game-stream_0.2.5_amd64.deb)**
from `releases/` and install with:

```bash
sudo apt install ./game-stream_0.2.5_amd64.deb
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

For every application update, bump the patch version and run
`./scripts/release.sh` (requires `dpkg-deb`). It rebuilds the `.deb`, standalone
uninstall `.sh` and uninstaller bundle, runs the test suite, and verifies that
packaged source/icons/helpers/README match the checkout. `scripts/check-release.py`
fails on stale artifacts. Stage and push all rebuilt artifacts with the source.
For build-only use `./scripts/build-deb.sh`. Outputs and checksums go to `releases/`. The package and its checksum are tracked in this repository for
sharing. Native apt installation on each Ubuntu release still needs a host test;
validation here builds/extracts the package, validates the launcher, checks the
bundled wheel checksum, and tests the user-runtime update/rollback logic. A real
relocated virtual environment also ran the installed wheel's Qt UI using existing
verified dependencies; a clean Ubuntu apt installation and first-run network
download still require a host test.

### Update an installed application

Install the current v0.2.5 package to get these controls. After that, use **Preferences → Updates → Save & check for updates…**. When a newer version is available, choose Yes to download it. Playback
continues during the download; you can cancel without closing the app. The app
checks the size, SHA256 checksum and Debian package name/version/architecture,
saves the session, then closes all its source browsers. A detached helper waits
for the app to exit before requesting Ubuntu administrator authorization through
`pkexec` and running `apt-get install` on the verified package.

The app reopens as your normal desktop user and restores that session automatically.
Previously active source browsers may still need Play or ordinary login/consent.
Runtime setup may take a few minutes after an update. If authorization is cancelled
or installation fails, the helper reopens the installed version and shows an error;
package-manager details are in `~/.local/state/game-stream/update.log`. Download
failures leave playback running. If browsers cannot close within three minutes,
installation is aborted without launching a duplicate app. A session-save failure
also prevents handoff.

Updates come only from this repository's `main/releases/latest.json` and its
versioned `.deb` via HTTPS; the published checksum detects incomplete or mismatched
downloads. Trust in updates comes from this GitHub repository and TLS; this is not
a separately signed apt repository. No password is stored or passed to a shell.
The download is staged privately and deleted after use. The updater supports the
Ubuntu amd64 package; source checkouts continue to use `git pull`.

#### GitHub download errors / private repositories

v0.2.2 hid all non-200 HTTP responses behind “could not be downloaded from
GitHub.” v0.2.3 reports the HTTP status and saves an actionable summary to
`~/.local/state/game-stream/update.log`. HTTP 404 can mean a missing file or a
private repository that the app cannot access; HTTP 401/403 can also indicate
missing authorization or a GitHub access limit. The old message alone cannot
identify which response your machine received.

On HTTP 401/403/404, the updater can use your existing GitHub CLI login. For a
private repository, install the current package manually once if needed, then run as your normal user:

```bash
sudo apt install gh
gh auth login
```

Sign in to GitHub.com with an account that can read `sbjonesy25/game-stream`, then
retry **Preferences → Updates → Save & check for updates…**. The app calls `gh api` directly, with prompts
disabled; GitHub CLI handles credentials. Tokens/passwords are never copied into
Game Stream config, logs or installer files. Each friend using private-repository
updates needs their own repository access. Public update files do not require
GitHub CLI or login. Missing files/denied accounts still require correcting
repository access or installing a shared `.deb` manually. The package recommends
GitHub CLI; it is optional. HTTP and authenticated downloads both keep the same
size/checksum/package-identity validation and cancellation behavior.

Every release rebuilds `latest.json` and verifies that it points to the matching
installer. Push it together with the `.deb` and checksums so installed apps can
discover subsequent updates.

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

1. Leave **Preferences → Playback → Show browser** enabled (the default), choose
   **1–4 panes** (or Alt+1–4), paste a page link, and press **Load** or Enter.
2. In the Chromium window, complete ordinary login/consent prompts and press
   **Play**. Wait for a few video segments to buffer (usually 10–20 seconds).
   With a supported Hls.js/Clappr MPEG-TS player, video starts in the app and the
   source browser minimizes automatically when the window manager permits it.
   **Keep that browser open**: it handles stream renewals and decoding the site's
   transport. The browser is muted; VLC owns the audible output. Stop or close
   Game Stream to close the associated browser, worker, and local relay.
3. Use **Audio on/off** and each pane's volume slider. **Preferences → Playback →
   Solo audio** mutes other panes when you enable one. Disable it to mix streams.
   Loading a new stream and the audio shortcuts always select exclusive audio.
4. **Expand** fills the wall with one pane and selects its audio while other loaded streams keep running.
   **F** or **F11** makes the window fullscreen; combine these for a single fullscreen
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
**Preferences → Playback → Show browser** to try playback without a visible window. Some sites
need a real player click, so visible browser mode is recommended first.

### Layout and Preferences

The Games menus, root-page settings and automatic catalog scraping were removed
in v0.2.3. Paste a source link or use Paste & Load to start a stream.

For three streams, select **3 panes** and press **F** or **F11**. By default,
fullscreen uses **Large stream + two**: one large pane on the left and two smaller
panes stacked on the right, using the whole canvas without an empty fourth cell.
Choose **Layout → Large stream → Quadrant 1 / 2 / 3**, or press **Shift+1 / 2 / 3**,
to select the large stream.
This rearranges running panes without restarting players, changing audio, or
recreating native video windows. **Layout → Grid** restores the original grid.
The Layout button appears when three panes are enabled and is available in the
fullscreen top controls. Windowed mode retains the normal grid. Video keeps its
aspect ratio, so individual panes can still have letterboxing.

**Preferences** has three tabs:

- **Playback**: Show browser, Solo audio, three-pane layout, large stream and
  double-tap interval and browser source quality. The source-quality cap defaults
  to **720p** for four-stream use; choose **1080p** or **Automatic** if desired. It
  applies to new browser bridge loads when multiple renditions exist; single-quality
  providers may exceed it. Direct HLS URLs are unchanged. Browser changes apply to subsequent loads. Enabling Solo
  audio immediately selects a single audible source.
- **Appearance**: **Default** (original colors), **Light** (near 0.9 gray), or
  **Dark** (near 0.1 gray/black), audio border, overlay visibility/duration/opacity and button
  text size. Overlays default to **85% opacity**; choose **25–100%**. Button text
  defaults to **12 px**, adjustable from **9–24 px** in windowed/fullscreen controls.
  Controls wrap to fit narrow panes at larger text sizes.
  Quadrant numbers have independent **10–64 px** text size (default **20 px**),
  a choice of **top left / top right / bottom left / bottom right**, and a visibility
  checkbox. They default to top left and remain visible when controls fade,
  including single-stream fullscreen and after reordering/zooming. Numbers always
  identify the original quadrant, rather than its current screen position.
  Labels paint only text, without copying video frames or starting new browser
  processes. Windowed labels reuse four input-transparent native badges and hide
  when the app is inactive, minimized or showing a modal/help popup. Fullscreen video geometry
  and 2-pixel gaps stay unchanged. Desktop compositing supplies the transparency.
- **Updates**: **Save & check for updates…** saves your edits, closes Preferences,
  checks the repository, then offers installation if a newer package exists.

The toolbar now holds Preferences, Layout (with three panes), pane count and
fullscreen. Show browser/Solo audio are in Playback; the App and cookie buttons
are removed. Layout/appearance choices persist between launches. Restoring a
session restores that session's Show browser/Solo audio choices; Preferences
reflects the actual active choices when opened.

Preferences live in `~/.config/game-stream/config.json` (or
`$XDG_CONFIG_HOME/game-stream/config.json`). Example:

```json
{
  "three_pane_layout": "focus",
  "three_pane_primary": 1,
  "solo_audio": true,
  "show_browser": true,
  "browser_max_height": 720,
  "theme": "default",
  "double_tap_ms": 350,
  "audio_border_enabled": true,
  "audio_border_color": "#00ff00",
  "overlay_enabled": true,
  "overlay_on_shortcuts": true,
  "overlay_timeout_ms": 2200,
  "overlay_opacity_percent": 85,
  "button_text_size": 12,
  "quadrant_labels_enabled": true,
  "quadrant_label_size": 20,
  "quadrant_label_corner": "top-left"
}
```

Use Preferences or edit the file while the app is closed, then relaunch. Existing
configurations gain the new defaults; obsolete root/catalog keys are ignored.
Saved source links, cookies-file references and session history are preserved.

### Audio border, loading and overlay preferences

The selected audio source has a **one-pixel green border** by default. Preferences
lets you disable it or choose another six-digit hex color (for example `#00ff00`).
Fullscreen paints the border above VLC without changing the video geometry or
2-pixel quadrant spacing. Windowed mode uses a one-pixel pane frame. This marks
the selected audio source, including while it buffers or is paused; manual mixed
audio highlights each selected source. Single-stream fullscreen suppresses the
border, both with 1 pane selected and when a quadrant is expanded. Returning to
a fullscreen grid restores the border around the selected audio source.

**Load** and **Paste & Load** give the target pane
exclusive audio, even when Solo audio is unchecked. Other panes are muted. Session
recovery preserves the saved audio/mute state rather than selecting a different
source for every restored link. Chromium source windows are always silent; VLC
owns sound output, and its Playing event reapplies the selected mute/volume state.

**Paste & Load**, next to Load, validates the clipboard as an HTTP/HTTPS link,
pastes it and starts loading. Invalid clipboard text leaves the current link and
stream alone. Replacing a loaded link closes that pane's associated browser before
starting its replacement. Newer requests supersede queued ones; Stop cancels
queued restarts too. Other Chromium windows/applications are never terminated.
Editing an input field alone does not stop playback; loading the replacement does.

Preferences also controls fullscreen overlays:

- `overlay_opacity_percent`: **25–100%**, default **85%**, including the buttons.
  Fading and subsequent mouse reveals retain the chosen opacity.
- `button_text_size`: **9–24 px**, default **12 px**; larger buttons wrap within
  each pane instead of overlapping adjacent videos.
- `overlay_timeout_ms`: idle duration, default **2200 ms**; **0** keeps revealed
  controls visible until leaving fullscreen or switching applications.
- `overlay_on_shortcuts`: disable automatic controls after audio/zoom/fullscreen
  actions, while retaining mouse-triggered controls.
- `overlay_enabled`: disable all fullscreen control overlays. Keyboard shortcuts,
  double-click zoom and the multi-pane audio border still work. Press F/F11/Esc to return to
  the normal window and change Preferences.

The config file accepts `audio_border_enabled`, `audio_border_color`,
`overlay_enabled`, `overlay_on_shortcuts`, and `overlay_timeout_ms`. Older config
files gain safe defaults automatically. Three-pane settings use
`three_pane_layout` (`focus` or `grid`) and `three_pane_primary` (1, 2 or 3).

### Keyboard and double-click controls

Shortcuts work while Game Stream has focus, including its floating controls.
Plain digit/F/G/H and Shift+digit shortcuts preserve text typed into URLs and other
editors (including !@#$). Alt+1–4 also works from a URL field. Dialogs/open menus
and other modified combinations keep their normal keyboard behavior. Keys for
hidden panes are ignored. Holding a key does not repeatedly toggle it.

| Key / action | Result |
| --- | --- |
| `Alt+1`, `Alt+2`, `Alt+3`, `Alt+4` | Show 1, 2, 3, or 4 panes; return from zoom to that grid. Hidden streams stop/mute; links remain. |
| `Shift+1`, `Shift+2`, `Shift+3` | Choose the large stream in three-pane mode, preserving audio. Works with shifted digit symbols/keypad too. Shift+4 has no effect. |
| `1`, `2`, `3`, `4` | Select only that quadrant's audio, even with Solo audio unchecked. |
| Double-tap `1`, `2`, `3`, `4` | Same zoom/audio action as `5`, `6`, `7`, `8`. Single taps still select audio immediately. |
| `0` | Mute all; press again to restore the last selected audio source. |
| `5`, `6`, `7`, `8` | Zoom quadrant 1, 2, 3, or 4 and select its audio. Enter fullscreen if needed. |
| Same zoom key again | Return to the selected pane grid, staying fullscreen and preserving audio/mute. |
| Double-click video while fullscreen | Zoom that quadrant and select its audio; double-click again to restore the grid without changing audio/mute. |
| `G` | Toggle Grid / Large stream + two with three panes selected; preserve audio. |
| `H` | Show / hide a shortcut reference window; H again or Esc closes it. |
| `F` or `F11` | Toggle window fullscreen. |
| `Esc` | Restore the grid and leave fullscreen. |

H opens a nonmodal shortcut reference. Playback keeps running while it is open.
It reuses one window; H again, Close or Esc dismisses it. Fullscreen control
overlays stay behind the reference while it is visible.

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
The buffer retains at most **8 segments / 48 MiB per pane** (192 MiB across four
relays). The displayed HLS window still uses the latest six segments.

This mode keeps both Chromium and VLC active, so four HD streams can use more
CPU/GPU memory than four direct HLS URLs. Minimize the source windows rather
than closing them. The app disables browser background throttling and keeps
source video muted to avoid duplicate audio.

### Browser sessions and legacy cookie imports

Cookie import/clear buttons were removed in v0.2.4. Use the source Chromium window
for normal playback/login/consent; Brave's profile is not shared automatically.
Existing cookie-file references restored from older sessions remain supported.
Cookies stay scoped to their paths/domains, and original files stay untouched.
Decline session restoration to start without an old import. Cookie files and
browser passwords are never bundled into installers.

For ordinary HLS, captured Referer, Origin, User-Agent and same-origin
Authorization headers accompany relay requests. Nested playlists, media, keys,
and initialization segments pass through that relay. Signed query strings are
preserved, cookies stay domain scoped, and Authorization does not cross origins.
Both relay types bind only to `127.0.0.1` on random ports with random resource
paths. Cookie values and signed upstream URLs are not written to app logs.

### Memory and four-stream use

v0.2.4 bounds the HTTP relay's formerly unlimited URL lookup table to 2,048
entries and four million URL characters (each URI is limited to 16,384 characters).
Recent URLs are retained, and the original VLC master URL stays valid. Stop clears
the maps and fragment caches. No network writes occur while holding the browser
fragment-cache lock, avoiding a slow client blocking incoming fragments/cleanup.

For supported Hls.js players, the capture hook applies a **10-second back buffer**,
**12-second forward target / 24-second maximum target**, and **16 MiB forward-buffer
size target**. These player settings are not a hard Chromium RAM limit. Complete
TS segments still have independent bounded relay storage. Only two fragment
copies per frame may be in flight; rejected players stop forwarding. Initial
capture hooks install before automatic Play, and discovery response subscriptions
are removed after handoff. Connected-player scanning and resolver polling slow
down once video is established. Each Playwright driver's Node old-space budget
defaults to 512 MiB to encourage earlier garbage collection; explicit user
`NODE_OPTIONS` are preserved. No browser renderer or VLC process is forcibly killed
for crossing a memory limit.

Four browser-assisted streams still require four source browsers plus VLC's
players. Keep **Show browser / Solo audio on** and **720p source quality** as a
starting point on a 16 GB host. Hiding a browser does not eliminate its decoding
work. Higher qualities, source-page ads/scripts, native GPU buffers and other
applications can add substantial memory; these fixes do not promise a 16 GB
system-wide cap. Browser/GPU/native VLC behavior must be checked on the host.

#### What to send for a continuing memory problem

Start with **one file: `game-stream-memory.json`**, generated by the current
repository's monitor. Schema 2 includes per-process PSS/RSS/private/swap memory,
CPU time, thread/file-descriptor counts, process IDs/parents/start times, role
groupings (app/VLC, Chromium, automation, other), system available RAM/file cache,
swap totals/I/O and memory pressure. This separates app/VLC growth from browser
or driver growth and from unrelated system memory pressure.

Metadata includes the installed Debian package version, kernel/architecture,
CPU/page size, X11/Wayland session type and validated on-disk Preferences. Missing
config fields use defaults; restored runtime settings can differ. Unknown legacy
fields are removed, including root-page URLs. No command-line arguments, raw
process names, environment dump, file paths, stream URLs, cookie values, passwords,
page HTML, HAR, raw session file, memory dump or browser databases are captured.
The JSON is owner-only (0600). Unreadable counters are marked; totals may then be
incomplete. The report stops if the app exits or its process ID is reused.

Run as your normal user, from the repository folder. Launch Game Stream first,
preferably with no streams running (decline session restore if needed), then:

```bash
git pull
GAME_STREAM_PID=$(pgrep -n -f '[g]ame_stream.app')
python3 scripts/monitor-resources.py --pid "$GAME_STREAM_PID" --seconds 3600 --interval 15 --output game-stream-memory.json
```

Let it capture 2–3 minutes idle, then load the usual streams. Leave it collecting
through the memory rise/unresponsiveness. If possible, press Stop on all panes,
keep the app open for another five minutes, then Ctrl+C in the monitor terminal.
Samples already saved remain intact even if you stop early. The monitor is a
separate process, so it does not depend on the GUI event loop remaining responsive.
Use the version that exhibits the growth; upgrading the monitor does not require
restarting an already-running installed app. For the new number labels, install
the current `.deb` and relaunch first.

Attach the generated JSON here and mention the approximate times you loaded the
streams, noticed the rise/stall, and stopped them. Include how many streams ran
and whether duplicate playback was also running in Brave. Separate cookie files,
`session.json`, HARs, heap/core dumps and full command-line/process listings are
not needed for this first pass. A validated settings copy is already in the report;
you do not need to send your raw configuration file either.

A **604-second synthetic run** with the resource limits forwarded 601 fragments
per source with all four FFmpeg readers still running. After a 300-second warm-up,
process-tree PSS ranged **2,673.8–2,735.8 MiB**; combined relay media stayed at
**18,732,320 bytes**. Cleanup returned the test tree from 49 processes to 1.
The cloud has no configured swap, so this does not validate actual swap pressure.
Raw measurements, test conditions and limitations are in
[`validation/resource-soak-0.2.4.json`](validation/resource-soak-0.2.4.json).

A reproducible synthetic soak runs four real Chromium/Hls.js 720p15 players and
four FFmpeg readers. It requires FFmpeg, Playwright Chromium/system Chromium and a
local Hls.js bundle (development used Hls.js 1.6.16). Run separately from the app:

```bash
.venv/bin/python scripts/soak-browser-resources.py --hls-js /path/to/hls.min.js --seconds 600 --output browser-resource-soak.json
```

The fixture generates its own video/audio and serves only loopback traffic. Its
Hls.js worker/progressive path is disabled so complete clear TS events exercise
the bridge, matching the supported custom transport. It does not reproduce the
live site's ads, token renewal, GPU behavior, or native VLC. A synthetic result
therefore does not establish a fix for every cause of an actual 30-minute host
stall. The app's raw input transport and supported-provider limitations still apply.

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

### Remove the current source setup before testing the installer

Close Game Stream, then from this checkout run:

```bash
git pull
./scripts/uninstall-current-setup.sh --dry-run
./scripts/uninstall-current-setup.sh
sudo apt install ./releases/game-stream_0.2.5_amd64.deb
```

This removes old launcher/icon registration and only an editable console package
that belongs to this checkout, avoiding PATH conflicts with `/usr/bin/game-stream`.
The checkout, virtual environment/dependencies, original cookie files and saved
settings remain. Add `--purge-user-data` only if you want a fresh session and
preferences too. Custom/symlinked environments and unrelated package installs
are left alone. Launch the installed app from Applications afterward.

The installer already provides **`game-stream-uninstall`**. A separate
**[`game-stream-uninstaller_0.2.5.tar.gz`](releases/game-stream-uninstaller_0.2.5.tar.gz)**
bundle and **[`uninstall-game-stream.sh`](releases/uninstall-game-stream.sh)**
are also included in `releases/`; they work without a checkout or Qt/Python app
runtime. Run `bash uninstall-game-stream.sh` as your normal desktop user. The
same `--dry-run` and `--purge-user-data` options apply.

### macOS

The architecture can be ported to macOS, but this release is Linux-only. A Mac
version needs the Cocoa Qt backend, VLC NSView embedding instead of `set_xwindow`,
Mac-specific fullscreen/input handling, and a separate `.app`/`.dmg` build and
test pass for Intel/Apple Silicon. The `.deb` does not install on a Mac.

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

Current development validation: **80 tests passed on Python 3.12 and 3.14** with
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
timing and audio/zoom parity, safe uninstall ownership checks, package contents, and
runtime staging/failed-update rollback. New regression checks cover exclusive
load audio, native Playing mute reapplication, one-pixel border rendering/colors,
clipboard validation, overlay options, serialized browser replacement, stale
callbacks, cancelled queued restarts, and source-environment cleanup. Layout tests
cover F/F11 and URL editing, three-pane canvas coverage, all large-stream choices,
unchanged native video handles/playback/audio, grid/zoom round trips, legacy config
migration and fullscreen single-stream border suppression. Added tests cover
Alt pane counts, Shift large-stream choices with real shifted symbols, text editing,
preferences playback flags/session consistency, update handoff after closing the
settings modal, opacity across fade/reveal, font changes and wrapped button bounds.
Resource/theme tests cover G/H without intercepting URLs, reusable help/Esc behavior,
palette switching and persistence, four bounded caches after 20,000 fragment pushes,
10,000 playlist rewrites, URL-byte budgets, Hls.js buffering/quality configuration,
other-player rejection and credential-free process-tree memory reports. New checks
cover every label corner and size limit, rendering with hidden controls, original
quadrant IDs during zoom, reused badge windows, video geometry/handle preservation,
label persistence/disable behavior, per-process diagnostic grouping, pressure/swap
counters, sensitive-data canaries and PID reuse. Update tests
check real Debian package identity, checksums/size limits, cancellation, source-install
rejection, shutdown-before-install ordering, authorization cancellation, restart as
the desktop user, session saving and staged-package tampering. A local fake GitHub
CLI exercises private-repository feed/package downloads, missing login, bounded
output, cancellation, and credential-free private diagnostics. The helper's
privileged apt command and restart are simulated; actual Ubuntu authorization and
upgrade/relaunch still need a desktop host test. All imported Qt modules are supplied by
PySide6-Essentials; unused Qt WebEngine/Addons are not required by fresh installs.
Browser install dry runs verify download selection for Ubuntu 22.04,
24.04 and 26.04 x64 without downloading browsers or changing system packages.
This does not constitute a full install on each Ubuntu version. Qt control tests
use a mock VLC instance: native VLC video
embedding, real audio output, and GPU playback still need the Ubuntu host smoke
test below. This cloud machine lacks libVLC and cannot install system packages.

Host smoke test:

- Change quadrant number corner/size/visibility, including one enlarged stream,
  three-pane rearrangements, hidden controls and inactive/minimized windows.
  Confirm labels stay above native VLC without intercepting clicks.

- Switch Default / Light / Dark themes, toggle G in a three-pane view, and use H
  twice (plus Esc) while streams play. Confirm the help popup stays above video.
- Run four streams for at least 30 minutes on the 16 GB host and capture the
  memory report. Check warm-up, steady state, swap and UI/audio responsiveness.

- Test Alt+1–4 pane counts, Shift+1–3 large stream selection, and typing !@#$ in
  URLs. Use Preferences to change Show browser/Solo audio, opacity and text size;
  confirm settings persist and overlays are translucent above native VLC video.

- With the installed package, use Preferences → Updates to check/install updates. Once a newer release
  is published, verify download, admin authorization, browser closure, installation,
  normal-user relaunch and session restoration. Test cancellation before download
  completion and at the authorization prompt.
- Select 3 panes, enter fullscreen with F, and choose each Layout → Large stream
  option. Check all streams stay visible, video/audio continue and there is no
  empty grid cell. Zoom a stream and restore; verify the audio border disappears
  only for the single-stream fullscreen view. Try Layout → Grid and relaunch.

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
- Type a URL containing digits and F: verify shortcuts do not interrupt editing.
- Stop/reload a pane during playback and discovery; verify its browser exits.
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
