<p align="center">
  <img src="assets/omajdownload-mark.svg" width="112" height="112" alt="OmaJDownLoad logo">
</p>

<h1 align="center">omarchy-OmaJDownLoad</h1>

<p align="center">
  A native Omarchy Quattro bar controller for remote JDownloader instances.
</p>

![OmaJDownLoad preview](preview.png)

OmaJDownLoad connects to devices registered with a
[MyJDownloader](https://my.jdownloader.org/) account. JDownloader itself can
run on a server, NAS, or in Docker; nothing has to be installed on the Omarchy
machine.

## What it does

- Discovers all online JDownloader devices in the account
- Switches between devices and remembers the selection
- Starts, pauses, resumes, stops, and force-starts downloads
- Shows package progress, transfer speed, and current controller state
- Adds one or more URLs to LinkGrabber or directly to the download queue
- Receives local Click'n'Load and encrypted CNL2 requests on `127.0.0.1:9666`
- Keeps incoming Click'n'Load requests in a review inbox for the selected device
- Moves or removes LinkGrabber packages
- Removes download-list entries without deleting downloaded files
- Uses a theme-aware vector mark with animated, paused, and offline states
- Stores the MyJDownloader password in Secret Service, never in `shell.json`

## Requirements

- Omarchy 4 / Quattro
- Python 3 with `venv`
- `secret-tool` from `libsecret`
- A MyJDownloader account with at least one connected JDownloader device

## Install from GitHub

```bash
omarchy plugin add https://github.com/flathack/omarchy-OmaJdownLoad.git --enable
```

Click the OmaJDownLoad icon in the bar, choose **Install helper**, then connect
your MyJDownloader account. The helper is installed into an isolated virtual
environment in the user profile; no system package is modified.

You can also install the helper from a terminal:

```bash
~/.config/omarchy/plugins/io.github.flathack.omajdownload/install.sh
```

## Controls

| Input | Action |
|---|---|
| Left click | Open or close the control panel |
| Middle click | Refresh status |
| Right click | Pause or resume all downloads |

The panel offers explicit controls for start, pause/resume, stop, force-start,
LinkGrabber submission, queue movement, and removal. Removing a download entry
keeps the downloaded file on disk.

## Click'n'Load

While the plugin helper is running, OmaJDownLoad provides the standard local
Click'n'Load endpoints at `http://127.0.0.1:9666/flash/`, following the
[JDownloader CNL2 protocol](https://jdownloader.org/knowledge/wiki/glossary/cnl2). Both plain CNL and
encrypted CNL2 requests are supported. Incoming links are not started silently:
they appear in the panel first, where they can be sent to LinkGrabber, added and
started, or dismissed. The current MyJDownloader device selection is used.

The listener is bound to loopback only, limits request size, and never executes
JavaScript supplied by a website. CNL2 pages that generate their AES key through
dynamic JavaScript need the optional browser companion planned for a later release.

## Security and local data

OmaJDownLoad runs inside the unsandboxed Omarchy shell, like every Omarchy
plugin. Review the source before enabling it.

- Password: desktop Secret Service keyring
- Non-secret settings: `~/.config/omarchy/omajdownload/config.json`
- Pending Click'n'Load inbox: `~/.config/omarchy/omajdownload/clicknload-inbox.json` (mode `0600`)
- Python environment: `~/.local/share/omajdownload/venv`
- API transport: encrypted MyJDownloader API over HTTPS

The password is passed from QML to the long-lived helper over stdin. It never
appears in process arguments, plugin settings, logs, or `shell.json`.

Dependency versions are pinned in `requirements.txt`; their licenses are
listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Remove

First disconnect the account from the bottom of the OmaJDownLoad panel. This
removes its keyring entry and non-secret configuration. Then remove the plugin:

```bash
omarchy plugin remove io.github.flathack.omajdownload
```

The isolated helper environment may be removed separately from
`~/.local/share/omajdownload` if it is no longer needed.

## Development

```bash
./scripts/check.sh
omarchy plugin validate .
```

For a live local checkout, copy the repository to
`~/.config/omarchy/plugins/io.github.flathack.omajdownload`, enable it, and edit there.
Omarchy hot-reloads plugin changes.

## License

[MIT](LICENSE)
See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for runtime dependencies.
