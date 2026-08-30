# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

- Move compact playback controls to the top of the configured panel
- Add inline LinkGrabber package renaming with keyboard save and cancel
- Reduce the Add links editor to two visible lines with overflow scrolling
- Prevent duplicate Click'n'Load submissions after uncertain remote outcomes and require an explicit retry
- Show verified browser origin and destination hosts separately from untrusted website labels
- Add authenticated browser-companion provenance and proactive JDownloader availability probes
- Preserve `fetch(Request)` bodies and emulate form and XHR Click'n'Load transports more completely
- Reset destructive confirmations on timeout, selection changes, refreshes, and panel close
- Make account replacement and removal transactional across configuration and Secret Service
- Quarantine malformed local state instead of silently overwriting it
- Verify exact locked helper versions on every launch and prune superseded environments safely
- Build browser-extension ZIPs from clean allowlisted contents
- Require actual QML parsing and executable browser transport tests in CI
- Document that dynamic JavaScript CNL2 keys are intentionally unsupported and never evaluated

## [0.3.0] - 2026-08-30

- Add complete panel keyboard navigation, accessible names, and visible input labels
- Preserve pasted links until MyJDownloader confirms their submission
- Paginate download and LinkGrabber lists beyond 60 packages
- Keep configured accounts usable while every device is offline and refresh devices live
- Make Click'n'Load admission durable before acknowledging a website
- Add a bounded persistent inbox and explicit listener failure responses
- Stop polling the desktop keyring during routine status refreshes
- Detect and repair stale helper environments with atomic, hash-locked installs
- Add an optional Chromium and Firefox Click'n'Load browser companion
- Expand automated coverage to bridge, HTTP listener, package paging, and extension security flows
- Render failed actions as errors immediately

## [0.2.0] - 2026-08-30

- Add a loopback-only Click'n'Load/CNL2 listener on port 9666
- Add a review inbox with LinkGrabber, start, and dismiss actions
- Persist pending requests privately and reject dynamic website JavaScript

## [0.1.0] - 2026-08-30

### Added

- Native Omarchy bar widget and control panel
- MyJDownloader account onboarding through Secret Service
- Automatic discovery and selection of multiple JDownloader devices
- Download start, pause, resume, stop, and force-start actions
- Download package progress and speed display
- LinkGrabber URL submission, queue movement, and removal
- One-click isolated helper installation
- Animated theme-aware OmaJDownLoad mark

[Unreleased]: https://github.com/flathack/omarchy-OmaJdownLoad/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/flathack/omarchy-OmaJdownLoad/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/flathack/omarchy-OmaJdownLoad/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/flathack/omarchy-OmaJdownLoad/releases/tag/v0.1.0
