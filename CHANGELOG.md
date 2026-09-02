# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

- Preserve outer absolute deadlines when nested deadlines are longer, and
  restore any outer's already-elapsed deadline on exit

## [0.6.1] - 2026-09-02

- Bound the loopback bridge, browser companion, helper IPC, remote response
  models, and subprocess output with explicit concurrency, byte, and time limits
- Make helper state and installer publication descriptor-safe, no-follow, private,
  process-tree aware, and crash-consistent during legacy migration
- Render all QML `Text` values as plain text and add helper/installer watchdogs
- Pin CI actions, containers, package snapshots, Omarchy source, and browser-test
  Python dependencies to immutable inputs

## [0.6.0] - 2026-08-31

- Add a persistent, keyboard-accessible MyJDownloader connection switch to the
  panel header while keeping the local Click'n'Load inbox available offline;
  the OFF view hides all unrelated remote-control sections

## [0.5.1] - 2026-08-31

- Keep the browser companion manifest source distinct from the root Omarchy
  plugin manifest so marketplace discovery sees exactly one plugin

## [0.5.0] - 2026-08-31

- Rename the application and plugin identity to OmaJD-Remote while retaining
  compatibility with existing local configuration and Click'n'Load clients
- Pulse the bar icon for pending Click'n'Load reviews and show a persistent
  accent state with link counts while LinkGrabber contains links
- Restore prior keyring credentials after every uncommitted configuration failure
- Bound Click'n'Load error notifications and claimed-source metadata
- Show empty-cache refresh errors and invalidate stale listener state
- Make full-URL reveal exclusive to one Click'n'Load request at a time
- Require confirmation before dismissing a Click'n'Load request
- Preserve LinkGrabber rename drafts until the remote result is known
- Support cache-busted Click'n'Load availability probes consistently
- Isolate browser request cancellation by tab, frame, and document
- Preserve XMLHttpRequest timeout semantics beyond the bridge watchdog
- Add Firefox data-collection declarations and browser-store privacy documentation
- Strengthen tagged-release validation so Unreleased changes cannot be packaged

## [0.4.0] - 2026-08-30

- Make private JSON commits mode-safe, fsync-backed, and crash-consistent
- Preserve existing keyring credentials across failed same-account replacement
- Require explicit duplicate-risk retries for ambiguous manual Add Links calls
- Bound Click'n'Load aggregate memory and load full URL previews only on demand
- Decouple controller polling and package sections, with visible truncation/errors
- Preserve LinkGrabber rename focus across model refreshes and back off helper crash loops
- Complete form submitter, completion, cancellation, XHR timeout, and script-tag browser bridging
- Add real Chromium/Firefox and semantic Omarchy QML CI jobs
- Make helper switching failure-safe and browser ZIPs deterministic and concurrency-safe
- Align plugin/browser version 0.4.0 and add a verified tag publication workflow
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
- Animated theme-aware OmaJD-Remote mark

[Unreleased]: https://github.com/flathack/omarchy-OmaJD-Remote/compare/v0.6.1...HEAD
[0.6.1]: https://github.com/flathack/omarchy-OmaJD-Remote/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/flathack/omarchy-OmaJD-Remote/compare/v0.5.1...v0.6.0
[0.5.1]: https://github.com/flathack/omarchy-OmaJD-Remote/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/flathack/omarchy-OmaJD-Remote/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/flathack/omarchy-OmaJD-Remote/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/flathack/omarchy-OmaJD-Remote/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/flathack/omarchy-OmaJD-Remote/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/flathack/omarchy-OmaJD-Remote/releases/tag/v0.1.0
