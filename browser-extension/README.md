# OmaJDownLoad Click'n'Load browser companion

This optional Manifest V3 extension forwards Click'n'Load form, Fetch, and
XMLHttpRequest submissions to the loopback-only OmaJDownLoad listener. It does
not know or store MyJDownloader credentials and cannot bypass OmaJDownLoad's
review inbox.

Because Click'n'Load buttons can appear on arbitrary download pages, the two
small relay scripts run on visited HTTP(S) pages. The service worker itself can
only contact `127.0.0.1:9666` or `localhost:9666`, and only the two Click'n'Load
POST paths are accepted. No page content or browsing history is stored.

The companion also bridges the standard `/flash/` and `/jdcheck.js`
availability probes, including programmatic and parser-created script tags.
It authenticates the source-tab metadata to the local
helper with an extension-only, short-lived loopback handshake so the panel can
distinguish verified browser provenance from the untrusted CNL `source` field.

Dynamic CNL2 `jk` programs are deliberately not evaluated. A CNL2 key must be
a literal 32-character hexadecimal return value; this keeps website code out
of the privileged extension and Python helper contexts.

Intercepted Fetch and XMLHttpRequest calls preserve abort and timeout behavior.
Intercepted forms include the clicked submit button and emit
`omajdownload:success` or `omajdownload:error` on the form; a named iframe target
also receives the listener response through `srcdoc`.

## Build packages

```bash
./scripts/build-extension.sh
```

This creates separate Chromium and Firefox ZIP files in `dist/`.

## Chromium development install

Open `chrome://extensions`, enable developer mode, choose **Load unpacked**,
and select the `browser-extension` directory. For publication, upload the
Chromium ZIP created by the build script.

## Firefox development install

Build the packages, extract the Firefox ZIP, then load its `manifest.json`
temporarily from `about:debugging`.

The only host permissions are `127.0.0.1:9666` and `localhost:9666`. A visible
page alert is shown if the local listener rejects or cannot receive a request.
The release smoke-test matrix is documented in [`TESTING.md`](TESTING.md).
