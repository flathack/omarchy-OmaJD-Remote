# Privacy policy

Effective date: 2026-08-31

OmaJD-Remote is a local Omarchy plugin and optional browser companion for
forwarding Click'n'Load requests to a JDownloader device selected through the
user's MyJDownloader account. The project developer does not operate a data
collection service and does not receive telemetry from the plugin or browser
companion.

## Data handled by the browser companion

The companion runs on HTTP and HTTPS pages so it can recognize standard
Click'n'Load forms, Fetch requests, XMLHttpRequest calls, and availability
probes. Only when a page makes a request to the standard local Click'n'Load
address does the companion handle:

- the source page URL, used to show the user where the request originated;
- the submitted Click'n'Load form fields, including download URLs, optional
  passwords, a claimed source label, and encrypted CNL2 data; and
- technical request state needed to report success, failure, cancellation, or
  timeout to the page.

The extension sends this information only to the OmaJD-Remote helper bound to
`127.0.0.1:9666` or `localhost:9666` on the same computer. It cannot contact
arbitrary hosts, does not receive MyJDownloader credentials, and does not send
handled data to the project developer or advertising or analytics services.

## Local storage and retention

Accepted requests are stored in the private local Click'n'Load inbox at
`~/.config/omarchy/omajdownload/clicknload-inbox.json` until the user submits
or dismisses them. The file is written with mode `0600`. Collapsed UI state
contains only bounded source and destination labels; a bounded list of full
download URLs is revealed only after the user activates the preview control.

The browser extension itself does not persist browsing history, page content,
Click'n'Load form data, identifiers, analytics, or credentials.

## MyJDownloader account data

The desktop plugin stores the user's MyJDownloader password in the desktop
Secret Service keyring. Non-secret account and device selection settings are
stored locally. The browser companion cannot access either location. Remote
JDownloader actions use MyJDownloader's encrypted API transport.

## User control and deletion

The user approves, retries, or dismisses each incoming request in the Omarchy
panel. Submitting or dismissing removes it from the local inbox. Disconnecting
the account removes its keyring credential and plugin configuration. Removing
the local inbox file deletes any remaining pending requests.

## Sharing and sale

The project developer does not collect, sell, rent, or share user data. Data is
sent to MyJDownloader only when the user directs the desktop plugin to perform
the corresponding JDownloader action, subject to MyJDownloader's own terms and
privacy practices.

## Changes and contact

Material policy changes will be documented in this repository. Questions or
privacy requests can be opened as a GitHub issue that contains no credentials,
tokens, device identifiers, or download URLs. Security-sensitive reports must
use GitHub private vulnerability reporting as described in `SECURITY.md`.
