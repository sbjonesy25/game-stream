# Game Stream development

- Use the existing checkout; do not create worktrees unless requested.
- Every application change must ship in the Ubuntu installer, not only in source.
  Bump the patch version in pyproject.toml for updates. Run scripts/release.sh,
  and stage the latest .deb, standalone uninstall .sh, uninstall .tar.gz bundle,
  releases/SHA256SUMS, releases/latest.json update feed, and corresponding
  documentation with the source changes.
- scripts/check-release.py must pass before pushing. It detects stale source,
  icons, bootstrap/uninstall helpers and README content in the release artifacts.
- The user wants the source and rebuilt release artifacts pushed to the repository.
- Preserve settings/session history during uninstall unless --purge-user-data is
  explicit. Never delete the source checkout, source .venv, unrelated Chromium
  processes, or unmarked runtime directories.
- macOS is not yet supported or tested: native VLC embedding and Linux-specific
  fullscreen, desktop integration and packaging require a separate port.
