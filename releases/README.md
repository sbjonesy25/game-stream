# Ubuntu installer

Download `game-stream_0.2.0_amd64.deb` and `SHA256SUMS` from this directory.

```bash
sha256sum -c SHA256SUMS
sudo apt install ./game-stream_0.2.0_amd64.deb
```

Open **Applications → Game Stream**. First launch downloads its private Python
and Chromium dependencies, so internet access and about 2 GB free space are
required. Right-click its running dock icon to pin it. This package targets
Ubuntu 22.04/24.04/26.04 **amd64 (x86-64)**, not Android or ARM.

Run `game-stream-uninstall` as your normal desktop user to remove the package,
your launcher/icons and the app-owned runtime. Preferences and saved sessions
stay by default; add `--purge-user-data` to remove this user's data too.

The package is built from the source in this repository using
`./scripts/build-deb.sh`. It contains the app, wheel, launcher and icons. System
libraries come from apt; Python/browser setup runs as the desktop user. See the
root README for source installation, preferences and validation limits.
